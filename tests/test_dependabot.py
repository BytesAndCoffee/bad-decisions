from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")


def _entries() -> set[tuple[str, str]]:
    pairs = re.findall(
        r'package-ecosystem:\s*"([^"]+)"\s*\n\s*directory:\s*"([^"]+)"', CONFIG
    )
    assert len(pairs) == CONFIG.count("package-ecosystem:"), "each entry lists its directory right after its ecosystem"
    return {(ecosystem, directory.rstrip("/") or "/") for ecosystem, directory in pairs}


def _tracked() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [Path(line) for line in output.splitlines()]


def _directory(path: Path) -> str:
    parent = path.parent.as_posix()
    return "/" if parent == "." else f"/{parent}"


def _expected() -> set[tuple[str, str]]:
    expected = {("github-actions", "/")}
    for path in _tracked():
        name = path.name.lower()
        if name == "pyproject.toml":
            expected.add(("pip", _directory(path)))
        elif "dockerfile" in name:
            expected.add(("docker", _directory(path)))
        elif name.startswith("docker-compose") and name.endswith((".yml", ".yaml")):
            if re.search(r"^\s*image:", (ROOT / path).read_text(encoding="utf-8"), re.M):
                expected.add(("docker-compose", _directory(path)))
    return expected


def test_every_pinned_manifest_has_a_dependabot_entry():
    missing = _expected() - _entries()
    assert not missing, f"add these to .github/dependabot.yml: {sorted(missing)}"


def test_dependabot_entries_point_at_real_manifests():
    stale = _entries() - _expected()
    assert not stale, f"remove or fix these .github/dependabot.yml entries: {sorted(stale)}"
