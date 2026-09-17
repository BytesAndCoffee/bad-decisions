from __future__ import annotations

import json

import pytest

from bad_decisions.archive import export_pack
from bad_decisions.errors import PackConfigurationError
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
