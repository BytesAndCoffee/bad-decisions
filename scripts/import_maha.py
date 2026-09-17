#!/usr/bin/env python3
"""Migrate the attached legacy MAHA Python deck without executing it."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path


def extract(source: Path) -> tuple[list[dict], list[str]]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    black: list[dict] | None = None
    white: list[str] | None = None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        value = node.value
        if "BLACK_CARDS" in names:
            if not isinstance(value, (ast.List, ast.Tuple)):
                raise ValueError("BLACK_CARDS is not a literal sequence")
            cards = []
            for item in value.elts:
                if not isinstance(item, ast.Call) or not isinstance(item.func, ast.Name) or item.func.id != "BlackCard":
                    raise ValueError("BLACK_CARDS contains a non-BlackCard expression")
                fields = {kw.arg: ast.literal_eval(kw.value) for kw in item.keywords}
                cards.append({key: fields[key] for key in ("repr", "template", "slots")})
            black = cards
        elif "WHITE_CARDS" in names:
            white = ast.literal_eval(value)
    if black is None or white is None:
        raise ValueError("legacy source does not define both card arrays")
    return black, white


def canonical_digest(black: list[dict], white: list[str]) -> str:
    payload = json.dumps({"black": black, "white": white}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def build_pack(source: Path) -> tuple[dict, dict]:
    black, white = extract(source)
    digest = canonical_digest(black, white)
    pack = {
        "schema_version": 1,
        "metadata": {
            "id": "maha",
            "name": "MAHA Pack",
            "description": "Custom health and wellness satire deck migrated from the original script.",
            "version": "1.0.0",
            "language": "en-US",
            "custom": True,
            "authors": ["Project owner, with ChatGPT assistance"],
            "attribution": "Custom MAHA deck by the project owner, created with ChatGPT assistance.",
            "license_id": "CC-BY-SA-4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "license_notice": "Licensed under Creative Commons Attribution-ShareAlike 4.0 International; attribution and share-alike are required.",
            "sources": [{
                "origin": "imports/original_cah_maha.py",
                "edition": "Supplied legacy script",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "retrieved": "2026-09-16",
                "license_evidence": "Owner declaration in this project conversation on 2026-09-16; LICENSES/CC-BY-SA-4.0-legalcode.html.",
            }],
            "modifications": ["Added stable IDs, pack identifiers, source references, and metadata; card fields and ordering unchanged."],
        },
        "black": [
            {"id": f"b{i:03d}", **card, "pack": "maha", "source_ref": f"legacy:BLACK_CARDS:{i}"}
            for i, card in enumerate(black, 1)
        ],
        "white": [
            {"id": f"w{i:03d}", "text": text, "pack": "maha", "source_ref": f"legacy:WHITE_CARDS:{i}"}
            for i, text in enumerate(white, 1)
        ],
    }
    report = {
        "source": str(source),
        "black_count": len(black),
        "white_count": len(white),
        "canonical_content_sha256": digest,
        "comparison": "Every repr/template/slots tuple and white text is compared by tests after removing migration-only fields.",
    }
    return pack, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    pack, report = build_pack(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(pack['black'])} black and {len(pack['white'])} white cards; digest {report['canonical_content_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
