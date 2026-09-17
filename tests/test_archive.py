from __future__ import annotations

import json
import zipfile

import pytest

from bad_decisions.archive import export_pack, import_pack, initialize_registry, validate_archive
from bad_decisions.errors import PackConfigurationError
from bad_decisions.packs import load_registry


def test_export_validate_and_import_round_trip(tmp_path):
    pack = load_registry().packs["maha"]
    archive = export_pack(pack, tmp_path / "maha.carddeck")
    assert validate_archive(archive) == pack
    with zipfile.ZipFile(archive) as contents:
        assert set(contents.namelist()) == {"manifest.json", "pack.json", "LICENSE.txt", "ATTRIBUTION.md"}
        manifest = json.loads(contents.read("manifest.json"))
        assert manifest["format"] == "carddeck"
        assert manifest["pack_id"] == "maha"
    registry = (tmp_path / "registry").resolve()
    imported = import_pack(archive, registry)
    assert imported == registry / "maha.json"
    with pytest.raises(PackConfigurationError, match="overwrite"):
        import_pack(archive, registry)


def test_import_requires_absolute_destination_and_never_overwrites(tmp_path):
    archive = export_pack(load_registry().packs["maha"], tmp_path / "maha.carddeck")
    with pytest.raises(PackConfigurationError, match="absolute"):
        import_pack(archive, "relative")
    registry = (tmp_path / "registry").resolve()
    import_pack(archive, registry)
    with pytest.raises(PackConfigurationError, match="overwrite"):
        import_pack(archive, registry)


def test_rejects_unexpected_or_unsafe_members(tmp_path):
    archive = tmp_path / "bad.carddeck"
    with zipfile.ZipFile(archive, "w") as contents:
        for name in ("manifest.json", "pack.json", "LICENSE.txt", "ATTRIBUTION.md", "../surprise"):
            contents.writestr(name, "{}")
    with pytest.raises(PackConfigurationError, match="exactly"):
        validate_archive(archive)


def test_rejects_the_biggest_blackest_zipbomb(tmp_path):
    archive = tmp_path / "the_biggest_blackest_zipbomb.carddeck"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as contents:
        contents.writestr("manifest.json", "{}")
        contents.writestr("pack.json", "{}")
        contents.writestr("LICENSE.txt", "x" * 100_000)
        contents.writestr("ATTRIBUTION.md", "attribution")
    with pytest.raises(PackConfigurationError, match="compression ratio"):
        validate_archive(archive)


def test_initialize_registry_requires_empty_absolute_directory(tmp_path):
    registry = (tmp_path / "registry").resolve()
    copied = initialize_registry(registry)
    assert [path.name for path in copied] == ["base.json", "coffee.json", "maha.json"]
    assert load_registry(registry).ids == ("base", "coffee", "maha")
    with pytest.raises(PackConfigurationError, match="non-empty"):
        initialize_registry(registry)


def test_rejects_non_carddeck_filename(tmp_path):
    with pytest.raises(PackConfigurationError, match=".carddeck"):
        export_pack(load_registry().packs["maha"], tmp_path / "maha.zip")
