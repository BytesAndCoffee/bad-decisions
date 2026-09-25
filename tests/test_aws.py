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


BASE_ENV = "AWS_PROFILE=decisions\nAWS_DEFAULT_REGION=ca-west-1\nBAD_DECISIONS_AWS_SECRET_ID=secret\nBAD_DECISIONS_REPOSITORY_NAME=bad-decisions\nBAD_DECISIONS_ALLOW_HTTP=1\n"
STACK_OUTPUTS = {"BadDecisionsHybrid": {"LoadBalancerDnsName": "example.invalid", "PublicApiUrl": "http://example.invalid", "PackBucketName": "packs-bucket", "PackDistributionDomainName": "packs.example.invalid", "ClusterName": "cluster", "ServiceName": "service", "ConsequencesTableName": "table"}}


@pytest.fixture
def aws_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".bad-decisions.env"
    monkeypatch.setattr(operations, "AWS_ENV", env_file)
    monkeypatch.setattr(operations.shutil, "which", lambda name: f"/usr/bin/{name}")
    return env_file


def _fake_aws(secret_exists=True, bootstrapped=True):
    """A _run stand-in that answers the AWS/CDK calls setup and deploy make."""
    def run(command, **_kwargs):
        if command[:3] == ["aws", "cloudformation", "describe-stacks"]:
            return Mock(returncode=0 if bootstrapped else 254, stdout="", stderr="")
        if command[:2] == ["aws", "sts"]:
            return Mock(returncode=0, stdout="123456789012\n", stderr="")
        if command[:3] == ["aws", "secretsmanager", "describe-secret"]:
            return Mock(returncode=0 if secret_exists else 254, stdout="", stderr="")
        if "--outputs-file" in command:
            Path(command[command.index("--outputs-file") + 1]).write_text(json.dumps(STACK_OUTPUTS))
        return Mock(returncode=0, stdout="", stderr="")
    return Mock(side_effect=run)


def _commands(run_mock):
    return [call.args[0] for call in run_mock.call_args_list]


def test_deploy_aws_uses_packaged_cdk_app(aws_env, monkeypatch):
    aws_env.write_text(BASE_ENV)
    run_mock = _fake_aws()
    monkeypatch.setattr(operations, "_run", run_mock)
    seed = Mock(return_value=0)
    monkeypatch.setattr(operations, "_seed_bundled_aws", seed)
    assert operations.deploy_aws(["--image", "example.invalid/bad-decisions:tag", "--yes"]) == 0
    command = run_mock.call_args.args[0]
    assert "bad_decisions.aws_cdk_app" in command[command.index("-a") + 1]
    assert run_mock.call_args.kwargs["env"]["BAD_DECISIONS_IMAGE"].endswith(":tag")
    assert run_mock.call_args.kwargs["env"]["BAD_DECISIONS_DESIRED_COUNT"] == "1"
    assert run_mock.call_args.kwargs["env"]["BAD_DECISIONS_MAX_COUNT"] == "2"
    saved = aws_env.read_text()
    assert "BAD_DECISIONS_AWS_ENDPOINT=http://example.invalid" in saved
    assert "BAD_DECISIONS_AWS_PACK_BUCKET=packs-bucket" in saved
    assert "BAD_DECISIONS_AWS_PACK_INDEX=https://packs.example.invalid/packs/index" in saved
    assert "BAD_DECISIONS_AWS_CONSEQUENCES_TABLE=table" in saved
    assert "BAD_DECISIONS_IMAGE=example.invalid/bad-decisions:tag" in saved
    seed.assert_called_once()


def test_deploy_aws_builds_image_and_diffs_before_deploying(aws_env, monkeypatch):
    aws_env.write_text(BASE_ENV)
    run_mock = _fake_aws()
    monkeypatch.setattr(operations, "_run", run_mock)
    monkeypatch.setattr(operations, "_seed_bundled_aws", Mock(return_value=0))
    publish = Mock(return_value="example.invalid/bad-decisions:new")
    monkeypatch.setattr(operations, "_publish_image", publish)
    assert operations.deploy_aws(["--yes"]) == 0
    publish.assert_called_once()
    cdk_actions = [c[c.index("-a") + 2] for c in _commands(run_mock) if "aws-cdk" in c]
    assert cdk_actions == ["diff", "deploy"]
    assert "BAD_DECISIONS_IMAGE=example.invalid/bad-decisions:new" in aws_env.read_text()


