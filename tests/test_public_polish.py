from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

from bad_decisions.models import Pack

ROOT = Path(__file__).resolve().parents[1]


def test_readme_puts_the_joke_and_quick_start_before_architecture():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index("regret deal") < readme.index("## Why this exists")
    assert readme.index("## Sixty-second quick start") < readme.index("## Run the engine")
    forbidden_legacy_slogan = "It Gets" + " Worse"
    assert forbidden_legacy_slogan not in readme
    assert "consider starring the repository" in readme


def test_minimal_pack_is_a_valid_strict_pack_and_legal_docs_match():
    example = ROOT / "examples/minimal-pack"
    pack = Pack.model_validate(json.loads((example / "pack.json").read_text(encoding="utf-8")))
    assert pack.metadata.id == "example-pack"
    assert (example / "LICENSE.txt").read_text(encoding="utf-8").strip() == pack.metadata.license_notice
    assert (example / "ATTRIBUTION.md").read_text(encoding="utf-8").strip() == pack.metadata.attribution


def test_minimal_pack_build_script_has_valid_shell_syntax():
    subprocess.run(["bash", "-n", str(ROOT / "examples/minimal-pack/build.sh")], check=True)
    subprocess.run(["bash", "-n", str(ROOT / "scripts/record_demo.sh")], check=True)


def test_package_metadata_has_public_discovery_links_and_keywords():
    for relative in ("pyproject.toml", "client/pyproject.toml"):
        project = tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))["project"]
        assert {"Homepage", "Documentation", "Source", "Issues", "Changelog"} <= set(project["urls"])
        assert len(project["keywords"]) >= 5


def test_community_health_files_are_present():
    expected = (
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/PULL_REQUEST_TEMPLATE.md",
    )
    assert all((ROOT / path).is_file() for path in expected)
