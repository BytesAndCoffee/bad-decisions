"""Tests for the object-archive catalog indexer and its nginx templates."""

from __future__ import annotations

import hashlib
import http.client
import importlib.util
import io
import json
import logging
import re
import stat
import threading
import zipfile
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from bad_decisions import archive as client_limits
from bad_decisions import models as client_models

ARCHIVE_DIR = Path(__file__).resolve().parents[1] / "deploy" / "object-archive"


def _load_catalog():
    spec = importlib.util.spec_from_file_location("catalog_indexer", ARCHIVE_DIR / "indexer" / "catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


catalog = _load_catalog()


def pack_document(schema: int = 2) -> dict[str, object]:
    metadata = {
        "id": "x",
        "name": "Example",
        "description": "Example pack",
        "version": "1",
        "language": "en",
        "attribution": "attribution",
        "license_id": "Example",
        "license_notice": "license",
        "authors": ["Example Author"],
        "modifications": [],
    }
    if schema == 1:
        return {"schema_version": 1, "metadata": metadata, "black": [{"repr": "Why?", "template": "Why?"}], "white": [{"text": "Because."}]}
    return {
        "schema_version": 2,
        "metadata": metadata,
        "prompts": [{"text": "Why?", "template": "Why?"}],
        "answers": [{"text": "Because."}, {"text": "Regret."}],
    }


def build_archive(members: dict[str, bytes] | None = None, *, compression=zipfile.ZIP_DEFLATED, patch=None) -> bytes:
    supplied = members or {}
    contents = {
        "pack.json": json.dumps(pack_document()).encode(),
        "LICENSE.txt": b"license",
        "ATTRIBUTION.md": b"attribution",
    }
    contents.update(supplied)
    if "manifest.json" not in supplied:
        contents["manifest.json"] = json.dumps({
            "format": "carddeck",
            "format_version": 1,
            "pack_id": "x",
            "pack_sha256": hashlib.sha256(contents["pack.json"]).hexdigest(),
        }).encode()
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
    assert catalog.CONTROL.pattern == client_models._CONTROL.pattern
    assert catalog.PACK_ID.pattern == client_models.ID_PATTERN


def test_good_archive_is_listed():
    payload = make_catalog({"good.carddeck": build_archive()}).payload()
    assert payload["pack_count"] == 1
    assert payload["rejected_archives"] == []
    assert (payload["packs"][0]["prompt_count"], payload["packs"][0]["answer_count"]) == (1, 2)
    assert payload["schema_version"] == 2


def test_schema_1_archives_are_still_catalogued():
    v1 = pack_document(1)
    v1["black"].append({"repr": "What?", "template": "What?"})
    payload = make_catalog({"old.carddeck": build_archive({"pack.json": json.dumps(v1).encode()})}).payload()
    assert payload["rejected_archives"] == []
    assert (payload["packs"][0]["prompt_count"], payload["packs"][0]["answer_count"]) == (2, 1)


@pytest.mark.parametrize("member", ["LICENSE.txt", "ATTRIBUTION.md"])
def test_license_documents_must_be_utf8(member):
    payload = make_catalog({"bad.carddeck": build_archive({member: b"\xff"})}).payload()
    assert payload["packs"] == []
    assert payload["rejected_archives"][0]["object_key"] == "bad.carddeck"


@pytest.mark.parametrize(
    ("member", "replacement", "expected"),
    [
        ("LICENSE.txt", b"different", "LICENSE.txt"),
        ("ATTRIBUTION.md", b"different", "ATTRIBUTION.md"),
    ],
)
def test_license_documents_must_match_metadata_exactly(member, replacement, expected):
    payload = make_catalog({"bad.carddeck": build_archive({member: replacement})}).payload()
    assert payload["packs"] == []
    assert expected in payload["rejected_archives"][0]["error"]


@pytest.mark.parametrize(
    ("schema", "target"),
    [
        (2, "prompt-text"),
        (2, "prompt-template"),
        (2, "answer-text"),
        (1, "prompt-repr"),
        (1, "prompt-template"),
        (1, "answer-text"),
        (2, "metadata-string"),
        (2, "metadata-list"),
    ],
)
def test_control_characters_are_rejected_in_printed_pack_text(schema, target):
    pack = pack_document(schema)
    prompts = pack["black"] if schema == 1 else pack["prompts"]
    answers = pack["white"] if schema == 1 else pack["answers"]
    if target == "prompt-text":
        prompts[0]["text"] = "bad\x00text"
    elif target == "prompt-repr":
        prompts[0]["repr"] = "bad\x00text"
    elif target == "prompt-template":
        prompts[0]["template"] = "bad\x00text"
    elif target == "answer-text":
        answers[0]["text"] = "bad\x7ftext"
    elif target == "metadata-string":
        pack["metadata"]["name"] = "bad\x00name"
    else:
        pack["metadata"]["authors"] = ["bad\x00author"]
    body = build_archive({"pack.json": json.dumps(pack).encode()})
    payload = make_catalog({"bad.carddeck": body}).payload()
    assert payload["packs"] == []
    assert "forbidden control character" in payload["rejected_archives"][0]["error"]


def test_tabs_and_newlines_remain_allowed_in_printed_pack_text():
    pack = pack_document()
    pack["metadata"]["description"] = "line one\nline two\tstill printable"
    body = build_archive({"pack.json": json.dumps(pack).encode()})
    assert make_catalog({"good.carddeck": body}).payload()["pack_count"] == 1


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ({"format": "carddeck", "format_version": 1, "pack_id": "x", "pack_sha256": "0" * 64}, "checksum"),
        ({"format": "carddeck", "format_version": 1, "pack_id": "other", "pack_sha256": None}, "pack_id"),
        ({"format": "carddeck", "format_version": 2, "pack_id": "x", "pack_sha256": None}, "format or version"),
        ({"format": "carddeck", "format_version": 1, "pack_id": "x", "pack_sha256": None, "extra": True}, "unexpected"),
    ],
)
def test_manifest_integrity_and_identity_are_validated(manifest, expected):
    pack_bytes = json.dumps(pack_document()).encode()
    manifest = dict(manifest)
    if manifest.get("pack_sha256") is None:
        manifest["pack_sha256"] = hashlib.sha256(pack_bytes).hexdigest()
    body = build_archive({"pack.json": pack_bytes, "manifest.json": json.dumps(manifest).encode()})
    payload = make_catalog({"bad.carddeck": body}).payload()
    assert payload["packs"] == []
    assert expected in payload["rejected_archives"][0]["error"]


