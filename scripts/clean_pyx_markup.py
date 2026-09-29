"""Rewrite already-imported PYX CardDeck archives with plain-text card text.

Early PYX imports kept the dump's HTML: character references such as &reg;,
<br> line breaks, and <i> italics. This applies the importer's current
clean_pyx_text() to every PYX-sourced archive in SOURCE and writes only the
archives that change to DESTINATION, with a new pack version and a recorded
modification. Card IDs, order, slots, source references, and every other field
are unchanged. The run is all-or-nothing: results are staged beside
DESTINATION and published only after each one validates.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/clean_pyx_markup.py SOURCE DESTINATION
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from bad_decisions.archive import _read_archive, export_pack, validate_archive
from bad_decisions.errors import PackConfigurationError
from bad_decisions.models import Pack
from bad_decisions.pyx_import import PYX_MARKUP_NOTE, clean_pyx_text

PYX_SOURCE = "PretendYoureXyzzy"
VERSION_SUFFIX = "+plaintext"


def _is_pyx(pack: Pack) -> bool:
    return any(PYX_SOURCE in source.origin for source in pack.metadata.sources)


def clean_pack(pack: Pack) -> tuple[Pack, int] | None:
    """Return the cleaned pack and the number of changed cards, or None if already clean."""

    raw = pack.model_dump(mode="json")
    changed = 0
    for prompt in raw["prompts"]:
        cleaned = {field: clean_pyx_text(prompt[field]) for field in ("text", "template")}
        if cleaned != {field: prompt[field] for field in cleaned}:
            prompt.update(cleaned)
            changed += 1
    for answer in raw["answers"]:
        cleaned = clean_pyx_text(answer["text"])
        if cleaned != answer["text"]:
            answer["text"] = cleaned
            changed += 1
    if not changed:
        return None
    metadata = raw["metadata"]
    if metadata["version"].endswith(VERSION_SUFFIX) or PYX_MARKUP_NOTE in metadata["modifications"]:
        raise ValueError(f"{metadata['id']}: already cleaned but still contains markup")
    metadata["version"] += VERSION_SUFFIX
    metadata["modifications"].append(PYX_MARKUP_NOTE)
    result = Pack.model_validate(raw)
    ids = lambda p: ([c.id for c in p.prompts], [c.id for c in p.answers])  # noqa: E731
    if ids(result) != ids(pack) or [c.slots for c in result.prompts] != [c.slots for c in pack.prompts]:
        raise AssertionError(f"{metadata['id']}: cleaning changed card identity, order, or slots")
    return result, changed


def clean_directory(source: Path, destination: Path) -> dict[str, object]:
    if not source.is_dir():
        raise ValueError(f"source is not a directory: {source}")
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    archives = sorted(source.glob("*.carddeck"))
    if not archives:
        raise ValueError(f"source contains no .carddeck archives: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    entries: list[dict[str, object]] = []
    try:
        for archive in archives:
            _manifest, pack, _payload, _license, _attribution = _read_archive(archive)
            if not _is_pyx(pack):
                continue
            try:
                outcome = clean_pack(pack)
            except PackConfigurationError as exc:
                raise ValueError(f"{archive.name}: {exc.message}") from exc
            if outcome is None:
                continue
            cleaned, changed = outcome
            output = export_pack(cleaned, staging / archive.name)
            if validate_archive(output) != cleaned:
                raise AssertionError(f"{archive.name}: exported pack changed during validation")
            entries.append(
                {
                    "archive": archive.name,
                    "pack_id": cleaned.metadata.id,
                    "pack_version": cleaned.metadata.version,
                    "changed_cards": changed,
                    "source_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                }
            )
        manifest: dict[str, object] = {"archive_count": len(entries), "archives": entries}
        (staging / "clean-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(staging, destination)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        manifest = clean_directory(args.source, args.destination)
    except (OSError, ValueError, PackConfigurationError) as exc:
        message = exc.message if isinstance(exc, PackConfigurationError) else exc
        parser.exit(1, f"error: {message}\n")
    print(f"cleaned {manifest['archive_count']} archives into {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
