"""Bulk CardDeck schema-2 rebuilding is validated and transactional."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from bad_decisions.archive import export_pack, validate_archive
from bad_decisions.models import Pack

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rebuild_carddecks_v2", ROOT / "scripts" / "rebuild_carddecks_v2.py")
rebuild_carddecks_v2 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(rebuild_carddecks_v2)


def pack(pack_id: str, version: str = "1") -> Pack:
    return Pack.model_validate(
        {
            "schema_version": 2,
            "metadata": {
                "id": pack_id,
                "name": pack_id,
                "description": "Test pack",
                "version": version,
                "language": "en-US",
                "custom": True,
                "authors": ["Test Author"],
                "attribution": "Test attribution",
                "license_id": "CC0-1.0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "license_notice": "CC0 test notice",
                "sources": [{"origin": "https://example.invalid/source"}],
                "modifications": [],
            },
            "prompts": [{"id": "p1", "text": "Why? _", "template": "Why? {}", "slots": 1, "pack": pack_id}],
            "answers": [{"id": "a1", "text": "Because.", "pack": pack_id}],
        }
    )


def test_rebuilds_every_archive_and_preserves_names_and_metadata(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    export_pack(pack("alpha"), source / "packs_alpha.carddeck")
    export_pack(pack("beta"), source / "packs_beta.carddeck")

    manifest = rebuild_carddecks_v2.rebuild_directory(source, destination, {})

    assert manifest["archive_count"] == 2
    assert {path.name for path in destination.glob("*.carddeck")} == {
        "packs_alpha.carddeck", "packs_beta.carddeck"
    }
    assert validate_archive(destination / "packs_alpha.carddeck") == pack("alpha")
    assert all(entry["schema_version"] == 2 for entry in manifest["archives"])
    assert (destination / "rebuild-manifest.json").is_file()


def test_replacement_is_published_under_original_name(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    replacement = tmp_path / "replacement.carddeck"
    export_pack(pack("alpha", "1"), source / "packs_alpha.carddeck")
    export_pack(pack("alpha", "2"), replacement)

    manifest = rebuild_carddecks_v2.rebuild_directory(
        source, destination, {"packs_alpha.carddeck": replacement}
    )

    assert validate_archive(destination / "packs_alpha.carddeck").metadata.version == "2"
    assert manifest["archives"][0]["replacement"] is True


def test_replacement_must_preserve_pack_identity_and_cards(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    replacement = tmp_path / "replacement.carddeck"
    export_pack(pack("alpha"), source / "packs_alpha.carddeck")
    export_pack(pack("beta"), replacement)
    with pytest.raises(ValueError, match="replacement pack_id"):
        rebuild_carddecks_v2.rebuild_directory(source, destination, {"packs_alpha.carddeck": replacement})
    assert not destination.exists()


def test_inventory_requires_exact_sources_and_hashes(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    archive = export_pack(pack("alpha"), source / "packs_alpha.carddeck")
    inventory = {archive.name: ("alpha", hashlib.sha256(archive.read_bytes()).hexdigest())}
    rebuild_carddecks_v2.rebuild_directory(source, destination, {}, inventory)
    assert validate_archive(destination / archive.name).metadata.id == "alpha"

    with pytest.raises(ValueError, match="source inventory mismatch"):
        rebuild_carddecks_v2.rebuild_directory(source, tmp_path / "missing", {}, {})
    bad = {archive.name: ("alpha", "0" * 64)}
    with pytest.raises(ValueError, match="source sha256"):
        rebuild_carddecks_v2.rebuild_directory(source, tmp_path / "bad", {}, bad)


def test_inventory_parser_rejects_malformed_and_duplicate_entries(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"inventory_version": 1, "archives": [
        {"archive": "one.carddeck", "pack_id": "one", "sha256": "0" * 64},
        {"archive": "one.carddeck", "pack_id": "one", "sha256": "0" * 64},
    ]}))
    with pytest.raises(ValueError, match="duplicate"):
        rebuild_carddecks_v2.load_inventory(path)


def test_bad_archive_leaves_no_partial_destination(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    export_pack(pack("alpha"), source / "packs_alpha.carddeck")
    (source / "packs_broken.carddeck").write_bytes(b"not a zip")

    with pytest.raises(Exception, match="cannot read archive"):
        rebuild_carddecks_v2.rebuild_directory(source, destination, {})

    assert not destination.exists()


def test_refuses_existing_destination_and_unknown_replacement(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    export_pack(pack("alpha"), source / "packs_alpha.carddeck")
    destination.mkdir()
    with pytest.raises(ValueError, match="destination already exists"):
        rebuild_carddecks_v2.rebuild_directory(source, destination, {})
    with pytest.raises(ValueError, match="no matching source"):
        rebuild_carddecks_v2.rebuild_directory(source, tmp_path / "other", {"missing.carddeck": source})
