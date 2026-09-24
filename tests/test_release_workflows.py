from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
RELEASE = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_release_tag", ROOT / "scripts" / "check_release_tag.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _job(text: str, name: str) -> str:
    start = re.search(rf"^  {name}:\n", text, re.M)
    assert start, f"job {name} not found"
    rest = text[start.end():]
    nxt = re.search(r"^  [A-Za-z_-]+:\n", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_every_action_is_pinned_to_a_full_commit_sha():
    for name, text in (("ci.yml", CI), ("release.yml", RELEASE)):
        refs = re.findall(r"^\s*-?\s*uses:\s*(\S+)", text, re.M)
        assert refs, name
        for ref in refs:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), f"{name}: {ref} is not SHA-pinned"
    for line in re.findall(r"^.*uses:.*$", CI + RELEASE, re.M):
        assert re.search(r"#\s*v\d", line), f"missing version comment: {line.strip()}"


def test_workflows_default_to_read_only_and_never_store_tokens_or_use_pr_target():
    for name, text in (("ci.yml", CI), ("release.yml", RELEASE)):
        assert re.search(r"^permissions:\n  contents: read\n", text, re.M), name
        assert "pull_request_target" not in text, name
        assert "secrets." not in text, f"{name} must use Trusted Publishing, not stored secrets"
        assert "password:" not in text, name


def test_ci_runs_on_main_and_pull_requests_with_the_full_gate():
    assert re.search(r"branches: \[main\]", CI)
    assert re.search(r"^  pull_request:", CI, re.M)
    for command in ("python -m pytest", "client/tests", "bash -n deploy.sh", "bash -n deploy/rollback.sh"):
        assert command in CI, command
    assert "3.9" in CI, "the client supports Python 3.9"


def test_release_triggers_only_on_version_tags():
    trigger = RELEASE.split("permissions:")[0]
    assert re.search(r'tags: \["v\[0-9\]\+\.\[0-9\]\+\.\[0-9\]\+"\]', trigger)
    assert "branches" not in trigger and "pull_request" not in trigger and "workflow_dispatch" not in trigger


def test_release_verifies_before_building_and_builds_before_publishing():
    verify, build, publish = (_job(RELEASE, job) for job in ("verify", "build", "publish"))
    assert "needs: verify" in build and "needs: build" in publish
    assert "git merge-base --is-ancestor" in verify and "origin/main" in verify
    assert "scripts/check_release_tag.py" in verify
    assert "python -m pytest" in verify and "client/tests" in verify
    assert "twine check dist/* client/dist/*" in build


def test_only_the_publish_job_can_mint_an_oidc_token_and_it_needs_approval():
    assert RELEASE.count("id-token: write") == 1
    publish = _job(RELEASE, "publish")
    assert "id-token: write" in publish
    assert re.search(r"environment:\n\s+name: pypi", publish)
    for job in ("verify", "build"):
        assert "id-token" not in _job(RELEASE, job)
    assert publish.count("gh-action-pypi-publish@") == 2
    assert "server-dist/" in publish and "client-dist/" in publish


def test_release_publishes_the_artifacts_it_built_without_rebuilding():
    publish = _job(RELEASE, "publish")
    assert "python -m build" not in publish
    assert "download-artifact@" in publish


def test_release_doc_covers_setup_and_the_tag_procedure():
    doc = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    for needle in ("Trusted Publishing", "release.yml", "environment", "git tag v", "pypi"):
        assert needle in doc, needle


@pytest.mark.parametrize("tag", ["v1.1.1", "v0.0.0"])
def test_check_accepts_a_matching_tag(tmp_path, tag):
    version = tag[1:]
    (tmp_path / "src/bad_decisions").mkdir(parents=True)
    (tmp_path / "client/src/bad_decisions_client").mkdir(parents=True)
    for path in ("pyproject.toml", "client/pyproject.toml"):
        (tmp_path / path).write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    for path in ("src/bad_decisions/__init__.py", "client/src/bad_decisions_client/__init__.py"):
        (tmp_path / path).write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    assert checker.check(tag, tmp_path) == []


def test_check_reports_every_disagreeing_source(tmp_path):
    (tmp_path / "src/bad_decisions").mkdir(parents=True)
    (tmp_path / "client/src/bad_decisions_client").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.1.1"\n', encoding="utf-8")
    (tmp_path / "client/pyproject.toml").write_text('[project]\nversion = "1.1.0"\n', encoding="utf-8")
    (tmp_path / "src/bad_decisions/__init__.py").write_text('__version__ = "1.1.1"\n', encoding="utf-8")
    (tmp_path / "client/src/bad_decisions_client/__init__.py").write_text("# no version\n", encoding="utf-8")
    problems = checker.check("v1.1.1", tmp_path)
    assert len(problems) == 2
    assert any("client/pyproject.toml" in p for p in problems)
    assert any("<missing>" in p for p in problems)


@pytest.mark.parametrize("tag", ["1.1.1", "v1.1", "v1.1.1rc1", "v1.1.1.1", "v1.1.1-dev", "", "refs/tags/v1.1.1", "v1.1.1\nv1.1.2"])
def test_check_rejects_malformed_tags(tag):
    problems = checker.check(tag, ROOT)
    assert problems and "must look like vX.Y.Z" in problems[0]


def test_the_repository_versions_are_releasable_as_the_current_tag():
    current = checker.package_versions(ROOT)["pyproject.toml"]
    assert checker.check(f"v{current}", ROOT) == []