def test_deploy_aws_declined_confirmation_deploys_nothing(aws_env, monkeypatch):
    aws_env.write_text(BASE_ENV)
    run_mock = _fake_aws()
    monkeypatch.setattr(operations, "_run", run_mock)
    monkeypatch.setattr(operations.sys.stdin, "isatty", lambda: True, raising=False)
    assert operations.deploy_aws(["--image", "example.invalid/bad-decisions:tag"], confirm=lambda _prompt: "n") == 1
    assert not any("deploy" in c for c in _commands(run_mock) if "aws-cdk" in c)
    assert "BAD_DECISIONS_AWS_ENDPOINT" not in aws_env.read_text()


def test_deploy_aws_requires_yes_without_terminal(aws_env, monkeypatch):
    aws_env.write_text(BASE_ENV)
    monkeypatch.setattr(operations.sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(SystemExit):
        operations.deploy_aws(["--image", "example.invalid/bad-decisions:tag"])


def test_deploy_aws_persists_domain_flags(aws_env, monkeypatch):
    aws_env.write_text(BASE_ENV.replace("BAD_DECISIONS_ALLOW_HTTP=1\n", ""))
    monkeypatch.setattr(operations, "_run", _fake_aws())
    monkeypatch.setattr(operations, "_seed_bundled_aws", Mock(return_value=0))
    flags = ["--certificate-arn", "arn:cert", "--domain-name", "cards.example.invalid", "--hosted-zone-id", "Z123"]
    assert operations.deploy_aws(["--image", "example.invalid/bad-decisions:tag", "--yes", *flags]) == 0
    assert operations.deploy_aws(["--image", "example.invalid/bad-decisions:tag", "--yes"]) == 0
    saved = aws_env.read_text()
    assert "BAD_DECISIONS_CERTIFICATE_ARN=arn:cert" in saved
    assert "BAD_DECISIONS_DOMAIN_NAME=cards.example.invalid" in saved


def test_setup_aws_keeps_existing_token_and_deploy_state(aws_env, monkeypatch):
    aws_env.write_text(BASE_ENV + "BAD_DECISIONS_AWS_MANAGEMENT_TOKEN=kept\nBAD_DECISIONS_AWS_ENDPOINT=https://api.example.invalid\n")
    run_mock = _fake_aws(secret_exists=True)
    monkeypatch.setattr(operations, "_run", run_mock)
    assert operations.setup_aws(["--secret-id", "secret"]) == 0
    assert not any(c[:2] == ["aws", "secretsmanager"] and c[2] != "describe-secret" for c in _commands(run_mock))
    assert not any(c[0] == "docker" for c in _commands(run_mock))
    saved = aws_env.read_text()
    assert "BAD_DECISIONS_AWS_MANAGEMENT_TOKEN=kept" in saved
    assert "BAD_DECISIONS_AWS_ENDPOINT=https://api.example.invalid" in saved
    assert "BAD_DECISIONS_ALLOW_HTTP=1" in saved


def test_setup_aws_creates_missing_secret(aws_env, monkeypatch):
    run_mock = _fake_aws(secret_exists=False)
    monkeypatch.setattr(operations, "_run", run_mock)
    assert operations.setup_aws(["--secret-id", "secret"]) == 0
    assert any(c[:3] == ["aws", "secretsmanager", "create-secret"] for c in _commands(run_mock))
    assert "BAD_DECISIONS_AWS_MANAGEMENT_TOKEN=" in aws_env.read_text()


def test_setup_aws_does_not_rotate_unsaved_existing_secret(aws_env, monkeypatch, capsys):
    run_mock = _fake_aws(secret_exists=True)
    monkeypatch.setattr(operations, "_run", run_mock)
    assert operations.setup_aws(["--secret-id", "secret"]) == 0
    assert not any(c[:3] == ["aws", "secretsmanager", "put-secret-value"] for c in _commands(run_mock))
    assert "rotate-token aws" in capsys.readouterr().err
    assert "BAD_DECISIONS_AWS_MANAGEMENT_TOKEN" not in aws_env.read_text()


def test_rotate_token_aws_updates_secret_and_restarts_tasks(aws_env, monkeypatch):
    aws_env.write_text(BASE_ENV + "BAD_DECISIONS_AWS_MANAGEMENT_TOKEN=old\nBAD_DECISIONS_AWS_CLUSTER=cluster\nBAD_DECISIONS_AWS_SERVICE=service\n")
    run_mock = _fake_aws()
    monkeypatch.setattr(operations, "_run", run_mock)
    reload = Mock()
    monkeypatch.setattr("bad_decisions.aws_archive.force_runtime_reload", reload)
    assert operations.run(["rotate-token", "aws"]) == 0
    assert any(c[:3] == ["aws", "secretsmanager", "put-secret-value"] for c in _commands(run_mock))
    assert "BAD_DECISIONS_AWS_MANAGEMENT_TOKEN=old" not in aws_env.read_text()
    reload.assert_called_once_with(cluster="cluster", service="service", profile="decisions", region="ca-west-1")


def test_rotate_token_aws_leaves_env_alone_when_secret_write_fails(aws_env, monkeypatch):
    aws_env.write_text(BASE_ENV + "BAD_DECISIONS_AWS_MANAGEMENT_TOKEN=old\n")
    monkeypatch.setattr(operations, "_run", Mock(return_value=Mock(returncode=1)))
    assert operations.rotate_token_aws([]) == 1
    assert "BAD_DECISIONS_AWS_MANAGEMENT_TOKEN=old" in aws_env.read_text()


def test_setup_aws_skips_bootstrap_when_already_bootstrapped(aws_env, monkeypatch):
    run_mock = _fake_aws(bootstrapped=True)
    monkeypatch.setattr(operations, "_run", run_mock)
    assert operations.setup_aws(["--secret-id", "secret"]) == 0
    assert not any("bootstrap" in c for c in _commands(run_mock))


@pytest.mark.parametrize(("bootstrapped", "flags"), [(False, []), (True, ["--bootstrap"])])
def test_setup_aws_bootstraps_when_missing_or_forced(aws_env, monkeypatch, bootstrapped, flags):
    run_mock = _fake_aws(bootstrapped=bootstrapped)
    monkeypatch.setattr(operations, "_run", run_mock)
    assert operations.setup_aws(["--secret-id", "secret", *flags]) == 0
    assert any("bootstrap" in c for c in _commands(run_mock))


def test_setup_aws_limits_retained_images(aws_env, monkeypatch):
    run_mock = _fake_aws()
    monkeypatch.setattr(operations, "_run", run_mock)
    assert operations.setup_aws(["--secret-id", "secret"]) == 0
    policy = next(c for c in _commands(run_mock) if c[:3] == ["aws", "ecr", "put-lifecycle-policy"])
    rule = json.loads(policy[policy.index("--lifecycle-policy-text") + 1])["rules"][0]
    assert rule["selection"]["countType"] == "imageCountMoreThan"
    assert rule["action"]["type"] == "expire"


def test_deploy_aws_persists_capacity(aws_env, monkeypatch):
    aws_env.write_text(BASE_ENV)
    run_mock = _fake_aws()
    monkeypatch.setattr(operations, "_run", run_mock)
    monkeypatch.setattr(operations, "_seed_bundled_aws", Mock(return_value=0))
    assert operations.deploy_aws(["--image", "example.invalid/bad-decisions:tag", "--yes", "--capacity", "on-demand"]) == 0
    assert run_mock.call_args.kwargs["env"]["BAD_DECISIONS_CAPACITY"] == "on-demand"
    assert "BAD_DECISIONS_CAPACITY=on-demand" in aws_env.read_text()


def _synth(monkeypatch, **env):
    import aws_cdk as cdk
    from aws_cdk.assertions import Template
    from bad_decisions.aws_stack import BadDecisionsAwsStack
    settings = {"BAD_DECISIONS_IMAGE": "123456789012.dkr.ecr.ca-west-1.amazonaws.com/bad-decisions:tag", "BAD_DECISIONS_AWS_SECRET_ID": "secret", "BAD_DECISIONS_ALLOW_HTTP": "1"}
    settings.update(env)
    for key in ("BAD_DECISIONS_CERTIFICATE_ARN", "BAD_DECISIONS_DOMAIN_NAME", "BAD_DECISIONS_HOSTED_ZONE_ID", "BAD_DECISIONS_CAPACITY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in settings.items():
        if value is None: monkeypatch.delenv(key, raising=False)
        else: monkeypatch.setenv(key, value)
    app = cdk.App()
    return Template.from_stack(BadDecisionsAwsStack(app, "Test", env=cdk.Environment(account="123456789012", region="ca-west-1")))


def test_stack_has_no_fixed_cost_networking(monkeypatch):
    template = _synth(monkeypatch)
    for resource in ("AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::EC2::NatGateway"):
        template.resource_count_is(resource, 0)
    endpoints = template.find_resources("AWS::EC2::VPCEndpoint")
    assert endpoints and all(e["Properties"].get("VpcEndpointType", "Gateway") == "Gateway" for e in endpoints.values())
    template.has_resource_properties("AWS::ECS::TaskDefinition", {"Cpu": "256", "Memory": "512"})


def test_stack_runs_spot_by_default_and_on_demand_on_request(monkeypatch):
    from aws_cdk.assertions import Match
    _synth(monkeypatch).has_resource_properties("AWS::ECS::Service", {"CapacityProviderStrategy": [Match.object_like({"CapacityProvider": "FARGATE_SPOT"})]})
    _synth(monkeypatch, BAD_DECISIONS_CAPACITY="on-demand").has_resource_properties("AWS::ECS::Service", {"CapacityProviderStrategy": [Match.object_like({"CapacityProvider": "FARGATE"})]})
    with pytest.raises(ValueError, match="BAD_DECISIONS_CAPACITY"):
        _synth(monkeypatch, BAD_DECISIONS_CAPACITY="cheap")


def test_stack_tasks_accept_traffic_only_from_the_vpc_link(monkeypatch):
    template = _synth(monkeypatch)
    ingress = list(template.find_resources("AWS::EC2::SecurityGroupIngress").values())
    assert len(ingress) == 1
    rule = ingress[0]["Properties"]
    assert (rule["FromPort"], rule["ToPort"], rule["IpProtocol"]) == (8000, 8000, "tcp")
    assert "SourceSecurityGroupId" in rule and "CidrIp" not in rule
    for group in template.find_resources("AWS::EC2::SecurityGroup").values():
        assert not group["Properties"].get("SecurityGroupIngress")
    template.has_resource_properties("AWS::ECS::Service", {"NetworkConfiguration": {"AwsvpcConfiguration": {"AssignPublicIp": "ENABLED"}}})


def test_stack_container_health_check_uses_python(monkeypatch):
    template = _synth(monkeypatch)
    definition = next(iter(template.find_resources("AWS::ECS::TaskDefinition").values()))
    command = definition["Properties"]["ContainerDefinitions"][0]["HealthCheck"]["Command"]
    assert command[:2] == ["CMD", "python"] and "/healthz" in command[-1]
    assert "curl" not in json.dumps(command)


def test_stack_custom_domain_disables_generated_endpoint(monkeypatch):
    template = _synth(monkeypatch, BAD_DECISIONS_ALLOW_HTTP=None, BAD_DECISIONS_CERTIFICATE_ARN="arn:aws:acm:ca-west-1:123456789012:certificate/x", BAD_DECISIONS_DOMAIN_NAME="cards.example.invalid", BAD_DECISIONS_HOSTED_ZONE_ID="Z123")
    template.has_resource_properties("AWS::ApiGatewayV2::Api", {"DisableExecuteApiEndpoint": True})
    template.has_resource_properties("AWS::ApiGatewayV2::DomainName", {"DomainName": "cards.example.invalid"})
    template.resource_count_is("AWS::Route53::RecordSet", 1)
    template.has_resource_properties("AWS::ApiGatewayV2::Stage", {"DefaultRouteSettings": {"ThrottlingRateLimit": 50, "ThrottlingBurstLimit": 100}})


def test_stack_expires_old_pack_versions(monkeypatch):
    from aws_cdk.assertions import Match
    _synth(monkeypatch).has_resource_properties("AWS::S3::Bucket", {"LifecycleConfiguration": {"Rules": [Match.object_like({"NoncurrentVersionExpiration": {"NoncurrentDays": 30}})]}})


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


def test_local_session_exports_aws_login_credentials(monkeypatch):
    from bad_decisions.aws_credentials import local_session
    exported = json.dumps({
        "Version": 1, "AccessKeyId": "temporary-access",
        "SecretAccessKey": "temporary-secret", "SessionToken": "temporary-token",
        "Expiration": "2099-01-01T00:00:00Z",
    })
    run = Mock(return_value=Mock(returncode=0, stdout=exported, stderr=""))
    session = Mock()
    constructor = Mock(return_value=session)
    monkeypatch.setattr("bad_decisions.aws_credentials.subprocess.run", run)
    monkeypatch.setattr("bad_decisions.aws_credentials.boto3.Session", constructor)
    assert local_session("decisions", "ca-west-1") is session
    assert run.call_args.args[0] == [
        "aws", "configure", "export-credentials", "--profile", "decisions", "--format", "process"
    ]
    constructor.assert_called_once_with(
        aws_access_key_id="temporary-access",
        aws_secret_access_key="temporary-secret",
        aws_session_token="temporary-token",
        region_name="ca-west-1",
    )


def test_local_session_reports_export_failure_without_stdout(monkeypatch):
    from bad_decisions.aws_credentials import local_session
    from bad_decisions.errors import PackConfigurationError
    monkeypatch.setattr(
        "bad_decisions.aws_credentials.subprocess.run",
        Mock(return_value=Mock(returncode=1, stdout="sensitive", stderr="login expired")),
    )
    with pytest.raises(PackConfigurationError, match="login expired") as caught:
        local_session("decisions", "ca-west-1")
    assert "sensitive" not in str(caught.value)


def test_seed_aws_cli_delegates_to_recovery_command(monkeypatch):
    command = Mock(return_value=0)
    monkeypatch.setattr(operations, "seed_aws_packs", command)
    from bad_decisions.cli import run
    assert run(["pack", "seed-aws"]) == 0
    command.assert_called_once_with([])
