"""Smoke-test both packages as installed from their built wheels, before publishing.

Usage: python scripts/check_installed.py PREFIX

PREFIX is a clean virtual environment with both wheels installed. The checks
cover what the unit tests cannot see: files that only exist if packaging put
them in the wheel (bundled packs, web assets, requirements.lock, manual pages)
and the console scripts working outside the source tree.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANPAGES = ("bad-decisions.1", "regret.1")


def _environment() -> dict[str, str]:
    # A developer's registry or config must not leak into the check.
    return {key: value for key, value in os.environ.items() if not key.startswith(("BAD_DECISIONS_", "REGRET_"))}


def _run(prefix: Path, *command: str) -> str:
    completed = subprocess.run(
        [str(prefix / "bin" / command[0]), *command[1:]],
        capture_output=True, text=True, timeout=120, env=_environment(), stdin=subprocess.DEVNULL, cwd=prefix,
    )
    if completed.returncode != 0:
        raise AssertionError(f"{' '.join(command)} exited {completed.returncode}: {completed.stderr.strip()[-500:]}")
    return completed.stdout


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read().decode("utf-8", "replace")


def _check_server(prefix: Path, version: str) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [str(prefix / "bin" / "bad-decisions"), "serve", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env=_environment(), cwd=prefix,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                status, body = _get(f"{base}/healthz")
                break
            except OSError:
                if server.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError(f"bad-decisions serve did not answer: {server.stderr.read()[-500:] if server.poll() is not None else 'timeout'}")
                time.sleep(0.25)
        health = json.loads(body)
        assert status == 200 and health.get("version") == version, f"/healthz reported {health}"
        status, body = _get(f"{base}/v1/round")
        assert status == 200 and json.loads(body).get("result"), "/v1/round did not deal"
        status, body = _get(f"{base}/web/")
        assert status == 200 and "<html" in body.lower(), "/web/ did not serve the web client"
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def check(prefix: Path, root: Path = ROOT) -> list[str]:
    """Return a list of problems; empty means both installed packages work."""
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    bundled = len(list((root / "src/bad_decisions/data/packs").glob("*.json")))
    checks = {
        "bad-decisions --version": lambda: _expect(_run(prefix, "bad-decisions", "--version").strip(), f"bad-decisions {version}"),
        "regret --version": lambda: _expect(_run(prefix, "regret", "--version").strip(), f"regret {version}"),
        "bundled packs": lambda: _expect(len(_run(prefix, "bad-decisions", "--list-packs").splitlines()), bundled),
        "bad-decisions --oneshot": lambda: _expect(bool(_run(prefix, "bad-decisions", "--oneshot").strip()), True),
        "requirements.lock": lambda: _expect(_run(
            prefix, "python", "-c",
            "import importlib.resources as r; print(r.files('bad_decisions').joinpath('requirements.lock').is_file())",
        ).strip(), "True"),
        "no optional extras in the core install": lambda: _expect(_run(
            prefix, "python", "-c",
            "import importlib.util as u; print(all(u.find_spec(m) is None for m in ('boto3', 'aws_cdk', 'textual')))",
        ).strip(), "True"),
        "manual pages": lambda: _expect(sorted(page for page in MANPAGES if (prefix / "share/man/man1" / page).is_file()), sorted(MANPAGES)),
        "bad-decisions serve": lambda: _check_server(prefix, version),
    }
    problems = []
    for name, run in checks.items():
        try:
            run()
        except (AssertionError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            problems.append(f"{name}: {exc}")
    return problems


def _expect(actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_installed.py PREFIX", file=sys.stderr)
        return 2
    problems = check(Path(argv[1]).resolve())
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if not problems:
        print("installed wheels pass the smoke test")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
