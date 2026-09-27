from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_installed", ROOT / "scripts" / "check_installed.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

VERSION = "9.9.9"
STUB = """#!{python}
import http.server, json, sys
NAME, VERSION, PACKS = {name!r}, {version!r}, {packs!r}
args = sys.argv[1:]
if args == ["--version"]:
    print(NAME, VERSION)
elif args == ["--list-packs"]:
    print("\\n".join(f"pack{{i}}\\tPack" for i in range(PACKS)))
elif args == ["--oneshot"]:
    print("A bad decision.")
elif args[:1] == ["-c"]:
    print({lock!r})
elif args[:1] == ["serve"]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = {{"/healthz": json.dumps({{"status": "ok", "version": VERSION}}), "/v1/round": json.dumps({{"result": "x"}}),
                    "/web/": "<html></html>"}}.get(self.path)
            self.send_response(200 if body else 404); self.end_headers(); self.wfile.write((body or "").encode())
        def log_message(self, *_): pass
    http.server.HTTPServer(("127.0.0.1", int(args[args.index("--port") + 1])), Handler).serve_forever()
else:
    sys.exit(2)
"""


def _source(root: Path, packs: int) -> None:
    (root / "src/bad_decisions/data/packs").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{VERSION}"\n', encoding="utf-8")
    for index in range(packs):
        (root / f"src/bad_decisions/data/packs/pack{index}.json").write_text("{}", encoding="utf-8")


def _prefix(prefix: Path, *, packs: int = 3, version: str = VERSION, manpages=checker.MANPAGES, lock: bool = True) -> Path:
    (prefix / "bin").mkdir(parents=True)
    for name in ("bad-decisions", "regret", "python"):
        script = prefix / "bin" / name
        script.write_text(textwrap.dedent(STUB.format(python=sys.executable, name=name, version=version, packs=packs, lock=str(lock))), encoding="utf-8")
        script.chmod(0o755)
    (prefix / "share/man/man1").mkdir(parents=True)
    for page in manpages:
        (prefix / "share/man/man1" / page).write_text(".TH X 1\n", encoding="utf-8")
    return prefix


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "src-tree"
    _source(root, 3)
    return root


def test_a_complete_install_passes(tmp_path, source):
    assert checker.check(_prefix(tmp_path / "venv"), source) == []


def test_missing_manpage_lock_and_packs_are_each_reported(tmp_path, source):
    problems = checker.check(_prefix(tmp_path / "venv", packs=2, manpages=("regret.1",), lock=False), source)
    assert [problem.split(":")[0] for problem in problems] == ["bundled packs", "requirements.lock", "no optional extras in the core install", "manual pages"]


def test_a_version_mismatch_fails_the_cli_and_server_checks(tmp_path, source):
    problems = checker.check(_prefix(tmp_path / "venv", version="9.9.8"), source)
    assert {problem.split(":")[0] for problem in problems} == {"bad-decisions --version", "regret --version", "bad-decisions serve"}


def test_a_developer_registry_does_not_leak_into_the_check(monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_PACK_DIR", "/elsewhere")
    monkeypatch.setenv("REGRET_API_URL", "http://elsewhere")
    assert not any(key.startswith(("BAD_DECISIONS_", "REGRET_")) for key in checker._environment())


def test_the_release_build_job_runs_the_installed_check():
    build = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8").split("\n  build:\n")[1].split("\n  publish:\n")[0]
    assert "python -m venv" in build and "requirements.lock" in build
    assert build.index("twine check") < build.index("scripts/check_installed.py") < build.index("upload-artifact")
