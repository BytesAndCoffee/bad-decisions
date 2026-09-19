"""Read-only, dynamically generated public CardDeck catalog."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import threading
import time
import zlib
from collections.abc import Callable
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

# These limits mirror src/bad_decisions/archive.py. The Dockerfile copies only
# this file, so they are duplicated; tests/test_catalog_indexer.py guards drift.
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 5 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
REQUIRED_MEMBERS = frozenset({"ATTRIBUTION.md", "LICENSE.txt", "manifest.json", "pack.json"})
DEFAULT_CACHE_TTL = 60.0
STALE_RETRY_SECONDS = 10.0
# One malformed object must be reported in rejected_archives, never fail the catalog.
ARCHIVE_ERRORS = (BadZipFile, KeyError, UnicodeDecodeError, ValueError, zlib.error, RuntimeError, TypeError, OSError)


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


def storage_errors() -> tuple[type[BaseException], ...]:
    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return ()
    return (BotoCoreError, ClientError)


def read_members(body: bytes) -> tuple[object, object]:
    """Validate the fixed CardDeck layout with the same limits as the client."""

    with ZipFile(io.BytesIO(body)) as archive:
        infos = archive.infolist()
        if len(infos) != len(REQUIRED_MEMBERS) or {info.filename for info in infos} != REQUIRED_MEMBERS:
            raise ValueError("unexpected CardDeck archive members")
        total = 0
        for info in infos:
            if Path(info.filename).name != info.filename or info.is_dir():
                raise ValueError(f"unsafe archive member: {info.filename!r}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"symbolic links are not allowed: {info.filename!r}")
            if info.flag_bits & 0x1:
                raise ValueError(f"encrypted archive member: {info.filename!r}")
            if info.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"archive member is too large: {info.filename!r}")
            if info.file_size and not info.compress_size:
                raise ValueError(f"invalid compressed member: {info.filename!r}")
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise ValueError(f"archive member compression ratio is too high: {info.filename!r}")
            total += info.file_size
        if total > MAX_ARCHIVE_BYTES:
            raise ValueError("archive is too large")
        members: dict[str, bytes] = {}
        for name in ("manifest.json", "pack.json"):
            # Header sizes can be forged, so bound the actual decompressed read.
            with archive.open(name) as member:
                data = member.read(MAX_MEMBER_BYTES + 1)
            if len(data) > MAX_MEMBER_BYTES:
                raise ValueError(f"archive member is too large: {name!r}")
            members[name] = data
    return json.loads(members["manifest.json"]), json.loads(members["pack.json"])


class PayloadCache:
    """TTL cache of the encoded catalog; refreshes are serialized and coalesced."""

    def __init__(self, build: Callable[[], dict[str, object]], ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._build = build
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._encoded: bytes | None = None
        self._expires_at = 0.0

    def get(self) -> bytes:
        with self._lock:
            now = self._clock()
            if self._encoded is not None and now < self._expires_at:
                return self._encoded
            try:
                encoded = json.dumps(self._build(), separators=(",", ":"), ensure_ascii=False).encode()
            except Exception:
                if self._encoded is None:
                    raise
                # Serve the stale catalog, but do not hammer storage while it is down.
                self._expires_at = now + min(self._ttl, STALE_RETRY_SECONDS)
                return self._encoded
            self._encoded = encoded
            self._expires_at = now + self._ttl
            return encoded


class Catalog:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

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
        manifest, pack = read_members(body)
        if not isinstance(manifest, dict) or not isinstance(pack, dict) or not isinstance(pack.get("metadata"), dict):
            raise ValueError("manifest.json, pack.json, and pack metadata must be JSON objects")
        black, white = pack.get("black", []), pack.get("white", [])
        if not isinstance(black, list) or not isinstance(white, list):
            raise ValueError("pack card lists must be JSON arrays")
        return {"bucket": bucket, "object_key": key, "url": f"https://{bucket}.{self.object_domain}/{quote(key)}", "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body), "last_modified": listed["LastModified"].isoformat(), "etag": str(listed["ETag"]).strip('"'), "archive": manifest, "metadata": pack["metadata"], "black_card_count": len(black), "white_card_count": len(white)}

    def payload(self) -> dict[str, object]:
        packs, rejected = [], []
        recoverable = ARCHIVE_ERRORS + storage_errors()
        for bucket in self.buckets:
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if key.endswith(".carddeck"):
                        try:
                            packs.append(self.pack(bucket, key, item))
                        except recoverable as exc:
                            rejected.append({"bucket": bucket, "object_key": key, "error": str(exc) or type(exc).__name__})
        packs.sort(key=lambda pack: (str(pack["bucket"]), str(pack["object_key"])))
        return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "buckets": list(self.buckets), "pack_count": len(packs), "packs": packs, "rejected_archives": rejected}


_cache: PayloadCache | None = None
_cache_guard = threading.Lock()


def encoded_catalog() -> bytes:
    """Build the catalog lazily so importing this module needs neither boto3 nor credentials."""

    global _cache
    with _cache_guard:
        if _cache is None:
            _cache = PayloadCache(Catalog().payload, float(os.environ.get("CATALOG_CACHE_TTL", DEFAULT_CACHE_TTL)))
        cache = _cache
    return cache.get()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/packs/index":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            response = encoded_catalog()
        except Exception:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", f"public, max-age={int(float(os.environ.get('CATALOG_CACHE_TTL', DEFAULT_CACHE_TTL)))}")
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer((setting("CATALOG_BIND_HOST"), int(setting("CATALOG_BIND_PORT"))), Handler).serve_forever()
