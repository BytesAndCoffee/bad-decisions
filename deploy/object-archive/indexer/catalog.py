"""Read-only, dynamically generated public CardDeck catalog."""

from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

import boto3
from botocore.config import Config

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
REQUIRED_MEMBERS = frozenset({"ATTRIBUTION.md", "LICENSE.txt", "manifest.json", "pack.json"})


def setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


def read_credentials(path: Path) -> tuple[str, str]:
    fields = dict(line.split(":", 1) for line in path.read_text(encoding="utf-8").splitlines() if ":" in line)
    try:
        return fields["Key ID"].strip(), fields["Secret key"].strip()
    except KeyError as exc:
        raise RuntimeError("catalog credential file is malformed") from exc


class Catalog:
    def __init__(self) -> None:
        access_key, secret_key = read_credentials(Path(setting("CATALOG_CREDENTIALS_FILE")))
        self.buckets = tuple(value.strip() for value in setting("CATALOG_BUCKETS").split(",") if value.strip())
        self.object_domain = setting("CATALOG_PUBLIC_OBJECT_DOMAIN")
        self.client = boto3.client("s3", endpoint_url=setting("CATALOG_ENDPOINT_URL"), region_name=setting("CATALOG_REGION"), aws_access_key_id=access_key, aws_secret_access_key=secret_key, config=Config(s3={"addressing_style": "path"}))

    def pack(self, bucket: str, key: str, listed: dict[str, object]) -> dict[str, object]:
        if int(listed["Size"]) > MAX_ARCHIVE_BYTES:
            raise ValueError(f"archive exceeds {MAX_ARCHIVE_BYTES} byte catalog limit")
        body = self.client.get_object(Bucket=bucket, Key=key)["Body"].read(MAX_ARCHIVE_BYTES + 1)
        if len(body) > MAX_ARCHIVE_BYTES:
            raise ValueError(f"archive exceeds {MAX_ARCHIVE_BYTES} byte catalog limit")
        with ZipFile(io.BytesIO(body)) as archive:
            if {entry.filename for entry in archive.infolist()} != REQUIRED_MEMBERS:
                raise ValueError("unexpected CardDeck archive members")
            manifest = json.loads(archive.read("manifest.json"))
            pack = json.loads(archive.read("pack.json"))
        return {"bucket": bucket, "object_key": key, "url": f"https://{bucket}.{self.object_domain}/{quote(key)}", "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body), "last_modified": listed["LastModified"].isoformat(), "etag": str(listed["ETag"]).strip('"'), "archive": manifest, "metadata": pack["metadata"], "black_card_count": len(pack.get("black", [])), "white_card_count": len(pack.get("white", []))}

    def payload(self) -> dict[str, object]:
        packs, rejected = [], []
        for bucket in self.buckets:
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if key.endswith(".carddeck"):
                        try:
                            packs.append(self.pack(bucket, key, item))
                        except (BadZipFile, KeyError, UnicodeDecodeError, ValueError) as exc:
                            rejected.append({"bucket": bucket, "object_key": key, "error": str(exc)})
        packs.sort(key=lambda pack: (str(pack["bucket"]), str(pack["object_key"])))
        return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "buckets": list(self.buckets), "pack_count": len(packs), "packs": packs, "rejected_archives": rejected}


CATALOG = Catalog()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/packs/index":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            response = json.dumps(CATALOG.payload(), separators=(",", ":"), ensure_ascii=False).encode()
        except Exception:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "public, max-age=60")
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer((setting("CATALOG_BIND_HOST"), int(setting("CATALOG_BIND_PORT"))), Handler).serve_forever()
