"""The server installs lean; AWS, AWS deployment, and the TUI are opt-in extras."""

from __future__ import annotations

import builtins
import importlib
import sys
import tomllib
from pathlib import Path

import pytest

from bad_decisions import cli
from bad_decisions.errors import MissingExtraError

ROOT = Path(__file__).resolve().parents[1]
OPTIONAL = {"boto3", "botocore", "aws-cdk-lib", "constructs", "jsii", "textual", "rich"}


def _names(path: str) -> set[str]:
    return {line.split("==")[0].lower() for line in (ROOT / path).read_text().splitlines() if "==" in line}


def test_core_dependencies_and_runtime_lock_exclude_optional_packages():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    core = {requirement.split("=")[0].split(">")[0].lower() for requirement in project["dependencies"]}
    assert core == {"fastapi", "pydantic", "uvicorn"}
    extras = project["optional-dependencies"]
    assert {"aws", "aws-deploy", "tui", "dev"} <= set(extras)
    assert any(item.startswith("aws-cdk-lib") for item in extras["aws-deploy"])
    assert not any(item.startswith("aws-cdk-lib") for item in extras["aws"]), "the AWS runtime must not need the CDK"
    assert not _names("requirements.lock") & OPTIONAL, "deploy local installs requirements.lock"
    assert {"boto3", "aws-cdk-lib", "textual"} <= _names("requirements-extras.lock")
    assert (ROOT / "requirements-dev.lock").read_text().startswith("-r requirements.lock\n-r requirements-extras.lock\n")
    assert '".[aws]"' in (ROOT / "Dockerfile").read_text()


@pytest.mark.parametrize("module,missing,extra", [
    ("bad_decisions.aws_packs", "boto3", "aws"),
    ("bad_decisions.aws_consequences", "boto3", "aws"),
    ("bad_decisions.aws_archive", "botocore", "aws"),
    ("bad_decisions.aws_stack", "aws_cdk", "aws-deploy"),
    ("bad_decisions.consequences_tui", "textual", "tui"),
])
def test_missing_dependency_names_the_extra(monkeypatch, module, missing, extra):
    real_import = builtins.__import__

    def without(name, *args, **kwargs):
        if name == missing or name.startswith(f"{missing}."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without)
    monkeypatch.delitem(sys.modules, module, raising=False)
    with pytest.raises(MissingExtraError) as error:
        importlib.import_module(module)
    assert error.value.extra == extra
    assert f"pip install 'bad-decisions[{extra}]'" in error.value.message


def test_cli_reports_a_missing_extra_in_one_line(monkeypatch, capsys):
    def missing(_values):
        raise MissingExtraError("tui")

    monkeypatch.setattr(cli, "_run_consequences", missing)
    assert cli.main(["consequences", "tui"]) == 1
    err = capsys.readouterr().err
    assert err.strip() == f"bad-decisions: {MissingExtraError('tui').message}"


def test_aws_deploy_commands_check_for_the_cdk_before_doing_anything(monkeypatch):
    from bad_decisions import operations

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "aws_cdk" else object())
    monkeypatch.setattr(operations, "_require_commands", lambda *_a: pytest.fail("must fail before checking commands"))
    with pytest.raises(MissingExtraError) as error:
        operations.setup_aws([])
    assert error.value.extra == "aws-deploy"
