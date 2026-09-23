from __future__ import annotations

import errno
import json
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

import bad_decisions.archive as archive_module
from bad_decisions.archive import export_pack, import_pack, initialize_registry, validate_archive
from bad_decisions.errors import PackConfigurationError
from bad_decisions.models import BlackCard, Pack, PackMetadata, Source
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


def test_archive_round_trip_preserves_declared_content_rights(tmp_path):
    metadata = PackMetadata(
        id="rights-test",
        name="Rights test",
        description="Synthetic metadata-preservation fixture.",
        version="7",
        language="en",
        custom=True,
        authors=("Example Creator",),
        attribution="Credit Example Creator.",
        license_id="Example-License-1.0",
        license_url="https://example.invalid/licenses/1.0",
        license_notice="Example license notice.",
        sources=(
            Source(
                origin="https://example.invalid/source",
                edition="First edition",
                sha256="0" * 64,
                retrieved="2026-09-23",
                license_evidence="Creator declaration.",
            ),
        ),
        modifications=("Converted to CardDeck without changing card text.",),
    )
    pack = Pack(
        schema_version=1,
        metadata=metadata,
        black=(BlackCard(id="b1", repr="Why? ____", template="Why? {}", slots=1, pack="rights-test"),),
        white=(),
    )

    archive = export_pack(pack, tmp_path / "rights-test.carddeck")
    assert validate_archive(archive).metadata == metadata

    registry = (tmp_path / "registry").resolve()
    import_pack(archive, registry)
    assert load_registry(registry).packs["rights-test"].metadata == metadata


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


def _without_hard_links(monkeypatch):
    def no_links(*_args, **_kwargs):
        raise OSError(errno.EPERM, "hard links are not supported")

    monkeypatch.setattr(os, "link", no_links)


def _maha(tmp_path):
    return export_pack(load_registry().packs["maha"], tmp_path / "maha.carddeck"), (tmp_path / "registry").resolve()


def test_fallback_refuses_an_existing_pack_and_leaves_no_lock(tmp_path, monkeypatch):
    archive, registry = _maha(tmp_path)
    registry.mkdir()
    (registry / "maha.json").write_text("precious", encoding="utf-8")
    _without_hard_links(monkeypatch)
    with pytest.raises(PackConfigurationError, match="overwrite"):
        import_pack(archive, registry)
    assert (registry / "maha.json").read_text(encoding="utf-8") == "precious"
    assert _registry_files(registry) == ["maha.json"]  # no lock, no temporary file


def test_fallback_refuses_a_dangling_symlink_destination(tmp_path, monkeypatch):
    archive, registry = _maha(tmp_path)
    registry.mkdir()
    victim = tmp_path / "victim.json"
    (registry / "maha.json").symlink_to(victim)
    _without_hard_links(monkeypatch)
    with pytest.raises(PackConfigurationError, match="overwrite"):
        import_pack(archive, registry)
    assert not victim.exists()
    assert _registry_files(registry) == ["maha.json"]


def test_fallback_leaves_no_lock_or_pack_when_the_replace_fails(tmp_path, monkeypatch):
    archive, registry = _maha(tmp_path)
    _without_hard_links(monkeypatch)

    def broken(*_args, **_kwargs):
        raise OSError(errno.EIO, "disk on fire")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(PackConfigurationError, match="cannot import pack"):
        import_pack(archive, registry)
    assert _registry_files(registry) == []


def test_fallback_leaves_no_lock_when_the_publish_is_interrupted(tmp_path, monkeypatch):
    archive, registry = _maha(tmp_path)
    _without_hard_links(monkeypatch)

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        import_pack(archive, registry)
    assert _registry_files(registry) == []


def test_a_pre_existing_lock_stops_the_fallback_without_breaking_it(tmp_path, monkeypatch):
    archive, registry = _maha(tmp_path)
    registry.mkdir()
    lock = registry / ".maha.json.lock"
    lock.write_text("held by another importer", encoding="utf-8")
    _without_hard_links(monkeypatch)
    with pytest.raises(PackConfigurationError, match="stale lock") as failure:
        import_pack(archive, registry)
    assert "overwrite" not in str(failure.value)  # a distinct diagnosis, not a phantom overwrite
    assert lock.read_text(encoding="utf-8") == "held by another importer"  # age never breaks a lock
    assert _registry_files(registry) == [".maha.json.lock"]


def test_a_leftover_lock_is_never_loaded_as_a_pack(tmp_path, monkeypatch):
    archive, registry = _maha(tmp_path)
    _without_hard_links(monkeypatch)
    import_pack(archive, registry)
    lock = registry / ".maha.json.lock"
    lock.write_text('{"hostile": true}', encoding="utf-8")  # as a crashed importer would leave it
    assert not lock.name.endswith(".json")
    assert load_registry(registry).ids == ("maha",)


def test_fallback_readers_never_see_a_partial_pack(tmp_path, monkeypatch):
    archive, registry = _maha(tmp_path)
    registry.mkdir()
    _without_hard_links(monkeypatch)
    expected = load_registry().packs["maha"].model_dump(mode="json")
    seen: list[object] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            for path in registry.glob("*.json"):
                try:
                    seen.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError) as exc:  # a partial file would land here
                    seen.append(exc)

    watcher = threading.Thread(target=reader)
    watcher.start()
    try:
        import_pack(archive, registry)
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()
        watcher.join()
    assert seen  # the reader really did observe the published pack
    assert all(document == expected for document in seen)


def test_concurrent_fallback_imports_yield_exactly_one_winner(tmp_path, monkeypatch):
    archive, registry = _maha(tmp_path)
    _without_hard_links(monkeypatch)
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
    assert all("overwrite" in str(loser) or "stale lock" in str(loser) for loser in losers)
    assert _registry_files(registry) == ["maha.json"]  # no leftover locks or temp files
    assert load_registry(registry).packs["maha"] == load_registry().packs["maha"]


def test_a_failing_temporary_cleanup_does_not_fail_the_import(tmp_path, monkeypatch):
    archive, registry = _maha(tmp_path)
    real_unlink = Path.unlink

    def flaky(self, *args, **kwargs):
        if self.name.startswith(".carddeck-"):
            raise OSError(errno.EIO, "disk on fire")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky)
    imported = import_pack(archive, registry)
    assert imported.exists()
    assert load_registry(registry).ids == ("maha",)  # the stranded temporary file is not a pack


def test_the_publish_lock_name_cannot_be_mistaken_for_a_pack(tmp_path):
    lock = archive_module._lock_path(tmp_path / "maha.json")
    assert lock.name == ".maha.json.lock" and not lock.match("*.json")


def test_other_link_errors_are_reported_and_leave_no_files(tmp_path, monkeypatch):
    archive = export_pack(load_registry().packs["maha"], tmp_path / "maha.carddeck")
    registry = (tmp_path / "registry").resolve()

    def full_disk(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(os, "link", full_disk)
    with pytest.raises(PackConfigurationError, match="cannot import pack"):
        import_pack(archive, registry)
    assert _registry_files(registry) == []
