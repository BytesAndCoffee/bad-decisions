from __future__ import annotations

import re
import tomllib
from pathlib import Path

from bad_decisions import __version__

ROOT = Path(__file__).resolve().parents[1]


def _init_version(path: str) -> str:
    return re.search(r'^__version__ = "([^"]+)"$', (ROOT / path).read_text(encoding="utf-8"), re.M).group(1)


def _project_version(path: str) -> str:
    return tomllib.loads((ROOT / path).read_text(encoding="utf-8"))["project"]["version"]


def test_every_version_source_agrees():
    versions = {
        "pyproject.toml": _project_version("pyproject.toml"),
        "src/bad_decisions/__init__.py": _init_version("src/bad_decisions/__init__.py"),
        "client/pyproject.toml": _project_version("client/pyproject.toml"),
        "client/src/bad_decisions_client/__init__.py": _init_version("client/src/bad_decisions_client/__init__.py"),
    }
    assert set(versions.values()) == {__version__}, versions


def test_web_asset_cache_busters_are_injected_from_the_bundle_digest():
    for name in ("index.html", "together.html"):
        html = (ROOT / "src/bad_decisions/web" / name).read_text(encoding="utf-8")
        busters = re.findall(r'(?:href|src)="[^"?]+\?v=([^"]+)"', html)
        assert busters, f"expected ?v= cache busters in {name}"
        assert set(busters) == {"__ASSET_VERSION__"}
