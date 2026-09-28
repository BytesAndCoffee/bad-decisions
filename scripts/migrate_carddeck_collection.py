"""Atomically migrate an audited S3-compatible CardDeck collection.

Dry-run validates the live objects against the rebuild manifest. The --apply
flag copies every original to a non-indexed rollback key, uploads each
replacement, and verifies its SHA-256. A failure restores every changed object.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_OBJECT_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Migration:
    bucket: str
    key: str
    archive: Path
    source_sha256: str
    output_sha256: str


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def build_plan(catalog_path: Path, manifest_path: Path, archive_dir: Path) -> list[Migration]:
    catalog = read_json(catalog_path)
    manifest = read_json(manifest_path)
    packs = catalog.get("packs")
    entries = manifest.get("archives")
    if not isinstance(packs, list) or not isinstance(entries, list):
        raise ValueError("catalog and rebuild manifest must contain arrays")
    by_id = {entry.get("pack_id"): entry for entry in entries if isinstance(entry, dict)}
    if len(by_id) != len(entries) or catalog.get("pack_count") != len(packs) or len(packs) != len(entries):
        raise ValueError("catalog and rebuild manifest must contain the same unique pack set")
    result = []
    for item in packs:
        if not isinstance(item, dict) or not isinstance(item.get("archive"), dict):
            raise ValueError("invalid catalog pack entry")
        pack_id = item["archive"].get("pack_id")
        entry = by_id.get(pack_id)
        if entry is None:
            raise ValueError(f"catalog pack is absent from rebuild manifest: {pack_id}")
        bucket, key = item.get("bucket"), item.get("object_key")
        if not isinstance(bucket, str) or not bucket or not isinstance(key, str) or not key:
            raise ValueError(f"{pack_id}: invalid bucket or object key")
        expected_name = key.replace("/", "_")
        if entry.get("archive") != expected_name:
            raise ValueError(f"{pack_id}: object key does not match audited archive name")
        if item.get("sha256") != entry.get("source_sha256"):
            raise ValueError(f"{pack_id}: live catalog digest does not match audited source")
        archive = archive_dir / expected_name
        data = archive.read_bytes()
        if len(data) > MAX_OBJECT_BYTES or digest(data) != entry.get("sha256"):
            raise ValueError(f"{pack_id}: rebuilt archive does not match its manifest")
        result.append(Migration(bucket, key, archive, entry["source_sha256"], entry["sha256"]))
    return sorted(result, key=lambda value: (value.bucket, value.key))


def read_object(client, bucket: str, key: str) -> bytes:
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        data = body.read(MAX_OBJECT_BYTES + 1)
    finally:
        body.close()
    if len(data) > MAX_OBJECT_BYTES:
        raise ValueError(f"{bucket}/{key}: object exceeds the CardDeck size limit")
    return data


def rollback_key(prefix: str, migration: Migration) -> str:
    return f"{prefix}/{migration.key}.v1-backup"


def object_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
            return False
        raise
    return True


def migrate(client, plan: list[Migration], backup_prefix: str, *, apply: bool) -> int:
    for item in plan:
        if digest(read_object(client, item.bucket, item.key)) != item.source_sha256:
            raise ValueError(f"{item.bucket}/{item.key}: live object changed after catalog generation")
    if not apply:
        return len(plan)
    for item in plan:
        backup = rollback_key(backup_prefix, item)
        if object_exists(client, item.bucket, backup):
            raise ValueError(f"{item.bucket}/{backup}: rollback object already exists")

    changed: list[Migration] = []
    try:
        for item in plan:
            backup = rollback_key(backup_prefix, item)
            client.copy_object(
                Bucket=item.bucket,
                Key=backup,
                CopySource={"Bucket": item.bucket, "Key": item.key},
                ContentType="application/zip",
                MetadataDirective="REPLACE",
            )
            if digest(read_object(client, item.bucket, backup)) != item.source_sha256:
                raise ValueError(f"{item.bucket}/{item.key}: rollback copy failed verification")
            changed.append(item)
            client.put_object(
                Bucket=item.bucket,
                Key=item.key,
                Body=item.archive.read_bytes(),
                ContentType="application/zip",
                CacheControl="public, max-age=300",
            )
            if digest(read_object(client, item.bucket, item.key)) != item.output_sha256:
                raise ValueError(f"{item.bucket}/{item.key}: replacement failed verification")
    except BaseException:
        rollback_errors = []
        for item in reversed(changed):
            try:
                client.copy_object(
                    Bucket=item.bucket,
                    Key=item.key,
                    CopySource={"Bucket": item.bucket, "Key": rollback_key(backup_prefix, item)},
                    ContentType="application/zip",
                    MetadataDirective="REPLACE",
                )
                if digest(read_object(client, item.bucket, item.key)) != item.source_sha256:
                    raise ValueError("restored digest mismatch")
            except BaseException as exc:
                rollback_errors.append(f"{item.bucket}/{item.key}: {exc}")
        if rollback_errors:
            raise RuntimeError("migration failed and rollback was incomplete: " + "; ".join(rollback_errors))
        raise
    return len(changed)


def credentials(path: Path) -> tuple[str, str]:
    fields = dict(line.split(":", 1) for line in path.read_text(encoding="utf-8").splitlines() if ":" in line)
    try:
        return fields["Key ID"].strip(), fields["Secret key"].strip()
    except KeyError as exc:
        raise ValueError("credential file is malformed") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("archives", type=Path)
    parser.add_argument("--endpoint-url", required=True)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--region", default="us-west-1")
    parser.add_argument("--backup-prefix")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    prefix = args.backup_prefix or datetime.now(timezone.utc).strftime("rollback/carddeck-v2-%Y%m%dT%H%M%SZ")
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        print("error: boto3 is required for Garage migration", file=sys.stderr)
        return 1
    try:
        access_key, secret_key = credentials(args.credentials)
        client = boto3.client(
            "s3",
            endpoint_url=args.endpoint_url,
            region_name=args.region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"total_max_attempts": 3, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )
        plan = build_plan(args.catalog, args.manifest, args.archives)
        count = migrate(client, plan, prefix, apply=args.apply)
    except (OSError, ValueError, RuntimeError, BotoCoreError, ClientError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    action = "migrated" if args.apply else "validated"
    print(f"{action} {count} archives; rollback prefix: {prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
