#!/usr/bin/env python3
"""Convert the owner-authorized Cards Against Coffee legacy ZIP to a portable pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from cah_engine.archive import export_pack
from cah_engine.models import Pack

LEGACY_DECK_MEMBER = "bytesandcoffee-pack-v2.json"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
LICENSE_NOTICE = "Licensed under Creative Commons Attribution-ShareAlike 4.0 International; attribution and share-alike are required."


def load_legacy(path: Path) -> tuple[dict, bytes]:
    try:
        with zipfile.ZipFile(path) as archive:
            payload = archive.read(LEGACY_DECK_MEMBER)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError(f"cannot read {LEGACY_DECK_MEMBER} from {path}: {exc}") from exc
    try:
        return json.loads(payload), payload
    except json.JSONDecodeError as exc:
        raise ValueError(f"legacy deck is not valid JSON: {exc}") from exc


def convert(legacy: dict, source_sha256: str) -> Pack:
    if not isinstance(legacy.get("cards"), list) or not isinstance(legacy.get("counts"), dict):
        raise ValueError("legacy deck must contain cards and counts")
    black: list[dict] = []
    white: list[dict] = []
    for row in legacy["cards"]:
        if not isinstance(row, dict) or row.get("type") not in {"black", "white"}:
            raise ValueError("legacy deck contains an invalid card type")
        text = row.get("text")
        original_id = row.get("id")
        source_ids = row.get("source_ids")
        if not isinstance(text, str) or not text.strip() or not isinstance(original_id, str) or not isinstance(source_ids, list):
            raise ValueError("legacy deck contains an invalid card")
        source_ref = "legacy-card:" + original_id
        if source_ids:
            source_ref += ";source-ids:" + ",".join(str(value) for value in source_ids)
        if row["type"] == "black":
            pick = row.get("pick")
            if type(pick) is not int or pick < 1 or text.count("____") != pick:
                raise ValueError(f"legacy black card {original_id!r} has invalid blanks")
            black.append(
                {
                    "id": f"coffee-black-{len(black) + 1:03d}",
                    "repr": text,
                    "template": text.replace("____", "{}"),
                    "slots": pick,
                    "pack": "coffee",
                    "source_ref": source_ref,
                }
            )
        else:
            white.append(
                {
                    "id": f"coffee-white-{len(white) + 1:03d}",
                    "text": text,
                    "pack": "coffee",
                    "source_ref": source_ref,
                }
            )
    if legacy["counts"] != {"black": len(black), "white": len(white)}:
        raise ValueError("legacy declared counts do not match its cards")
    raw = {
        "schema_version": 1,
        "metadata": {
            "id": "coffee",
            "name": str(legacy.get("title", "Cards Against Coffee")),
            "description": "Owner-authorized card deck adapted from the owner's IRC messages; raw message corpus excluded.",
            "version": str(legacy.get("edition", "2")),
            "language": "en",
            "custom": True,
            "authors": ["Michael Yazdani"],
            "attribution": "Cards Against Coffee by Michael Yazdani, based on the author's IRC messages.",
            "license_id": "CC-BY-SA-4.0",
            "license_url": LICENSE_URL,
            "license_notice": LICENSE_NOTICE,
            "sources": [
                {
                    "origin": LEGACY_DECK_MEMBER,
                    "edition": f"Legacy edition {legacy.get('edition', 'unknown')}",
                    "sha256": source_sha256,
                    "license_evidence": "Owner declaration in this project conversation; CC BY-SA 4.0 designation.",
                }
            ],
            "modifications": [
                "Converted legacy underscores to explicit template placeholders and assigned stable portable IDs.",
                "Raw IRC message corpus and source-message bodies intentionally excluded; only compact source references remain.",
            ],
        },
        "black": black,
        "white": white,
    }
    return Pack.model_validate(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_zip", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--archive", type=Path, help="also write a .cahpack archive")
    args = parser.parse_args()
    legacy, payload = load_legacy(args.legacy_zip)
    pack = convert(legacy, hashlib.sha256(payload).hexdigest())
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.archive:
        export_pack(pack, args.archive)
    print(f"converted {len(pack.black)} black and {len(pack.white)} white cards to {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
