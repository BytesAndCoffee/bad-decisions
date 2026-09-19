from __future__ import annotations

import errno
import json
import os
import threading
import zipfile

import pytest

import bad_decisions.archive as archive_module
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


def _registry_files(registry):
    return sorted(path.name for path in registry.iterdir())


def test_concurrent_imports_of_one_pack_yield_exactly_one_winner(tmp_path):
    archive = export_pack(load_registry().packs["maha"], tmp_path / "maha.carddeck")
    registry = (tmp_path / "registry").resolve()
    workers = 8
    barrier = threading.Barrier(workers)
    outcomes: list[object] = []

    def race():
        barrier.wait()
        try:
            outcomes.append(import_pack(archive, registry))
        except PackConfigurationError as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=race) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    losers = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(winners) == 1 and len(losers) == workers - 1
    assert all("overwrite" in str(loser) for loser in losers)
    assert _registry_files(registry) == ["maha.json"]  # no leftover temp files
    assert load_registry(registry).packs["maha"] == load_registry().packs["maha"]


def test_import_never_touches_an_existing_destination(tmp_path):
    archive = export_pack(load_registry().packs["maha"], tmp_path / "maha.carddeck")
    registry = (tmp_path / "registry").resolve()
    registry.mkdir()
    (registry / "maha.json").write_text("precious", encoding="utf-8")
    with pytest.raises(PackConfigurationError, match="overwrite"):
        import_pack(archive, registry)
    assert (registry / "maha.json").read_text(encoding="utf-8") == "precious"
    assert _registry_files(registry) == ["maha.json"]


def test_import_refuses_a_dangling_symlink_destination(tmp_path):
    archive = export_pack(load_registry().packs["maha"], tmp_path / "maha.carddeck")
    registry = (tmp_path / "registry").resolve()
    registry.mkdir()
    victim = tmp_path / "victim.json"
    (registry / "maha.json").symlink_to(victim)
    with pytest.raises(PackConfigurationError, match="overwrite"):
        import_pack(archive, registry)
    assert not victim.exists()
    assert _registry_files(registry) == ["maha.json"]


def test_import_fsyncs_the_file_and_the_directory(tmp_path, monkeypatch):
    archive = export_pack(load_registry().packs["maha"], tmp_path / "maha.carddeck")
    registry = (tmp_path / "registry").resolve()
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda descriptor: (synced.append(descriptor), real_fsync(descriptor))[1])
    import_pack(archive, registry)
    assert len(synced) >= 2  # temporary file, then the registry directory (POSIX)


def test_import_falls_back_to_exclusive_create_without_hard_links(tmp_path, monkeypatch):
    archive = export_pack(load_registry().packs["maha"], tmp_path / "maha.carddeck")
    registry = (tmp_path / "registry").resolve()

    def no_links(*_args, **_kwargs):
        raise OSError(errno.EPERM, "hard links are not supported")

    monkeypatch.setattr(os, "link", no_links)
    imported = import_pack(archive, registry)
    assert load_registry(registry).packs["maha"] == load_registry().packs["maha"]
    assert imported.stat().st_mode & 0o777 == 0o644
    assert _registry_files(registry) == ["maha.json"]
    with pytest.raises(PackConfigurationError, match="overwrite"):
        import_pack(archive, registry)
    assert _registry_files(registry) == ["maha.json"]


def test_fallback_never_overwrites_and_removes_its_partial_file(tmp_path, monkeypatch):
    destination = tmp_path / "maha.json"
    destination.write_text("precious", encoding="utf-8")
    with pytest.raises(FileExistsError):
        archive_module._create_exclusive(destination, b"new")
    assert destination.read_text(encoding="utf-8") == "precious"

    partial = tmp_path / "partial.json"
    monkeypatch.setattr(os, "fsync", lambda _descriptor: (_ for _ in ()).throw(OSError(errno.EIO, "disk on fire")))
    with pytest.raises(OSError, match="disk on fire"):
        archive_module._create_exclusive(partial, b"new")
    assert not partial.exists()


def test_other_link_errors_are_reported_and_leave_no_files(tmp_path, monkeypatch):
    archive = export_pack(load_registry().packs["maha"], tmp_path / "maha.carddeck")
    registry = (tmp_path / "registry").resolve()

    def full_disk(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(os, "link", full_disk)
    with pytest.raises(PackConfigurationError, match="cannot import pack"):
        import_pack(archive, registry)
    assert _registry_files(registry) == []
