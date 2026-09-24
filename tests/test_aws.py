from __future__ import annotations

import io
import json
from importlib.resources import files
from pathlib import Path
from unittest.mock import Mock

import pytest

from bad_decisions import operations
from bad_decisions.aws_packs import MAX_PACK_BYTES, load_s3_packs


class Body(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()


class Pages:
    def __init__(self, keys): self.keys = keys
    def search(self, _expression): return iter(self.keys)


class Paginator:
    def __init__(self, keys): self.keys = keys
    def paginate(self, **_kwargs): return Pages(self.keys)


class S3:
    def __init__(self, objects): self.objects = objects
    def get_paginator(self, name): assert name == "list_objects_v2"; return Paginator(self.objects)
    def get_object(self, *, Bucket, Key):
        data = self.objects[Key]
        return {"ContentLength": len(data), "Body": Body(data)}


def test_s3_pack_loading_is_validated_and_sorted(monkeypatch):
    base = files("bad_decisions").joinpath("data/packs/base.json").read_bytes()
    client = S3({"packs/base.json": base, "packs/ignore.txt": b"no"})
    monkeypatch.setattr("bad_decisions.aws_packs.boto3.client", lambda *_a, **_k: client)
    loaded = load_s3_packs("bucket")
    assert tuple(loaded) == ("base",)


def test_s3_pack_size_limit(monkeypatch):
    client = S3({"packs/huge.json": b"x" * (MAX_PACK_BYTES + 1)})
    monkeypatch.setattr("bad_decisions.aws_packs.boto3.client", lambda *_a, **_k: client)
    with pytest.raises(Exception, match="exceeds"):
        load_s3_packs("bucket")


def test_deploy_aws_uses_packaged_cdk_app(tmp_path, monkeypatch):
    env_file = tmp_path / ".bad-decisions.env"
    env_file.write_text("AWS_PROFILE=decisions\nAWS_DEFAULT_REGION=ca-west-1\nBAD_DECISIONS_AWS_SECRET_ID=secret\nBAD_DECISIONS_IMAGE=example.invalid/bad-decisions:tag\nBAD_DECISIONS_REPOSITORY_NAME=bad-decisions\nBAD_DECISIONS_ALLOW_HTTP=1\n")
    monkeypatch.setattr(operations, "AWS_ENV", env_file)
    completed = Mock(returncode=0)
    def run(command, **_kwargs):
        output_path = Path(command[command.index("--outputs-file") + 1])
        output_path.write_text(json.dumps({"BadDecisionsHybrid": {"LoadBalancerDnsName": "example.invalid", "PublicApiUrl": "http://example.invalid", "PackBucketName": "packs-bucket", "PackDistributionDomainName": "packs.example.invalid", "ClusterName": "cluster", "ServiceName": "service", "ConsequencesTableName": "table"}}))
        return completed
    run_mock = Mock(side_effect=run)
    monkeypatch.setattr(operations, "_run", run_mock)
    seed = Mock(return_value=0)
    monkeypatch.setattr(operations, "_seed_bundled_aws", seed)
    assert operations.deploy_aws([]) == 0
    command = run_mock.call_args.args[0]
    assert "bad_decisions.aws_cdk_app" in command[command.index("-a") + 1]
    assert run_mock.call_args.kwargs["env"]["BAD_DECISIONS_IMAGE"].endswith(":tag")
    assert run_mock.call_args.kwargs["env"]["BAD_DECISIONS_DESIRED_COUNT"] == "1"
    assert run_mock.call_args.kwargs["env"]["BAD_DECISIONS_MAX_COUNT"] == "2"
    saved = env_file.read_text()
    assert "BAD_DECISIONS_AWS_ENDPOINT=http://example.invalid" in saved
    assert "BAD_DECISIONS_AWS_PACK_BUCKET=packs-bucket" in saved
    assert "BAD_DECISIONS_AWS_PACK_INDEX=https://packs.example.invalid/packs/index" in saved
    assert "BAD_DECISIONS_AWS_CONSEQUENCES_TABLE=table" in saved
    seed.assert_called_once()


def test_status_aws_uses_saved_token(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".bad-decisions.env"
    env_file.write_text("BAD_DECISIONS_AWS_ENDPOINT=https://example.invalid\nBAD_DECISIONS_AWS_MANAGEMENT_TOKEN=secret\n")
    monkeypatch.setattr(operations, "AWS_ENV", env_file)

    class Response(Body):
        pass

    def open_request(request, timeout):
        assert timeout == 10
        assert request.full_url == "https://example.invalid/v1/manage/status"
        assert request.get_header("Authorization") == "Bearer secret"
        return Response(b'{"status":"ok"}')

    monkeypatch.setattr(operations.urllib.request, "urlopen", open_request)
    assert operations.status_aws([]) == 0
    assert capsys.readouterr().out.strip() == '{"status":"ok"}'


def test_legacy_cdk_entry_delegates_without_container_curl_healthcheck():
    source = (Path(__file__).resolve().parents[1] / "infra" / "aws" / "bad_decisions_stack.py").read_text(encoding="utf-8")
    assert "from bad_decisions.aws_stack import BadDecisionsAwsStack" in source
    assert "curl" not in source


class RecordingS3:
    def __init__(self, fail_key=None):
        self.puts = []
        self.deletes = []
        self.fail_key = fail_key
    def put_object(self, **kwargs):
        if kwargs["Key"] == self.fail_key:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "InternalError"}, "ResponseMetadata": {"HTTPStatusCode": 500}}, "PutObject")
        self.puts.append(kwargs)
        return {"VersionId": f"v-{len(self.puts)}"}
    def delete_object(self, **kwargs):
        self.deletes.append(kwargs)

