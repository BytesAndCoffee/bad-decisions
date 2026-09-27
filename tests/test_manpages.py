from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = [ROOT, ROOT / "client"]


def _config(project: Path) -> dict:
    return tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("project", PROJECTS, ids=lambda path: path.name)
def test_every_console_script_installs_its_manpage(project: Path):
    config = _config(project)
    shared = config["tool"]["hatch"]["build"]["targets"]["wheel"].get("shared-data", {})
    sdist = config["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    scripts = config["project"]["scripts"]
    assert scripts
    for script in scripts:
        source = f"man/{script}.1"
        assert shared.get(source) == f"share/man/man1/{script}.1", f"{project.name}: {script} manpage not installed"
        page = (project / source).read_text(encoding="utf-8")
        assert re.search(rf'^\.TH "?{re.escape(script.upper())}"? 1\b', page, re.M), f"{source} has the wrong .TH"
        assert "man" in sdist, f"{project.name}: sdist omits man/"
    assert set(shared) == {f"man/{script}.1" for script in scripts}


def _page(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _choices(parser) -> set[str]:
    return {name for action in parser._actions if hasattr(action, "choices") and isinstance(action.choices, dict) for name in action.choices}


@pytest.mark.parametrize(("page", "init"), [
    ("man/bad-decisions.1", "src/bad_decisions/__init__.py"),
    ("client/man/regret.1", "client/src/bad_decisions_client/__init__.py"),
])
def test_manpage_footer_names_the_release_version(page: str, init: str):
    version = re.search(r'^__version__ = "([^"]+)"$', _page(init), re.M).group(1)
    header = _page(page).splitlines()[0]
    assert re.search(rf'"[^"]* {re.escape(version)}" "User Commands"$', header), f"{page}: update .TH to {version}: {header}"


def test_bad_decisions_manpage_documents_every_command_and_setting():
    from bad_decisions.cli import consequences_parser, pack_parser

    page = _page("man/bad-decisions.1")
    cli = _page("src/bad_decisions/cli.py")
    operational = set(re.findall(r'"([a-z-]+)"', re.search(r'values\[0\] in (\{"setup"[^}]*\})', cli).group(1)))
    commands = operational | _choices(pack_parser()) | _choices(consequences_parser())
    missing = sorted(name for name in commands if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", page))
    assert not missing, f"bad-decisions(1) does not mention: {missing}"
    # Settings the AWS stack injects into its own containers are not user-facing.
    provisioned = {"BAD_DECISIONS_CONSEQUENCES_DYNAMODB_TABLE", "BAD_DECISIONS_MANAGEMENT_TOKEN"}
    settings = set(re.findall(r"BAD_DECISIONS_[A-Z_]+", _page("src/bad_decisions/settings.py"))) - provisioned
    assert not sorted(name for name in settings if f".B {name}\n" not in page), "bad-decisions(1) ENVIRONMENT is missing settings"


def test_regret_manpage_documents_every_command():
    page = _page("client/man/regret.1")
    cli = _page("client/src/bad_decisions_client/cli.py")
    commands = set(re.findall(r'values\[0\]=="([a-z-]+)"', cli))
    assert {"together", "health", "deal"} <= commands
    missing = sorted(name for name in commands if f".B regret {name}" not in page)
    assert not missing, f"regret(1) SYNOPSIS does not list: {missing}"
