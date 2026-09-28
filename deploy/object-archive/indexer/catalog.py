"""Read-only, dynamically generated public CardDeck catalog."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import stat
import threading
import time
import zlib
from collections.abc import Callable
from contextlib import closing
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
# 2: per-pack counts are prompt_count and answer_count (1 said black/white_card_count).
CATALOG_SCHEMA_VERSION = 2
MAX_COMPRESSION_RATIO = 100
REQUIRED_MEMBERS = frozenset({"ATTRIBUTION.md", "LICENSE.txt", "manifest.json", "pack.json"})
MANIFEST_MEMBERS = frozenset({"format", "format_version", "pack_id", "pack_sha256"})
PACK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
PRINTED_METADATA_STRINGS = ("name", "description", "version", "language", "attribution", "license_id", "license_notice")
PRINTED_METADATA_LISTS = ("authors", "modifications")
DEFAULT_CACHE_TTL = 60.0
STALE_RETRY_SECONDS = 10.0
# Content-invalid objects are reported in rejected_archives and never fail the catalog.
# OSError, botocore ClientError/BotoCoreError and socket errors are deliberately absent:
# a storage outage must fail the refresh (so the stale catalog is served), not be
# published as though every archive were malformed. EOFError is a truncated deflate stream.
ARCHIVE_ERRORS = (BadZipFile, EOFError, KeyError, UnicodeDecodeError, ValueError, zlib.error, RuntimeError, TypeError)
logger = logging.getLogger("catalog")


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


def cache_ttl() -> float:
    """Parse CATALOG_CACHE_TTL once at startup; a bad value must stop the service."""

    raw = os.environ.get("CATALOG_CACHE_TTL", "").strip()
    if not raw:
        return DEFAULT_CACHE_TTL
    try:
        ttl = float(raw)
    except ValueError:
        raise RuntimeError(f"CATALOG_CACHE_TTL must be a number of seconds, got {raw!r}") from None
    if not math.isfinite(ttl) or ttl < 0:
        raise RuntimeError(f"CATALOG_CACHE_TTL must be a finite, non-negative number of seconds, got {raw!r}")
    return ttl


def read_members(body: bytes) -> tuple[object, object, bytes, str, str]:
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
        for name in REQUIRED_MEMBERS:
            # Header sizes can be forged, so bound the actual decompressed read.
            with archive.open(name) as member:
                data = member.read(MAX_MEMBER_BYTES + 1)
            if len(data) > MAX_MEMBER_BYTES:
                raise ValueError(f"archive member is too large: {name!r}")
            members[name] = data
    license_text = members["LICENSE.txt"].decode("utf-8").strip()
    attribution = members["ATTRIBUTION.md"].decode("utf-8").strip()
    if not license_text or not attribution:
        raise ValueError("license and attribution files must not be empty")
    return json.loads(members["manifest.json"]), json.loads(members["pack.json"]), members["pack.json"], license_text, attribution


def printable(value: object, label: str, *, nonempty: bool = True) -> None:
    """Reject values that cannot be safely printed in catalog consumers."""

    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if nonempty and not value.strip():
        raise ValueError(f"{label} must not be empty")
    match = CONTROL.search(value)
    if match:
        raise ValueError(f"{label} contains forbidden control character U+{ord(match.group()):04X}")


def validate_visible_text(pack: dict[str, object], prompts: list[object], answers: list[object], schema: int) -> None:
    """Mirror the importer's control-character checks without importing the package."""

    metadata = pack["metadata"]
    if not isinstance(metadata, dict):  # not an assert: python -O strips those
        raise ValueError("pack metadata must be a JSON object")
    for field in PRINTED_METADATA_STRINGS:
        printable(metadata.get(field), f"metadata.{field}")
    for field in PRINTED_METADATA_LISTS:
        values = metadata.get(field)
        if not isinstance(values, list):
            raise ValueError(f"metadata.{field} must be a JSON array")
        for index, value in enumerate(values):
            printable(value, f"metadata.{field}[{index}]", nonempty=False)
    for index, prompt in enumerate(prompts):
        if not isinstance(prompt, dict):
            raise ValueError("pack prompt entries must be JSON objects")
        for field in (("repr", "template") if schema == 1 else ("text", "template")):
            printable(prompt.get(field), f"prompt[{index}].{field}")
    for index, answer in enumerate(answers):
        if not isinstance(answer, dict):
            raise ValueError("pack answer entries must be JSON objects")
        printable(answer.get("text"), f"answer[{index}].text")


def validate_manifest(manifest: dict[str, object], pack: dict[str, object], payload: bytes) -> None:
    """Validate the integrity and identity fields the catalog republishes."""

    if set(manifest) != MANIFEST_MEMBERS:
        raise ValueError("manifest.json has missing or unexpected fields")
    if manifest.get("format") != "carddeck" or type(manifest.get("format_version")) is not int or manifest["format_version"] != 1:
        raise ValueError("unsupported archive format or version")
    pack_id = manifest.get("pack_id")
    digest = manifest.get("pack_sha256")
    if not isinstance(pack_id, str) or not PACK_ID.fullmatch(pack_id) or pack_id == "all":
        raise ValueError("manifest pack_id is invalid")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("manifest pack_sha256 is invalid")
    if digest != hashlib.sha256(payload).hexdigest():
        raise ValueError("pack.json checksum does not match manifest")