class FakeSession:
    def __init__(self, client): self.value = client
    def client(self, *_args, **_kwargs): return self.value

def test_publish_archive_writes_catalog_last_and_preserves_metadata(tmp_path, sample_pack, monkeypatch):
    from bad_decisions.archive import export_pack
    from bad_decisions.aws_archive import publish_archive
    archive = export_pack(sample_pack, tmp_path / "sample.carddeck")
    client = RecordingS3()
    monkeypatch.setattr("bad_decisions.aws_archive._session", lambda *_a, **_k: FakeSession(client))
    metadata = publish_archive(archive, bucket="private-bucket", public_base_url="https://packs.example")
    assert [item["Key"] for item in client.puts] == [
        "packs/maha.carddeck", "runtime-packs/maha.json", "catalog/maha.json"
    ]
    assert metadata["metadata"]["license_id"] == sample_pack.metadata.license_id
    assert metadata["metadata"]["sources"] == [source.model_dump(mode="json") for source in sample_pack.metadata.sources]
    assert metadata["url"] == "https://packs.example/packs/maha.carddeck"

def test_publish_archive_rolls_back_exact_created_versions(tmp_path, sample_pack, monkeypatch):
    from bad_decisions.archive import export_pack
    from bad_decisions.aws_archive import publish_archive
    archive = export_pack(sample_pack, tmp_path / "sample.carddeck")
    client = RecordingS3(fail_key="catalog/maha.json")
    monkeypatch.setattr("bad_decisions.aws_archive._session", lambda *_a, **_k: FakeSession(client))
    with pytest.raises(Exception, match="InternalError"):
        publish_archive(archive, bucket="private-bucket", public_base_url="https://packs.example")
    assert client.deletes == [
        {"Bucket": "private-bucket", "Key": "runtime-packs/maha.json", "VersionId": "v-2"},
        {"Bucket": "private-bucket", "Key": "packs/maha.carddeck", "VersionId": "v-1"},
    ]

def test_aws_pack_cli_delegates_to_management(monkeypatch):
    command = Mock(return_value=0)
    monkeypatch.setattr(operations, "publish_aws_pack", command)
    from bad_decisions.cli import run
    assert run(["pack", "publish-aws", "x.carddeck", "--no-reload"]) == 0
    command.assert_called_once_with(["x.carddeck", "--no-reload"])

def test_settings_select_dynamodb_without_sqlite(monkeypatch):
    from bad_decisions.settings import Settings
    monkeypatch.delenv("BAD_DECISIONS_CONSEQUENCES_DB", raising=False)
    monkeypatch.setenv("BAD_DECISIONS_CONSEQUENCES_DYNAMODB_TABLE", "table")
    assert Settings.from_env().consequences_dynamodb_table == "table"


class DynamoClient:
    def __init__(self): self.transactions = []
    def transact_write_items(self, **kwargs): self.transactions.append(kwargs)

def test_dynamo_consequences_stores_raw_elements(registry, monkeypatch):
    from boto3.dynamodb.types import TypeDeserializer
    from bad_decisions.aws_consequences import DynamoConsequencesStore
    from bad_decisions.engine import generate_from_resolved
    from bad_decisions.packs import resolve_pools
    client = DynamoClient()
    monkeypatch.setattr("bad_decisions.aws_consequences.boto3.client", lambda *_a, **_k: client)
    round_ = generate_from_resolved(resolve_pools(registry), registry)
    issued = DynamoConsequencesStore("table").record_round(
        round_, request_id="request", client_id=None, session_id=None, feedback_enabled=True
    )
    raw = client.transactions[0]["TransactItems"][0]["Put"]["Item"]
    decoder = TypeDeserializer()
    item = {name: decoder.deserialize(value) for name, value in raw.items()}
    assert issued.combination_hash == item["combination_hash"]
    assert item["elements"][0]["text"] == round_.black.repr
    assert [element["text"] for element in item["elements"][1:]] == [card.text for card in round_.white]
    assert item["elements"][0]["license"] == round_.provenance[round_.black.pack].license_id

def test_catalog_lambda_builds_stable_compatible_index():
    from bad_decisions.aws_lambda.catalog_index import build_catalog
    class CatalogS3:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            class Pager:
                def paginate(self, **kwargs):
                    assert kwargs == {"Bucket": "bucket", "Prefix": "catalog/"}
                    return [{"Contents": [{"Key": "catalog/z.json"}, {"Key": "catalog/a.json"}]}]
            return Pager()
        def get_object(self, *, Bucket, Key):
            pack_id = Key.removeprefix("catalog/").removesuffix(".json")
            body = json.dumps({"archive": {"pack_id": pack_id}, "url": f"https://packs.example/packs/{pack_id}.carddeck", "object_key": f"packs/{pack_id}.carddeck"}).encode()
            return {"ContentLength": len(body), "Body": Body(body)}
    result = build_catalog(CatalogS3(), "bucket")
    assert result["schema_version"] == 1
    assert [entry["archive"]["pack_id"] for entry in result["packs"]] == ["a", "z"]
    assert result["rejected_archives"] == []
