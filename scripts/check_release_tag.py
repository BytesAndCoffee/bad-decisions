"""Fail unless a release tag (vX.Y.Z) matches every package version in the repo.

Usage: python scripts/check_release_tag.py v1.1.1
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG_PATTERN = re.compile(r"v(\d+\.\d+\.\d+)")


def _init_version(root: Path, path: str) -> str:
    match = re.search(r'^__version__ = "([^"]+)"$', (root / path).read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else "<missing>"


def _project_version(root: Path, path: str) -> str:
    return tomllib.loads((root / path).read_text(encoding="utf-8"))["project"]["version"]


def package_versions(root: Path = ROOT) -> dict[str, str]:
    return {
        "pyproject.toml": _project_version(root, "pyproject.toml"),
        "src/bad_decisions/__init__.py": _init_version(root, "src/bad_decisions/__init__.py"),
        "client/pyproject.toml": _project_version(root, "client/pyproject.toml"),
        "client/src/bad_decisions_client/__init__.py": _init_version(root, "client/src/bad_decisions_client/__init__.py"),
    }


def check(tag: str, root: Path = ROOT) -> list[str]:
    """Return a list of problems; empty means the tag is releasable."""
    match = TAG_PATTERN.fullmatch(tag)
    if not match:
        return [f"tag {tag!r} must look like vX.Y.Z"]
    expected = match.group(1)
    return [
        f"{path} is {version}, but tag {tag} expects {expected}"
        for path, version in package_versions(root).items()
        if version != expected
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_release_tag.py vX.Y.Z", file=sys.stderr)
        return 2
    problems = check(argv[1])
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if not problems:
        print(f"{argv[1]} matches all package versions")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