def test_empty_license_documents_are_rejected():
    payload = make_catalog({"bad.carddeck": build_archive({"LICENSE.txt": b" \n"})}).payload()
    assert payload["packs"] == []
    assert "must not be empty" in payload["rejected_archives"][0]["error"]


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
    "cards-not-list": (lambda: build_archive({"pack.json": b'{"schema_version": 2, "metadata": {}, "prompts": 3}'}), "JSON arrays"),
    "v1-cards-not-list": (lambda: build_archive({"pack.json": b'{"schema_version": 1, "metadata": {}, "black": 3}'}), "JSON arrays"),
    "no-schema-version": (lambda: build_archive({"pack.json": b'{"metadata": {}, "prompts": []}'}), "schema_version 1 or 2"),
    "boolean-schema-version": (lambda: build_archive({"pack.json": b'{"schema_version": true, "metadata": {}}'}), "schema_version 1 or 2"),
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


@pytest.mark.parametrize("error", [zlib.error("bad"), RuntimeError("bad"), TypeError("bad"), EOFError("truncated deflate")])
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


class ClientError(Exception):
    """Stands in for botocore.exceptions.ClientError (boto3 is not a test dependency)."""


@pytest.mark.parametrize("error", [OSError("bad"), ConnectionResetError("reset"), TimeoutError("slow"), ClientError("SlowDown")])
def test_storage_errors_propagate_instead_of_rejecting_archives(error):
    instance = make_catalog({"a.carddeck": build_archive(), "b.carddeck": build_archive()}, fail=error)
    with pytest.raises(type(error)):
        instance.payload()
    assert not issubclass(type(error), catalog.ARCHIVE_ERRORS)


