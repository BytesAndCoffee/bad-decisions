"""Point homebrew/regret.rb at a published bad-decisions-client sdist.

Usage: python scripts/update_homebrew_formula.py 1.8.6

Run it after the release is on PyPI. It rewrites the formula's own url and
sha256 and resets any formula revision carried by the previous upstream
version. Resource blocks are left alone; `brew update-python-resources`
refreshes them. Then test the formula and copy it to the tap, as
docs/RELEASING.md describes.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMULA = ROOT / "homebrew" / "regret.rb"
PYPI_RELEASE = "https://pypi.org/pypi/bad-decisions-client/{version}/json"
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
# The formula's own url/sha256 are the ones before the first resource block.
HEAD_RE = re.compile(r'\A(?P<before>.*?^  url ")[^"]+(?P<middle>"\n  sha256 ")[0-9a-f]{64}(?P<after>".*)\Z', re.S | re.M)


def published_sdist(version: str, fetch=None) -> tuple[str, str]:
    """(url, sha256) of the version's sdist on PyPI."""
    if fetch is None:
        def fetch(url: str) -> bytes:
            with urllib.request.urlopen(url, timeout=30) as response:
                return response.read()
    files = json.loads(fetch(PYPI_RELEASE.format(version=version)))["urls"]
    expected = f"bad_decisions_client-{version}.tar.gz"
    for item in files:
        if item.get("filename") == expected and item.get("packagetype") == "sdist":
            url, digest = item["url"], item["digests"]["sha256"]
            if url.startswith("https://files.pythonhosted.org/") and re.fullmatch(r"[0-9a-f]{64}", digest):
                return url, digest
    raise ValueError(f"PyPI has no {expected}; publish the release first")


def rewrite(formula: str, url: str, digest: str) -> str:
    head = formula.split("\n  resource ", 1)[0]
    match = HEAD_RE.match(head)
    if not match:
        raise ValueError("formula has no top-level url and sha256 to update")
    updated_head = match.group("before") + url + match.group("middle") + digest + match.group("after")
    updated_head = re.sub(r"^  revision \d+\n", "", updated_head, count=1, flags=re.M)
    return updated_head + formula[len(head):]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not VERSION_RE.fullmatch(argv[1]):
        print("usage: update_homebrew_formula.py X.Y.Z", file=sys.stderr)
        return 2
    try:
        url, digest = published_sdist(argv[1])
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    FORMULA.write_text(rewrite(FORMULA.read_text(encoding="utf-8"), url, digest), encoding="utf-8")
    print(f"{FORMULA.relative_to(ROOT)} now installs bad-decisions-client {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