class PayloadCache:
    """TTL cache of the encoded catalog; refreshes are serialized and coalesced.

    get() returns (body, remaining_seconds). remaining_seconds is how long the body
    may still be cached downstream; it is 0 for a stale copy served after a failed refresh.
    """

    def __init__(self, build: Callable[[], dict[str, object]], ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._build = build
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._encoded: bytes | None = None
        self._expires_at = 0.0
        self._stale = False
        self._failure: Exception | None = None
        self._failed_until = 0.0

    def get(self) -> tuple[bytes, float]:
        with self._lock:
            now = self._clock()
            if self._encoded is not None:
                if now < self._expires_at:
                    return self._encoded, 0.0 if self._stale else self._expires_at - now
            elif self._failure is not None and now < self._failed_until:
                # Cold failure: do not let every request rescan a storage backend that is down.
                raise self._failure
            try:
                encoded = json.dumps(self._build(), separators=(",", ":"), ensure_ascii=False).encode()
            except Exception as exc:
                # The scan itself can be slow, so hold off from the time it finished, not started.
                built = self._clock()
                retry_at = built + min(self._ttl, STALE_RETRY_SECONDS)
                if self._encoded is None:
                    self._failure, self._failed_until = exc, retry_at
                    raise
                logger.warning("catalog refresh failed; serving the stale catalog", exc_info=True)
                self._stale, self._expires_at = True, retry_at
                return self._encoded, 0.0
            built = self._clock()
            self._encoded, self._expires_at = encoded, built + self._ttl
            self._stale, self._failure = False, None
            return encoded, self._ttl


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
        stream = self.client.get_object(Bucket=bucket, Key=key)["Body"]
        with closing(stream):
            body = stream.read(MAX_ARCHIVE_BYTES + 1)
        if len(body) > MAX_ARCHIVE_BYTES:
            raise ValueError(f"archive exceeds {MAX_ARCHIVE_BYTES} byte catalog limit")
        manifest, pack, payload, license_text, attribution = read_members(body)
        if not isinstance(manifest, dict) or not isinstance(pack, dict) or not isinstance(pack.get("metadata"), dict):
            raise ValueError("manifest.json, pack.json, and pack metadata must be JSON objects")
        validate_manifest(manifest, pack, payload)
        # Pack schema 1 named prompts and answers "black" and "white"; both are catalogued.
        schema = pack.get("schema_version")
        if schema not in (1, 2) or isinstance(schema, bool):
            raise ValueError("pack.json must declare schema_version 1 or 2")
        prompts, answers = (pack.get("black", []), pack.get("white", [])) if schema == 1 else (pack.get("prompts", []), pack.get("answers", []))
        if not isinstance(prompts, list) or not isinstance(answers, list):
            raise ValueError("pack card lists must be JSON arrays")
        metadata = pack["metadata"]
        if metadata.get("id") != manifest["pack_id"]:
            raise ValueError("manifest pack_id does not match pack metadata")
        license_notice = metadata.get("license_notice")
        declared_attribution = metadata.get("attribution")
        if not isinstance(license_notice, str) or license_text != license_notice.strip():
            raise ValueError("LICENSE.txt does not match metadata.license_notice")
        if not isinstance(declared_attribution, str) or attribution != declared_attribution.strip():
            raise ValueError("ATTRIBUTION.md does not match metadata.attribution")
        validate_visible_text(pack, prompts, answers, schema)
        return {"bucket": bucket, "object_key": key, "url": f"https://{bucket}.{self.object_domain}/{quote(key)}", "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body), "last_modified": listed["LastModified"].isoformat(), "etag": str(listed["ETag"]).strip('"'), "archive": manifest, "metadata": pack["metadata"], "prompt_count": len(prompts), "answer_count": len(answers)}

    def payload(self) -> dict[str, object]:
        packs, rejected = [], []
        recoverable = ARCHIVE_ERRORS
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
        return {"schema_version": CATALOG_SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "buckets": list(self.buckets), "pack_count": len(packs), "packs": packs, "rejected_archives": rejected}


class Handler(BaseHTTPRequestHandler):
    cache: PayloadCache  # set by main() after configuration has been validated

    def _serve(self, *, include_body: bool) -> None:
        if self.path != "/packs/index":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            response, remaining = self.cache.get()
        except Exception:
            logger.exception("catalog refresh failed with nothing cached")
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        max_age = int(remaining)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        # Stale fallbacks (and a nearly expired body) must not be cached downstream.
        self.send_header("Cache-Control", f"public, max-age={max_age}" if max_age > 0 else "no-cache")
        self.end_headers()
        if include_body:
            self.wfile.write(response)

    def do_GET(self) -> None:  # noqa: N802
        self._serve(include_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(include_body=False)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    """Validate all configuration before binding, so a bad setting fails at startup."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ttl = cache_ttl()
    host, port = setting("CATALOG_BIND_HOST"), int(setting("CATALOG_BIND_PORT"))
    Handler.cache = PayloadCache(Catalog().payload, ttl)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