def test_truncated_archive_is_rejected_not_fatal():
    good = build_archive({"pack.json": b"{" + b'"a":1,' * 200 + b'"z":1}'})
    with zipfile.ZipFile(io.BytesIO(good)) as archive:
        info = archive.getinfo("pack.json")
    end = info.header_offset + 30 + len(info.filename) + len(info.extra) + info.compress_size // 2
    # Cut the archive mid-member: the central directory is lost, so this must be a clean rejection.
    payload = make_catalog({"a-cut.carddeck": good[:end], "b-good.carddeck": build_archive()}).payload()
    assert [pack["object_key"] for pack in payload["packs"]] == ["b-good.carddeck"]
    assert payload["rejected_archives"][0]["object_key"] == "a-cut.carddeck"


def test_storage_outage_serves_the_stale_catalog_not_a_rejection_list():
    clock = Clock()
    instance = make_catalog({"good.carddeck": build_archive()})
    cache = catalog.PayloadCache(instance.payload, 60, clock)
    good, _ = cache.get()
    assert json.loads(good)["pack_count"] == 1
    instance.client.fail = ClientError("AccessDenied")
    clock.now = 61
    body, remaining = cache.get()
    assert body == good and remaining == 0
    assert json.loads(body)["rejected_archives"] == []


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
    body, remaining = cache.get()
    assert (json.loads(body), remaining) == ({"n": 1}, 60)
    clock.now = 59
    body, remaining = cache.get()
    assert (json.loads(body), remaining) == ({"n": 1}, 1)
    clock.now = 61
    assert json.loads(cache.get()[0]) == {"n": 2}
    assert len(calls) == 2


def test_cache_serves_stale_when_refresh_fails_and_retries_soon():
    clock, state = Clock(), {"fail": False, "calls": 0}

    def build():
        state["calls"] += 1
        if state["fail"]:
            raise OSError("storage down")
        return {"ok": True}

    cache = catalog.PayloadCache(build, 60, clock)
    good, _ = cache.get()
    state["fail"] = True
    clock.now = 61
    assert cache.get() == (good, 0)
    assert cache.get() == (good, 0)  # still stale inside the hold-off window
    assert state["calls"] == 2  # one failed refresh, then held off
    clock.now = 61 + catalog.STALE_RETRY_SECONDS
    state["fail"] = False
    assert cache.get() == (good, 60)
    assert state["calls"] == 3


def test_cache_propagates_failure_when_nothing_is_cached():
    def build():
        raise OSError("storage down")

    with pytest.raises(OSError):
        catalog.PayloadCache(build, 60, Clock()).get()


def test_cold_failure_is_negative_cached_across_threads_then_retried():
    clock, calls = Clock(), []
    barrier = threading.Barrier(8)

    def build():
        calls.append(1)
        raise ConnectionError("storage down")

    cache = catalog.PayloadCache(build, 60, clock)
    outcomes = []

    def request():
        barrier.wait(5)
        try:
            cache.get()
        except ConnectionError as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=request) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len(outcomes) == 8
    assert len(calls) == 1
    clock.now = catalog.STALE_RETRY_SECONDS - 1
    with pytest.raises(ConnectionError):
        cache.get()
    assert len(calls) == 1
    clock.now = catalog.STALE_RETRY_SECONDS
    with pytest.raises(ConnectionError):
        cache.get()
    assert len(calls) == 2


def test_cold_failure_hold_off_is_capped_by_a_short_ttl_and_clears_on_success():
    clock, state = Clock(), {"fail": True, "calls": 0}

    def build():
        state["calls"] += 1
        if state["fail"]:
            raise OSError("down")
        return {"ok": True}

    cache = catalog.PayloadCache(build, 3, clock)
    with pytest.raises(OSError):
        cache.get()
    clock.now = 3
    state["fail"] = False
    body, remaining = cache.get()  # retried after min(ttl, retry), not after 10s
    assert (json.loads(body), remaining, state["calls"]) == ({"ok": True}, 3, 2)


def test_expiry_is_measured_from_the_end_of_a_slow_build():
    clock = Clock()

    def slow_build():
        clock.now += 30
        return {"ok": True}

    cache = catalog.PayloadCache(slow_build, 60, clock)
    cache.get()
    assert cache._expires_at == 90
    clock.now = 89
    _, remaining = cache.get()
    assert remaining == 1

    def slow_failure():
        clock.now += 30
        raise OSError("down")

    cache._build = slow_failure
    clock.now = 91
    assert cache.get()[1] == 0
    assert cache._expires_at == 121 + catalog.STALE_RETRY_SECONDS


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
    assert len(results) == 8 and len({body for body, _ in results}) == 1


