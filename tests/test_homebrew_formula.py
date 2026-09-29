from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORMULA = (ROOT / "homebrew" / "regret.rb").read_text(encoding="utf-8")
spec = importlib.util.spec_from_file_location("update_homebrew_formula", ROOT / "scripts" / "update_homebrew_formula.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)

NEW_URL = "https://files.pythonhosted.org/packages/aa/bb/bad_decisions_client-9.9.9.tar.gz"
NEW_SHA = "a" * 64


def _pypi(files):
    return lambda _url: json.dumps({"urls": files}).encode()


def test_formula_installs_the_client_sdist_with_its_manpage_and_tests_it():
    assert re.search(r'^  url "https://files\.pythonhosted\.org/\S+/bad_decisions_client-\d+\.\d+\.\d+\.tar\.gz"$', FORMULA, re.M)
    assert "virtualenv_install_with_resources" in FORMULA  # links share/man/man1/regret.1
    assert 'license "MIT"' in FORMULA
    assert "revision 1" in FORMULA, "2.1.0 users must receive the newly bundled TUI on upgrade"
    for dependency in ("linkify-it-py", "markdown-it-py", "mdit-py-plugins", "mdurl", "platformdirs", "pygments", "rich", "textual", "typing-extensions"):
        assert f'resource "{dependency}"' in FORMULA
    assert FORMULA.count('  resource "') == 9
    assert '"import textual"' in FORMULA, "the formula test proves the TUI dependency is installed"
    assert "regret doctor" in FORMULA, "the formula test exercises doctor's Homebrew detection"
    assert "regret --version" in FORMULA and 'man1/"regret.1"' in FORMULA
    assert "Cards Against" not in FORMULA, "unofficial fan project: no trade names in the description"


def test_rewrite_changes_only_the_formula_url_and_digest():
    updated = updater.rewrite(FORMULA, NEW_URL, NEW_SHA)
    assert f'url "{NEW_URL}"' in updated and f'sha256 "{NEW_SHA}"' in updated
    changed = [(old, new) for old, new in zip(FORMULA.splitlines(), updated.splitlines()) if old != new]
    assert len(changed) == 2 and all(old.startswith(("  url ", "  sha256 ")) for old, _new in changed)
    assert updated.count("\n") == FORMULA.count("\n")


def test_formula_tui_resource_versions_match_the_extras_lock():
    lock = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("requirements.lock", "requirements-extras.lock"))
    pins = dict((name.replace("_", "-"), version) for name, version in (line.strip().lower().split("==", 1) for line in lock.splitlines() if "==" in line))
    for name in ("linkify-it-py", "markdown-it-py", "mdit-py-plugins", "mdurl", "platformdirs", "pygments", "rich", "textual", "typing-extensions"):
        block = FORMULA.split(f'resource "{name}" do', 1)[1].split("  end", 1)[0]
        normalized = name.replace("-", "[_-]")
        match = re.search(rf"{normalized}-([0-9][^/]+)\.tar\.gz", block, re.I)
        assert match and match.group(1) == pins[name]


def test_rewrite_leaves_resource_blocks_alone():
    with_resource = FORMULA.replace('  license "MIT"\n', '  license "MIT"\n\n  resource "x" do\n    url "https://files.pythonhosted.org/x.tar.gz"\n    sha256 "' + "b" * 64 + '"\n  end\n')
    updated = updater.rewrite(with_resource, NEW_URL, NEW_SHA)
    assert updated.split("\n  resource ", 1)[1] == with_resource.split("\n  resource ", 1)[1]


def test_published_sdist_picks_the_exact_sdist():
    files = [
        {"filename": "bad_decisions_client-9.9.9-py3-none-any.whl", "packagetype": "bdist_wheel", "url": "https://files.pythonhosted.org/w", "digests": {"sha256": "b" * 64}},
        {"filename": "bad_decisions_client-9.9.9.tar.gz", "packagetype": "sdist", "url": NEW_URL, "digests": {"sha256": NEW_SHA}},
    ]
    assert updater.published_sdist("9.9.9", _pypi(files)) == (NEW_URL, NEW_SHA)


@pytest.mark.parametrize("item", [
    {"filename": "bad_decisions_client-9.9.9.tar.gz", "packagetype": "sdist", "url": "http://evil.example/x.tar.gz", "digests": {"sha256": NEW_SHA}},
    {"filename": "bad_decisions_client-9.9.9.tar.gz", "packagetype": "sdist", "url": NEW_URL, "digests": {"sha256": "nothex"}},
    {"filename": "bad_decisions_client-9.9.8.tar.gz", "packagetype": "sdist", "url": NEW_URL, "digests": {"sha256": NEW_SHA}},
])
def test_published_sdist_rejects_unexpected_files(item):
    with pytest.raises(ValueError, match="publish the release first"):
        updater.published_sdist("9.9.9", _pypi([item]))


def test_rewrite_refuses_a_formula_without_a_top_level_url():
    with pytest.raises(ValueError):
        updater.rewrite('class Regret < Formula\n  resource "x" do\n  end\nend\n', NEW_URL, NEW_SHA)
