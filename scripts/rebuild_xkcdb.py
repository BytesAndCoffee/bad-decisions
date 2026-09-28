"""Rebuild the owner-supplied xkcdb 1.2 archive for Bad Decisions 2.0.

Usage: PYTHONPATH=src .venv/bin/python scripts/rebuild_xkcdb.py SOURCE.carddeck OUTPUT.carddeck
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from bad_decisions.archive import _read_archive, export_pack
from bad_decisions.models import Pack


def replace_exact(text: str, replacements: tuple[tuple[str, str], ...]) -> str:
    for old, new in replacements:
        if old not in text:
            raise ValueError(f"expected source wording is missing: {old!r}")
        text = text.replace(old, new)
    return text


def read_source(path: Path) -> tuple[Pack, str, str]:
    manifest, pack, _payload, license_text, attribution = _read_archive(path, require_document_match=False)
    if manifest.pack_id != "xkcdb" or pack.metadata.id != "xkcdb" or pack.metadata.version != "1.2.0":
        raise ValueError("source must be the xkcdb 1.2.0 archive")
    return pack, license_text, attribution


def rebuild(source: Path, destination: Path) -> Path:
    pack, license_text, attribution = read_source(source)
    license_text = replace_exact(license_text, (("The white cards", "The answers"), ("The black-card prompts", "The prompts")))
    attribution = replace_exact(
        attribution,
        (
            ("**Version:** 1.2.0", "**Version:** 1.2.1"),
            ("White-card source material", "Answer source material"),
            ("Version 1.2.0 contains **200 white cards** and **64 black cards**.", "Version 1.2.1 contains **200 answers** and **64 prompts**."),
            ("The white deck", "The answer deck"),
            ("Each white card", "Each answer"),
        ),
    )
    value = pack.model_dump(mode="json")
    metadata = value["metadata"]
    metadata["version"] = "1.2.1"
    metadata["license_notice"] = license_text
    metadata["attribution"] = attribution
    metadata["modifications"] = [
        item.replace("white deck", "answer deck").replace("white cards", "answers").replace("black-card prompts", "prompts")
        for item in metadata["modifications"]
    ] + ["1.2.1: license_notice and attribution now carry the archive's full LICENSE.txt and ATTRIBUTION.md text, worded as prompts and answers; cards unchanged."]
    for source_info in metadata["sources"]:
        if source_info.get("edition"):
            source_info["edition"] = source_info["edition"].replace("white-card corpus", "answer corpus")
    rebuilt = Pack.model_validate(value)
    if rebuilt.prompts != pack.prompts or rebuilt.answers != pack.answers:
        raise AssertionError("the rebuild changed card content")
    return export_pack(rebuilt, destination)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: rebuild_xkcdb.py SOURCE.carddeck OUTPUT.carddeck", file=sys.stderr)
        return 2
    try:
        output = rebuild(Path(argv[1]), Path(argv[2]))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{output}  sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
