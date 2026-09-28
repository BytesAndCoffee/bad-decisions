"""Rebuild a directory of validated CardDeck archives to pack schema 2.

The rebuild is all-or-nothing: archives are written to a sibling staging
directory and published only after every result validates. A source filename
may be replaced by an already-audited archive.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/rebuild_carddecks_v2.py SOURCE OUTPUT \
      --replace packs_xkcdb.carddeck=build/xkcdb/xkcdb.carddeck
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from bad_decisions.archive import _read_archive, export_pack, validate_archive
from bad_decisions.models import PACK_SCHEMA_VERSION

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _replacement(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or Path(name).name != name or not name.endswith(".carddeck") or not path:
        raise argparse.ArgumentTypeError("replacement must be ARCHIVE.carddeck=PATH")
    return name, Path(path)


def load_inventory(path: Path) -> dict[str, tuple[str, str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read source inventory {path}: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {"inventory_version", "archives"}:
        raise ValueError("source inventory must contain only inventory_version and archives")
    if value["inventory_version"] != 1 or not isinstance(value["archives"], list):
        raise ValueError("unsupported source inventory")
    inventory: dict[str, tuple[str, str]] = {}
    for entry in value["archives"]:
        if not isinstance(entry, dict) or set(entry) != {"archive", "pack_id", "sha256"}:
            raise ValueError("invalid source inventory entry")
        name, pack_id, digest = entry["archive"], entry["pack_id"], entry["sha256"]
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".carddeck"):
            raise ValueError("invalid archive name in source inventory")
        if not isinstance(pack_id, str) or not pack_id:
            raise ValueError(f"invalid pack_id for {name}")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise ValueError(f"invalid sha256 for {name}")
        if name in inventory:
            raise ValueError(f"duplicate source inventory entry: {name}")
        inventory[name] = (pack_id, digest)
    if not inventory:
        raise ValueError("source inventory is empty")
    return inventory


def rebuild_directory(
    source: Path,
    destination: Path,
    replacements: dict[str, Path],
    inventory: dict[str, tuple[str, str]] | None = None,
) -> dict[str, object]:
    if not source.is_dir():
        raise ValueError(f"source is not a directory: {source}")
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    archives = sorted(source.glob("*.carddeck"))
    if not archives:
        raise ValueError(f"source contains no .carddeck archives: {source}")
    source_names = {archive.name for archive in archives}
    if inventory is not None and source_names != set(inventory):
        missing = sorted(set(inventory) - source_names)
        extra = sorted(source_names - set(inventory))
        raise ValueError(f"source inventory mismatch: missing={missing}, extra={extra}")
    unknown = sorted(set(replacements) - source_names)
    if unknown:
        raise ValueError(f"replacement has no matching source archive: {', '.join(unknown)}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    entries: list[dict[str, object]] = []
    try:
        for source_archive in archives:
            source_digest = hashlib.sha256(source_archive.read_bytes()).hexdigest()
            source_manifest, source_pack, _payload, _license, _attribution = _read_archive(
                source_archive, require_document_match=False
            )
            if inventory is not None:
                expected_id, expected_digest = inventory[source_archive.name]
                if source_manifest.pack_id != expected_id:
                    raise ValueError(f"{source_archive.name}: inventory pack_id does not match archive")
                if source_digest != expected_digest:
                    raise ValueError(f"{source_archive.name}: source sha256 does not match inventory")
            selected = replacements.get(source_archive.name, source_archive)
            pack = validate_archive(selected)
            if source_pack.metadata.id != pack.metadata.id:
                raise ValueError(f"{source_archive.name}: replacement pack_id does not match source")
            if source_pack.prompts != pack.prompts or source_pack.answers != pack.answers:
                raise ValueError(f"{source_archive.name}: replacement changed card content or order")
            if pack.schema_version != PACK_SCHEMA_VERSION:
                raise ValueError(f"{selected}: did not upgrade to schema {PACK_SCHEMA_VERSION}")
            output = export_pack(pack, staging / source_archive.name)
            verified = validate_archive(output)
            if verified != pack:
                raise AssertionError(f"{source_archive.name}: exported pack changed during validation")
            entries.append(
                {
                    "archive": source_archive.name,
                    "pack_id": pack.metadata.id,
                    "pack_version": pack.metadata.version,
                    "schema_version": pack.schema_version,
                    "prompts": len(pack.prompts),
                    "answers": len(pack.answers),
                    "source_sha256": source_digest,
                    "selected_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    "replacement": source_archive.name in replacements,
                }
            )
        manifest: dict[str, object] = {
            "schema_version": PACK_SCHEMA_VERSION,
            "archive_count": len(entries),
            "archives": entries,
        }
        (staging / "rebuild-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(staging, destination)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--inventory", type=Path, help="require an exact source filename, pack ID, and SHA-256 inventory")
    parser.add_argument("--replace", action="append", default=[], type=_replacement, metavar="NAME=PATH")
    args = parser.parse_args()
    replacements = dict(args.replace)
    if len(replacements) != len(args.replace):
        parser.error("each replacement archive name may be specified only once")
    try:
        inventory = load_inventory(args.inventory) if args.inventory else None
        manifest = rebuild_directory(args.source, args.destination, replacements, inventory)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(f"rebuilt {manifest['archive_count']} archives at {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
