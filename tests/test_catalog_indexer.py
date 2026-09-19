"""Tests for the object-archive catalog indexer and its nginx templates."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import stat
import threading
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bad_decisions import archive as client_limits

ARCHIVE_DIR = Path(__file__).resolve().parents[1] / "deploy" / "object-archive"


def _load_catalog():
    spec = importlib.util.spec_from_file_location("catalog_indexer", ARCHIVE_DIR / "indexer" / "catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


catalog = _load_catalog()


def build_archive(members: dict[str, bytes] | None = None, *, compression=zipfile.ZIP_DEFLATED, patch=None) -> bytes:
    contents = {
        "manifest.json": json.dumps({"format": "carddeck", "format_version": 1, "pack_id": "x", "pack_sha256": "0" * 64}).encode(),
        "pack.json": json.dumps({"metadata": {"id": "x"}, "black": [{}], "white": [{}, {}]}).encode(),
        "LICENSE.txt": b"license",
        "ATTRIBUTION.md": b"attribution",
    }
    contents.update(members or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            if patch:
                patch(info)
            archive.writestr(info, data)
    return buffer.getvalue()


class FakeClient:
    def __init__(self, objects: dict[str, bytes], fail: BaseException | None = None):
        self.objects = objects
        self.fail = fail

    def get_paginator(self, _name):
        objects = self.objects

        class Paginator:
            def paginate(self, Bucket):  # noqa: N803
                contents = [{"Key": key, "Size": len(body), "LastModified": datetime(2026, 1, 1, tzinfo=timezone.utc), "ETag": '"e"'} for key, body in objects.items()]
                yield {"Contents": contents}

        return Paginator()

    def get_object(self, Bucket, Key):  # noqa: N803
        if self.fail:
            raise self.fail
        return {"Body": io.BytesIO(self.objects[Key])}


def make_catalog(objects: dict[str, bytes], **kwargs) -> catalog.Catalog:
    instance = catalog.Catalog.__new__(catalog.Catalog)
    instance.buckets = ("bucket",)
    instance.object_domain = "example.test"
    instance.client = FakeClient(objects, **kwargs)
    return instance


def test_limits_match_the_importer():
    assert catalog.MAX_MEMBER_BYTES == client_limits.MAX_MEMBER_BYTES
    assert catalog.MAX_ARCHIVE_BYTES == client_limits.MAX_ARCHIVE_BYTES == 5 * 1024 * 1024
    assert catalog.MAX_COMPRESSION_RATIO == client_limits.MAX_COMPRESSION_RATIO
    assert catalog.REQUIRED_MEMBERS == client_limits.REQUIRED_MEMBERS


def test_good_archive_is_listed():
    payload = make_catalog({"good.carddeck": build_archive()}).payload()
    assert payload["pack_count"] == 1
    assert payload["rejected_archives"] == []
    assert (payload["packs"][0]["black_card_count"], payload["packs"][0]["white_card_count"]) == (1, 2)


def corrupt_deflate() -> bytes:
    data = bytearray(build_archive({"pack.json": b"{" + b'"a":1,' * 200 + b'"z":1}'}))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
        info = archive.getinfo("pack.json")
    start = info.header_offset + 30 + len(info.filename) + len(info.extra)
    data[start : start + info.compress_size] = b"\xff" * info.compress_size
    return bytes(data)


def symlink_archive() -> bytes:
    return build_archive(patch=lambda info: setattr(info, "external_attr", (stat.S_IFLNK | 0o777) << 16) if info.filename == "LICENSE.txt" else None)


def encrypted_archive() -> bytes:
    # zipfile clears the flag when writing, so set the "encrypted" bit in the raw bytes.
    data = bytearray(build_archive())
    for signature, offset in ((b"PK\x01\x02", 8), (b"PK\x03\x04", 6)):
        position = data.find(signature)
        while position != -1:
            data[position + offset] |= 0x1
            position = data.find(signature, position + 4)
    return bytes(data)


def duplicate_archive() -> bytes:
    buffer = io.BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in ("manifest.json", "pack.json", "LICENSE.txt", "LICENSE.txt"):
                archive.writestr(name, b"{}")
    return buffer.getvalue()


HOSTILE = {
    "corrupt-deflate": (corrupt_deflate, None),
    "symlink": (symlink_archive, "symbolic links"),
    "encrypted": (encrypted_archive, "encrypted"),
    "duplicate-member": (duplicate_archive, "unexpected"),
    "extra-member": (lambda: build_archive({"extra.txt": b"x"}), "unexpected"),
    "traversal-member": (lambda: build_archive({"../evil": b"x"}), "unexpected"),
    "ratio-bomb": (lambda: build_archive({"LICENSE.txt": b"x" * 300_000}), "compression ratio"),
    "oversized-member": (lambda: build_archive({"LICENSE.txt": _random(catalog.MAX_MEMBER_BYTES + 1)}, compression=zipfile.ZIP_STORED), "too large"),
    "pack-is-list": (lambda: build_archive({"pack.json": b"[]"}), "JSON objects"),
    "metadata-not-object": (lambda: build_archive({"pack.json": b'{"metadata": 3}'}), "JSON objects"),
    "cards-not-list": (lambda: build_archive({"pack.json": b'{"metadata": {}, "black": 3}'}), "JSON arrays"),
    "manifest-not-json": (lambda: build_archive({"manifest.json": b"\xff\xfe"}), None),
    "not-a-zip": (lambda: b"this is not a zip", None),
}


def _random(size: int) -> bytes:
    import os

    return os.urandom(size)


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_archive_is_rejected_and_good_archive_survives(name):
    build, expected = HOSTILE[name]
    payload = make_catalog({"a-bad.carddeck": build(), "b-good.carddeck": build_archive()}).payload()
    assert [pack["object_key"] for pack in payload["packs"]] == ["b-good.carddeck"]
    (rejected,) = payload["rejected_archives"]
    assert rejected["object_key"] == "a-bad.carddeck"
    if expected:
        assert re.search(expected, rejected["error"])


def test_total_uncompressed_size_is_capped():
    chunk = _random(catalog.MAX_MEMBER_BYTES - 1)
    body = build_archive({"manifest.json": chunk, "pack.json": chunk, "LICENSE.txt": chunk}, compression=zipfile.ZIP_STORED)
    with pytest.raises(ValueError, match="too large"):
        catalog.read_members(body)


@pytest.mark.parametrize("error", [zlib.error("bad"), RuntimeError("bad"), TypeError("bad"), OSError("bad")])
def test_unexpected_per_archive_errors_do_not_fail_the_catalog(error):
    class Flaky(catalog.Catalog):
        def pack(self, bucket, key, listed):
            if key == "a-bad.carddeck":
                raise error
            return super().pack(bucket, key, listed)

    instance = make_catalog({"a-bad.carddeck": b"", "b-good.carddeck": build_archive()})
    instance.__class__ = Flaky
    payload = instance.payload()
    assert payload["pack_count"] == 1
    assert payload["rejected_archives"][0]["object_key"] == "a-bad.carddeck"


def test_archive_over_the_shared_size_limit_is_rejected():
    big = b"\0" * (client_limits.MAX_ARCHIVE_BYTES + 1)
    payload = make_catalog({"big.carddeck": big}).payload()
    assert payload["packs"] == []
    assert "catalog limit" in payload["rejected_archives"][0]["error"]


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def test_cache_serves_within_ttl_and_refreshes_after():
    clock, calls = Clock(), []

    def build():
        calls.append(1)
        return {"n": len(calls)}

    cache = catalog.PayloadCache(build, 60, clock)
    assert json.loads(cache.get()) == {"n": 1}
    clock.now = 59
    assert json.loads(cache.get()) == {"n": 1}
    clock.now = 61
    assert json.loads(cache.get()) == {"n": 2}
    assert len(calls) == 2


def test_cache_serves_stale_when_refresh_fails_and_retries_soon():
    clock, state = Clock(), {"fail": False, "calls": 0}

    def build():
        state["calls"] += 1
        if state["fail"]:
            raise OSError("storage down")
        return {"ok": True}

    cache = catalog.PayloadCache(build, 60, clock)
    good = cache.get()
    state["fail"] = True
    clock.now = 61
    assert cache.get() == good
    assert cache.get() == good
    assert state["calls"] == 2  # one failed refresh, then held off
    clock.now = 61 + catalog.STALE_RETRY_SECONDS
    state["fail"] = False
    assert cache.get() == good
    assert state["calls"] == 3


def test_cache_propagates_failure_when_nothing_is_cached():
    def build():
        raise OSError("storage down")

    with pytest.raises(OSError):
        catalog.PayloadCache(build, 60, Clock()).get()


def test_concurrent_requests_share_one_scan():
    started, release, calls = threading.Event(), threading.Event(), []

    def build():
        calls.append(1)
        started.set()
        release.wait(5)
        return {"ok": True}

    cache = catalog.PayloadCache(build, 60, Clock())
    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get())) for _ in range(8)]
    for thread in threads:
        thread.start()
    assert started.wait(5)
    release.set()
    for thread in threads:
        thread.join(5)
    assert len(calls) == 1
    assert len(results) == 8 and len(set(results)) == 1


def render(path: Path) -> str:
    text = path.read_text()
    for name, value in {"OBJECT_ARCHIVE_DOMAIN": "example.test", "CATALOG_UPSTREAM": "127.0.0.1:8080", "CATALOG_CACHE_DIR": "/var/cache/nginx/catalog"}.items():
        text = text.replace(f"@{name}@", value)
    assert "@" not in re.sub(r"#.*", "", text)
    return text


def test_nginx_templates_split_http_and_server_directives():
    http = render(ARCHIVE_DIR / "nginx-carddeck-catalog.http.conf.template")
    server = render(ARCHIVE_DIR / "nginx-carddeck-catalog.conf.template")
    assert "limit_req_zone" in http and "proxy_cache_path" in http
    assert "limit_req_zone" not in server and "proxy_cache_path" not in server
    zone = re.search(r"limit_req_zone \S+ zone=(\w+):", http).group(1)
    cache = re.search(r"keys_zone=(\w+):", http).group(1)
    location = re.search(r"location = /packs/index \{(.*?)\n    \}", server, re.S).group(1)
    assert f"limit_req zone={zone}" in location
    assert "limit_req_status 429" in location
    assert f"proxy_cache {cache};" in location
    assert "proxy_cache_valid" in location and "proxy_cache_lock on" in location