def test_cache_ttl_default_and_valid_values(monkeypatch):
    monkeypatch.delenv("CATALOG_CACHE_TTL", raising=False)
    assert catalog.cache_ttl() == catalog.DEFAULT_CACHE_TTL
    monkeypatch.setenv("CATALOG_CACHE_TTL", "30")
    assert catalog.cache_ttl() == 30
    monkeypatch.setenv("CATALOG_CACHE_TTL", "0")
    assert catalog.cache_ttl() == 0


@pytest.mark.parametrize("value", ["abc", "-1", "nan", "inf", "-inf", "1e999", "6o"])
def test_cache_ttl_rejects_bad_values(monkeypatch, value):
    monkeypatch.setenv("CATALOG_CACHE_TTL", value)
    with pytest.raises(RuntimeError, match="CATALOG_CACHE_TTL"):
        catalog.cache_ttl()


@contextmanager
def serve(cache):
    handler = type("TestHandler", (catalog.Handler,), {"cache": cache})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def fetch(path="/packs/index", method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, response.getheader("Cache-Control"), response.read()
        finally:
            connection.close()

    try:
        yield fetch
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_cache_control_reflects_remaining_freshness_and_stale_is_no_cache():
    clock, state = Clock(), {"fail": False}

    def build():
        if state["fail"]:
            raise OSError("storage down")
        return {"ok": True}

    with serve(catalog.PayloadCache(build, 60, clock)) as fetch:
        status, cache_control, body = fetch()
        assert (status, cache_control, json.loads(body)) == (200, "public, max-age=60", {"ok": True})
        clock.now = 20
        assert fetch()[1] == "public, max-age=40"
        clock.now = 59.5  # under one second left must not advertise a lifetime
        assert fetch()[1] == "no-cache"
        state["fail"] = True
        clock.now = 61
        status, cache_control, body = fetch()
        assert (status, cache_control, json.loads(body)) == (200, "no-cache", {"ok": True})
        clock.now = 65  # still inside the stale hold-off window
        assert fetch()[1] == "no-cache"
        assert fetch("/other")[0] == 404
        status, cache_control, body = fetch(method="HEAD")
        assert (status, cache_control, body) == (200, "no-cache", b"")


def test_zero_ttl_is_never_cached_downstream():
    with serve(catalog.PayloadCache(lambda: {"ok": True}, 0, Clock())) as fetch:
        assert fetch()[:2] == (200, "no-cache")


def test_cold_failure_is_503_and_logged(caplog):
    def build():
        raise ClientError("storage down")

    with serve(catalog.PayloadCache(build, 60, Clock())) as fetch, caplog.at_level(logging.ERROR, logger="catalog"):
        assert fetch()[0] == 503
    assert any(record.exc_info and record.exc_info[0] is ClientError for record in caplog.records)


def test_startup_fails_fast_on_bad_configuration(monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise AssertionError("must not get past configuration validation")

    monkeypatch.setattr(catalog, "Catalog", unreachable)
    monkeypatch.setattr(catalog, "ThreadingHTTPServer", unreachable)
    monkeypatch.setenv("CATALOG_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("CATALOG_BIND_PORT", "8080")
    monkeypatch.setenv("CATALOG_CACHE_TTL", "abc")
    with pytest.raises(RuntimeError, match="CATALOG_CACHE_TTL"):
        catalog.main()
    monkeypatch.setenv("CATALOG_CACHE_TTL", "-1")
    with pytest.raises(RuntimeError, match="CATALOG_CACHE_TTL"):
        catalog.main()
    monkeypatch.setenv("CATALOG_CACHE_TTL", "30")
    monkeypatch.delenv("CATALOG_BIND_HOST")
    with pytest.raises(RuntimeError, match="CATALOG_BIND_HOST"):
        catalog.main()


def test_main_wires_the_validated_ttl_into_the_handler_cache(monkeypatch):
    seen = {}

    class FakeCatalog:
        def payload(self):
            return {"ok": True}

    class FakeServer:
        def __init__(self, address, handler):
            seen["address"], seen["handler"] = address, handler

        def serve_forever(self):
            seen["served"] = True

    monkeypatch.setattr(catalog, "Catalog", FakeCatalog)
    monkeypatch.setattr(catalog, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(catalog.Handler, "cache", None, raising=False)
    monkeypatch.setenv("CATALOG_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("CATALOG_BIND_PORT", "8080")
    monkeypatch.setenv("CATALOG_CACHE_TTL", "30")
    catalog.main()
    assert seen["address"] == ("127.0.0.1", 8080) and seen["served"]
    assert catalog.Handler.cache._ttl == 30
    assert json.loads(catalog.Handler.cache.get()[0]) == {"ok": True}


def test_module_imports_without_boto3_or_bad_decisions():
    source = (ARCHIVE_DIR / "indexer" / "catalog.py").read_text()
    top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not any("boto" in line or "bad_decisions" in line for line in top_level)


def render(path: Path) -> str:
    text = path.read_text()
    for name, value in {"OBJECT_ARCHIVE_DOMAIN": "example.test", "GARAGE_WEB_UPSTREAM": "100.64.0.2:3902", "CATALOG_UPSTREAM": "127.0.0.1:8080", "CATALOG_CACHE_DIR": "/var/cache/nginx/catalog"}.items():
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


def test_public_archive_proxy_is_read_only_and_rate_limited():
    http = render(ARCHIVE_DIR / "nginx-carddeck-catalog.http.conf.template")
    server = render(ARCHIVE_DIR / "nginx-object-archive.conf.template")
    assert "zone=carddeck_archive_rl:10m rate=10r/s" in http
    assert "limit_req_zone" not in server
    assert server.count("limit_except GET HEAD { deny all; }") == 2
    assert server.count("limit_req zone=carddeck_archive_rl burst=30 nodelay;") == 2
    assert server.count("limit_req_status 429;") == 2
    assert server.count("client_max_body_size 1m;") == 2
    assert "client_max_body_size 0;" not in server
    assert "proxy_set_header Host $host;" in server
    # Uploaded objects must not be content-sniffed into something executable.
    assert server.count("add_header X-Content-Type-Options nosniff always;") == 1
    # Rollback copies (including an archive with an unspecified license) must not be public.
    assert "location ^~ /rollback/ {\n        return 404;\n    }" in server


def test_public_catalog_proxy_is_read_only_and_body_limited():
    server = render(ARCHIVE_DIR / "nginx-carddeck-catalog.conf.template")
    assert server.count("limit_except GET HEAD { deny all; }") == 2
    assert server.count("client_max_body_size 1m;") == 2
    assert server.count("limit_req zone=carddeck_catalog_rl burst=5 nodelay;") == 2
    assert server.count("limit_req_status 429;") == 2
    assert server.count("add_header X-Content-Type-Options nosniff always;") == 1


def test_catalog_container_is_read_only_and_drops_privileges():
    compose = (ARCHIVE_DIR / "docker-compose.catalog-v3.yml").read_text()
    assert "read_only: true" in compose
    assert "cap_drop: [ALL]" in compose
    assert "security_opt: [no-new-privileges:true]" in compose
    assert 'PYTHONDONTWRITEBYTECODE: "1"' in compose
    assert "network_mode: host" in compose and "CATALOG_BIND_HOST: ${GARAGE_TAILNET_IP}" in compose


def test_non_object_metadata_is_rejected_without_assert():
    # python -O strips asserts; the metadata type check must be a real error.
    with pytest.raises(ValueError, match="metadata must be a JSON object"):
        catalog.validate_visible_text({"metadata": []}, [], [], 2)


def test_garage_cannot_gain_privileges():
    compose = (ARCHIVE_DIR / "docker-compose.yml").read_text()
    assert "security_opt: [no-new-privileges:true]" in compose
    assert all(line.strip().startswith('- "${GARAGE_TAILNET_IP}:') for line in compose.splitlines() if line.strip().startswith('- "') and ":39" in line), "ports bind only to the Tailnet address"
