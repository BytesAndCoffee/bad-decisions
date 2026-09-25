"""Owner-side publishing for the AWS CardDeck archive and runtime registry."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from botocore.config import Config
from botocore.exceptions import ClientError
from .archive import FORMAT, FORMAT_VERSION, _pack_payload, validate_archive
from .aws_credentials import local_session
from .errors import PackConfigurationError
_RETRY = Config(retries={"total_max_attempts": 4, "mode": "adaptive"})

def _session(profile: str | None, region: str | None):
    return local_session(profile, region)

def _put_new(client, *, bucket: str, key: str, body: bytes, content_type: str, cache_control: str) -> str | None:
    try:
        response = client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type, CacheControl=cache_control, IfNoneMatch="*")
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = exc.response.get("Error", {}).get("Code")
        if status == 412 or code in {"PreconditionFailed", "ConditionalRequestConflict"}:
            raise PackConfigurationError(f"refusing to overwrite existing AWS object: s3://{bucket}/{key}") from exc
        raise
    return response.get("VersionId")

def publish_archive(archive_path: str | Path, *, bucket: str, public_base_url: str, profile: str | None = None, region: str | None = None) -> dict[str, object]:
    """Validate then atomically expose archive, runtime JSON, and catalog metadata."""
    path = Path(archive_path)
    pack = validate_archive(path)
    archive_bytes = path.read_bytes()
    runtime = _pack_payload(pack)
    digest = hashlib.sha256(archive_bytes).hexdigest()
    pack_id = pack.metadata.id
    archive_key = f"packs/{pack_id}.carddeck"
    runtime_key = f"runtime-packs/{pack_id}.json"
    catalog_key = f"catalog/{pack_id}.json"
    metadata = {
        "bucket": bucket, "object_key": archive_key, "url": f"{public_base_url.rstrip('/')}/{quote(archive_key)}",
        "sha256": digest, "size_bytes": len(archive_bytes), "last_modified": datetime.now(timezone.utc).isoformat(), "etag": digest,
        "archive": {"format": FORMAT, "format_version": FORMAT_VERSION, "pack_id": pack_id, "pack_sha256": hashlib.sha256(runtime).hexdigest()},
        "metadata": pack.metadata.model_dump(mode="json"), "black_card_count": len(pack.black), "white_card_count": len(pack.white),
    }
    catalog = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    client = _session(profile, region).client("s3", config=_RETRY)
    created: list[tuple[str, str | None]] = []
    try:
        created.append((archive_key, _put_new(client, bucket=bucket, key=archive_key, body=archive_bytes, content_type="application/zip", cache_control="public, max-age=31536000, immutable")))
        created.append((runtime_key, _put_new(client, bucket=bucket, key=runtime_key, body=runtime, content_type="application/json", cache_control="no-store")))
        created.append((catalog_key, _put_new(client, bucket=bucket, key=catalog_key, body=catalog, content_type="application/json", cache_control="no-store")))
    except BaseException:
        for key, version in reversed(created):
            arguments = {"Bucket": bucket, "Key": key}
            if version:
                arguments["VersionId"] = version
            client.delete_object(**arguments)
        raise
    return metadata

def read_catalog(*, bucket: str, profile: str | None = None, region: str | None = None) -> dict[str, object]:
    client = _session(profile, region).client("s3", config=_RETRY)
    try:
        response = client.get_object(Bucket=bucket, Key="packs/index")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            return {"schema_version": 1, "pack_count": 0, "packs": []}
        raise
    return json.loads(response["Body"].read())

def force_runtime_reload(*, cluster: str, service: str, profile: str | None = None, region: str | None = None) -> None:
    _session(profile, region).client("ecs", config=_RETRY).update_service(cluster=cluster, service=service, forceNewDeployment=True)

def archive_exists(*, bucket: str, pack_id: str, profile: str | None = None, region: str | None = None) -> bool:
    client = _session(profile, region).client("s3", config=_RETRY)
    try:
        for key in (f"packs/{pack_id}.carddeck", f"runtime-packs/{pack_id}.json", f"catalog/{pack_id}.json"):
            client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404 or exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    return True
