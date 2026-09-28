"""The xkcdb 1.2.1 distribution rebuild stays reproducible and card-preserving."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from bad_decisions.archive import validate_archive

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rebuild_xkcdb", ROOT / "scripts" / "rebuild_xkcdb.py")
rebuild_xkcdb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebuild_xkcdb)

LICENSE = """XKCDB CONTENT LICENSE STATUS

The white cards are curated from quote text.

The black-card prompts were newly authored for this pack."""
ATTRIBUTION = """# Attribution

**Version:** 1.2.0

White-card source material was derived from XKCDB.

Version 1.2.0 contains **200 white cards** and **64 black cards**.

The white deck was rebuilt. Each white card retains a source_ref."""


def source_archive(path: Path, *, attribution: str = ATTRIBUTION) -> None:
    pack = {
        "schema_version": 1,
        "metadata": {
            "id": "xkcdb", "name": "XKCDB", "description": "Example", "version": "1.2.0",
            "language": "en-US", "custom": True, "authors": ["BytesAndCoffee"],
            "attribution": "old", "license_id": "LicenseRef-XKCDB-Unspecified",
            "license_url": None, "license_notice": "old",
            "sources": [{"origin": "https://www.xkcdb.com/", "edition": "white-card corpus"}],
            "modifications": ["Rebuilt the white deck.", "Curated white cards.", "Retained black-card prompts."],
        },
        "black": [{"id": "p1", "repr": "Why? _", "template": "Why? {}", "slots": 1, "pack": "xkcdb"}],
        "white": [{"id": "a1", "text": "Because.", "pack": "xkcdb", "source_ref": "xkcdb:1"}],
    }
    payload = json.dumps(pack, sort_keys=True, separators=(",", ":")).encode()
    manifest = json.dumps({
        "format": "carddeck", "format_version": 1, "pack_id": "xkcdb",
        "pack_sha256": hashlib.sha256(payload).hexdigest(),
    }).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("pack.json", payload)
        archive.writestr("LICENSE.txt", LICENSE)
        archive.writestr("ATTRIBUTION.md", attribution)


def test_rebuild_is_schema_2_card_preserving_and_self_consistent(tmp_path):
    source, destination = tmp_path / "source.carddeck", tmp_path / "rebuilt.carddeck"
    source_archive(source)
    rebuild_xkcdb.rebuild(source, destination)
    pack = validate_archive(destination)
    assert pack.schema_version == 2 and pack.metadata.version == "1.2.1"
    assert ([card.text for card in pack.prompts], [card.text for card in pack.answers]) == (["Why? _"], ["Because."])
    assert "white" not in pack.metadata.attribution.lower()
    assert pack.metadata.modifications[-1].endswith("cards unchanged.")


def test_rebuild_refuses_unexpected_source_wording(tmp_path):
    source_archive(tmp_path / "source.carddeck", attribution=ATTRIBUTION.replace("White-card", "Changed"))
    with pytest.raises(ValueError, match="expected source wording"):
        rebuild_xkcdb.rebuild(tmp_path / "source.carddeck", tmp_path / "rebuilt.carddeck")
