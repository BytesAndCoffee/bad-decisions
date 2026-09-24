"""Rebuild the public CardDeck catalog from trusted publisher metadata."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
import boto3
MAX_METADATA_BYTES = 256 * 1024

def _entry(client, bucket: str, key: str) -> dict[str, object]:
    response = client.get_object(Bucket=bucket, Key=key)
    if response.get("ContentLength", MAX_METADATA_BYTES + 1) > MAX_METADATA_BYTES:
        raise ValueError(f"{key}: metadata is too large")
    body = response["Body"].read(MAX_METADATA_BYTES + 1)
    if len(body) > MAX_METADATA_BYTES:
        raise ValueError(f"{key}: metadata is too large")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError(f"{key}: metadata must be an object")
    archive = value.get("archive")
    if not isinstance(archive, dict) or not isinstance(archive.get("pack_id"), str):
        raise ValueError(f"{key}: archive.pack_id must be a string")
    if not isinstance(value.get("url"), str) or not value["url"].startswith("https://"):
        raise ValueError(f"{key}: url must be HTTPS")
    return value

def build_catalog(client, bucket: str) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="catalog/"):
        for item in page.get("Contents", []):
            key = item.get("Key", "")
            if not key.endswith(".json"):
                continue
            entry = _entry(client, bucket, key)
            pack_id = str(entry["archive"]["pack_id"])
            if pack_id in seen:
                raise ValueError(f"duplicate pack ID: {pack_id}")
            seen.add(pack_id)
            entries.append(entry)
    entries.sort(key=lambda item: (str(item.get("bucket", "")), str(item.get("object_key", ""))))
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "buckets": [bucket], "pack_count": len(entries), "packs": entries, "rejected_archives": []}

def handler(_event, _context):
    bucket = os.environ["BUCKET"]
    client = boto3.client("s3")
    document = build_catalog(client, bucket)
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    client.put_object(Bucket=bucket, Key="packs/index", Body=payload, ContentType="application/json; charset=utf-8", CacheControl="public, max-age=30")
    return {"pack_count": document["pack_count"]}
