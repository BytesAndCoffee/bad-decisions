"""``regret doctor``: check this installation and print fixes; changes nothing.

Installers cannot run code after installing a wheel, so the shell integration a
package would like (for example, making ``man regret`` work behind pyenv shims)
is checked here, on request, and reported as the exact line to add yourself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Callable, Optional

DISTRIBUTION = "bad-decisions-client"
HOMEBREW = "brew install bytesandcoffee/tap/regret"


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, message: str) -> None:
        print(f"ok    {message}")

    def warn(self, message: str) -> None:
        print(f"warn  {message}")

    def fail(self, message: str) -> None:
        print(f"FAIL  {message}")
        self.failures += 1

    def fix(self, message: str) -> None:
        print(f"      {message}")


def install_method(prefix: str = sys.prefix, base_prefix: str = sys.base_prefix, environ=os.environ) -> str:
    """How this copy was installed, judged from its interpreter prefix."""
    real = os.path.realpath(prefix)
    homebrew = environ.get("HOMEBREW_PREFIX")
    if "/Cellar/" in real or (homebrew and real.startswith(os.path.realpath(homebrew) + os.sep)):
        return "homebrew"
    if f"{os.sep}pipx{os.sep}venvs{os.sep}" in real:
        return "pipx"
    if prefix != base_prefix:
        return "venv"
    pyenv_root = environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
    if real.startswith(os.path.join(os.path.realpath(pyenv_root), "versions") + os.sep):
        return "pyenv"
    return "pip"


def installed_manpage() -> Optional[Path]:
    """The regret.1 this distribution installed, wherever the installer put it."""
    try:
        distribution = metadata.distribution(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return None
    for entry in distribution.files or ():
        if entry.name == "regret.1":
            path = Path(distribution.locate_file(entry)).resolve()
            if path.is_file():
                return path
    return None


def man_finds(name: str = "regret", run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Optional[str]:
    """Path that ``man -w NAME`` resolves, or None when man cannot find it."""
    if shutil.which("man") is None:
        return None
    try:
        completed = run(["man", "-w", name], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = completed.stdout.strip().splitlines()
    return lines[0] if completed.returncode == 0 and lines else None


def check_manpage(report: Report, method: str, *, page: Optional[Path], found: Optional[str], platform: Optional[str] = None) -> None:
    platform = sys.platform if platform is None else platform
    if platform.startswith("win"):
        report.ok("manual pages are not used on Windows; see regret --help")
        return
    if found:
        report.ok(f"man regret finds {found}")
        return
    if page is None:
        report.warn("this copy was installed without its manual page")
        if platform == "darwin":
            report.fix(f"On macOS, the suggested install is Homebrew: {HOMEBREW}")
        return
    report.fail(f"man regret cannot find {page}")
    manpath = page.parent.parent
    if method == "pyenv":
        report.fix("pyenv puts shims on PATH, so man never looks in the interpreter's prefix. Add to your shell profile:")
        report.fix('export MANPATH=":$(pyenv prefix)/share/man"')
    else:
        report.fix("Add to your shell profile (the leading ':' keeps the system manual pages):")
        report.fix(f'export MANPATH=":{manpath}"')
    if platform == "darwin" and method != "homebrew":
        report.fix(f"Or install with Homebrew, which needs no shell changes: {HOMEBREW}")
    elif method != "pipx":
        report.fix("Or install with pipx, which links the page into ~/.local/share/man: pipx install bad-decisions-client")


def check_path(report: Report) -> None:
    command = shutil.which("regret")
    if command:
        report.ok(f"regret on PATH is {command}")
    else:
        report.warn("regret is not on PATH; this copy only runs through its full path or python -m")


def check_config(report: Report, config: Path) -> None:
    if not config.exists():
        report.ok(f"no preferences saved yet ({config} is created on first use)")
        return
    mode = config.stat().st_mode & 0o777
    if mode & 0o077 and not sys.platform.startswith("win"):
        report.warn(f"{config} is readable by other users (mode {mode:o}); it holds your pseudonymous identity")
        report.fix(f"chmod 600 {config}")
    else:
        report.ok(f"preferences in {config}")


def check_service(report: Report, api_url: str, current: str, request_json, is_newer) -> None:
    try:
        payload, _headers = request_json(f"{api_url}/healthz", timeout=5.0)
        version = payload.get("version")
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        report.fail(f"{api_url} did not answer: {exc}")
        return
    report.ok(f"{api_url} is up (service {version})")
    if version and is_newer(str(version), current):
        report.warn(f"the service runs {version}, newer than this regret {current}")
        report.fix("Upgrade with the tool you installed regret with (brew upgrade, pipx upgrade, or pip install --upgrade).")
