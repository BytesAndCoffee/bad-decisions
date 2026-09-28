from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("migrate_carddeck_collection", ROOT / "scripts/migrate_carddeck_collection.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class Body(BytesIO):
    pass


class MissingObject(Exception):
    response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeS3:
    def __init__(self, objects, fail_key=None):
        self.objects = dict(objects)
        self.fail_key = fail_key

    def get_object(self, *, Bucket, Key):
        return {"Body": Body(self.objects[(Bucket, Key)])}

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise MissingObject()
        return {}

    def copy_object(self, *, Bucket, Key, CopySource, **_values):
        self.objects[(Bucket, Key)] = self.objects[(CopySource["Bucket"], CopySource["Key"])]

    def put_object(self, *, Bucket, Key, Body, **_values):
        self.objects[(Bucket, Key)] = b"corrupt" if Key == self.fail_key else Body


def sha(value):
    return hashlib.sha256(value).hexdigest()


def files(tmp_path):
    old, new = b"old archive", b"new archive"
    archives = tmp_path / "archives"
    archives.mkdir()
    (archives / "packs_one.carddeck").write_bytes(new)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"pack_count": 1, "packs": [{
        "bucket": "bucket", "object_key": "packs/one.carddeck", "sha256": sha(old),
        "archive": {"pack_id": "one"},
    }]}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"archives": [{
        "archive": "packs_one.carddeck", "pack_id": "one",
        "source_sha256": sha(old), "sha256": sha(new),
    }]}))
    return old, new, catalog, manifest, archives


def test_dry_run_validates_without_writes(tmp_path):
    old, _new, catalog, manifest, archives = files(tmp_path)
    plan = module.build_plan(catalog, manifest, archives)
    client = FakeS3({("bucket", "packs/one.carddeck"): old})
    assert module.migrate(client, plan, "rollback/test", apply=False) == 1
    assert set(client.objects) == {("bucket", "packs/one.carddeck")}


def test_apply_keeps_verified_rollback_copy(tmp_path):
    old, new, catalog, manifest, archives = files(tmp_path)
    plan = module.build_plan(catalog, manifest, archives)
    client = FakeS3({("bucket", "packs/one.carddeck"): old})
    assert module.migrate(client, plan, "rollback/test", apply=True) == 1
    assert client.objects[("bucket", "packs/one.carddeck")] == new
    assert client.objects[("bucket", "rollback/test/packs/one.carddeck.v1-backup")] == old


def test_failure_restores_changed_object(tmp_path):
    old, _new, catalog, manifest, archives = files(tmp_path)
    plan = module.build_plan(catalog, manifest, archives)
    client = FakeS3({("bucket", "packs/one.carddeck"): old}, fail_key="packs/one.carddeck")
    with pytest.raises(ValueError, match="replacement failed verification"):
        module.migrate(client, plan, "rollback/test", apply=True)
    assert client.objects[("bucket", "packs/one.carddeck")] == old


def test_apply_refuses_to_overwrite_a_rollback_copy(tmp_path):
    old, _new, catalog, manifest, archives = files(tmp_path)
    plan = module.build_plan(catalog, manifest, archives)
    backup = ("bucket", "rollback/test/packs/one.carddeck.v1-backup")
    client = FakeS3({("bucket", "packs/one.carddeck"): old, backup: b"previous backup"})
    with pytest.raises(ValueError, match="rollback object already exists"):
        module.migrate(client, plan, "rollback/test", apply=True)
    assert client.objects[backup] == b"previous backup"
    assert client.objects[("bucket", "packs/one.carddeck")] == old


def test_plan_rejects_catalog_drift(tmp_path):
    _old, _new, catalog, manifest, archives = files(tmp_path)
    value = json.loads(catalog.read_text())
    value["packs"][0]["sha256"] = "0" * 64
    catalog.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="live catalog digest"):
        module.build_plan(catalog, manifest, archives)
