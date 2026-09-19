from __future__ import annotations

import json

import pytest

from bad_decisions.archive import export_pack
from bad_decisions.errors import PackConfigurationError
from bad_decisions.packs import load_registry
from bad_decisions.remote import _check_url, import_index, import_url


def test_import_url_validates_before_atomic_install(tmp_path, sample_pack, monkeypatch):
    archive = export_pack(sample_pack, tmp_path / "sample.carddeck")
    monkeypatch.setattr("bad_decisions.remote._read_url", lambda *_args, **_kwargs: archive.read_bytes())

    target = import_url("https://objects.example/packs/sample.carddeck", tmp_path / "registry")

    assert target.name == f"{sample_pack.metadata.id}.json"


def test_import_index_imports_selected_packs_and_rejects_duplicates(tmp_path, sample_pack, monkeypatch):
    archive = export_pack(sample_pack, tmp_path / "sample.carddeck")
    index_url = "https://catalog.example/packs/index"
    archive_url = "https://objects.example/packs/sample.carddeck"
    index = json.dumps({"schema_version": 1, "packs": [{"archive": {"pack_id": sample_pack.metadata.id}, "url": archive_url}]}).encode()

    monkeypatch.setattr("bad_decisions.remote._read_url", lambda url, **_kwargs: index if url == index_url else archive.read_bytes())
    imported = import_index(index_url, tmp_path / "registry", pack_ids=[sample_pack.metadata.id])

    assert len(imported) == 1
    with pytest.raises(PackConfigurationError, match="selected only once"):
        import_index(index_url, tmp_path / "other", pack_ids=[sample_pack.metadata.id, sample_pack.metadata.id])


@pytest.mark.parametrize("url", ["http://objects.example/a.carddeck", "https://user@objects.example/a.carddeck", "https://objects.example/a.zip", "https://objects.example/a.carddeck#fragment"])
def test_remote_urls_must_be_https_clean_carddeck_urls(url):
    with pytest.raises(PackConfigurationError):
        _check_url(url, carddeck=True)


def _serve_catalog(monkeypatch, archives: dict[str, bytes]):
    index_url = "https://catalog.example/packs/index"
    urls = {pack_id: f"https://objects.example/packs/{pack_id}.carddeck" for pack_id in archives}
    index = json.dumps(
        {"schema_version": 1, "packs": [{"archive": {"pack_id": pack_id}, "url": url} for pack_id, url in urls.items()]}
    ).encode()
    by_url = {urls[pack_id]: data for pack_id, data in archives.items()}
    monkeypatch.setattr("bad_decisions.remote._read_url", lambda url, **_kwargs: index if url == index_url else by_url[url])
    return index_url


def _registry_files(registry):
    return sorted(path.name for path in registry.iterdir()) if registry.exists() else []


def test_import_index_installs_several_packs(tmp_path, monkeypatch):
    packs = load_registry().packs
    archives = {pack_id: export_pack(packs[pack_id], tmp_path / f"{pack_id}.carddeck").read_bytes() for pack_id in ("maha", "coffee")}
    registry = (tmp_path / "registry").resolve()
    imported = import_index(_serve_catalog(monkeypatch, archives), registry, pack_ids=["maha", "coffee"])
    assert [path.name for path in imported] == ["maha.json", "coffee.json"]
    assert _registry_files(registry) == ["coffee.json", "maha.json"]


def test_import_index_installs_nothing_when_a_later_archive_is_corrupt(tmp_path, monkeypatch):
    packs = load_registry().packs
    archives = {
        "maha": export_pack(packs["maha"], tmp_path / "maha.carddeck").read_bytes(),
        "coffee": b"PK\x03\x04 this is not a real archive",
    }
    registry = (tmp_path / "registry").resolve()
    with pytest.raises(PackConfigurationError):
        import_index(_serve_catalog(monkeypatch, archives), registry, pack_ids=["maha", "coffee"])
    assert _registry_files(registry) == []


def test_import_index_installs_nothing_when_a_catalog_id_lies(tmp_path, monkeypatch):
    packs = load_registry().packs
    archives = {
        "maha": export_pack(packs["maha"], tmp_path / "maha.carddeck").read_bytes(),
        "coffee": export_pack(packs["maha"], tmp_path / "again.carddeck").read_bytes(),  # not coffee
    }
    registry = (tmp_path / "registry").resolve()
    with pytest.raises(PackConfigurationError, match="does not match"):
        import_index(_serve_catalog(monkeypatch, archives), registry, pack_ids=["maha", "coffee"])
    assert _registry_files(registry) == []


def test_import_index_rolls_back_when_a_later_pack_already_exists(tmp_path, monkeypatch):
    packs = load_registry().packs
    archives = {pack_id: export_pack(packs[pack_id], tmp_path / f"{pack_id}.carddeck").read_bytes() for pack_id in ("maha", "coffee")}
    registry = (tmp_path / "registry").resolve()
    registry.mkdir()
    (registry / "coffee.json").write_text("precious", encoding="utf-8")
    with pytest.raises(PackConfigurationError, match="overwrite"):
        import_index(_serve_catalog(monkeypatch, archives), registry, pack_ids=["maha", "coffee"])
    assert _registry_files(registry) == ["coffee.json"]  # maha.json rolled back, coffee.json untouched
    assert (registry / "coffee.json").read_text(encoding="utf-8") == "precious"
