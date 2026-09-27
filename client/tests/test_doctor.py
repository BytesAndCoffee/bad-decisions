from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bad_decisions_client import cli, doctor


@pytest.mark.parametrize("prefix,base,environ,expected", [
    ("/opt/homebrew/Cellar/regret/1.8.5/libexec", "/opt/homebrew/opt/python@3.13", {}, "homebrew"),
    ("/home/linuxbrew/.linuxbrew/opt/regret/libexec", "/usr", {"HOMEBREW_PREFIX": "/home/linuxbrew/.linuxbrew"}, "homebrew"),
    ("/home/u/.local/share/pipx/venvs/bad-decisions-client", "/usr", {}, "pipx"),
    ("/home/u/project/.venv", "/usr", {}, "venv"),
    ("/home/u/.pyenv/versions/3.11.0", "/home/u/.pyenv/versions/3.11.0", {"PYENV_ROOT": "/home/u/.pyenv"}, "pyenv"),
    ("/usr", "/usr", {}, "pip"),
])
def test_install_method(prefix, base, environ, expected):
    assert doctor.install_method(prefix, base, environ) == expected


def _manpage(tmp_path: Path) -> Path:
    page = tmp_path / "share/man/man1/regret.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH REGRET 1\n")
    return page


def test_a_findable_page_passes(capsys):
    report = doctor.Report()
    doctor.check_manpage(report, "pip", page=None, found="/usr/share/man/man1/regret.1", platform="linux")
    assert report.failures == 0 and "man regret finds" in capsys.readouterr().out


def test_pyenv_gets_the_pyenv_prefix_fix_and_homebrew_on_macos(tmp_path, capsys):
    report = doctor.Report()
    doctor.check_manpage(report, "pyenv", page=_manpage(tmp_path), found=None, platform="darwin")
    out = capsys.readouterr().out
    assert report.failures == 1
    assert 'export MANPATH=":$(pyenv prefix)/share/man"' in out
    assert doctor.HOMEBREW in out


def test_other_installs_get_their_own_manpath_and_pipx(tmp_path, capsys):
    page = _manpage(tmp_path)
    doctor.check_manpage(doctor.Report(), "venv", page=page, found=None, platform="linux")
    out = capsys.readouterr().out
    assert f'export MANPATH=":{tmp_path / "share/man"}"' in out
    assert "pipx install bad-decisions-client" in out and doctor.HOMEBREW not in out


def test_homebrew_and_pipx_installs_are_not_told_to_reinstall(tmp_path, capsys):
    doctor.check_manpage(doctor.Report(), "homebrew", page=_manpage(tmp_path), found=None, platform="darwin")
    assert "brew install" not in capsys.readouterr().out
    doctor.check_manpage(doctor.Report(), "pipx", page=tmp_path / "share/man/man1/regret.1", found=None, platform="linux")
    assert "pipx install" not in capsys.readouterr().out


def test_missing_page_suggests_homebrew_only_on_macos(capsys):
    report = doctor.Report()
    doctor.check_manpage(report, "pip", page=None, found=None, platform="darwin")
    assert report.failures == 0 and doctor.HOMEBREW in capsys.readouterr().out
    doctor.check_manpage(doctor.Report(), "pip", page=None, found=None, platform="linux")
    assert doctor.HOMEBREW not in capsys.readouterr().out


def test_windows_skips_manual_pages(capsys):
    report = doctor.Report()
    doctor.check_manpage(report, "pip", page=None, found=None, platform="win32")
    assert report.failures == 0 and "not used on Windows" in capsys.readouterr().out


def test_man_finds_uses_man_w(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/usr/bin/man")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "/x/regret.1\n", "")

    assert doctor.man_finds(run=run) == "/x/regret.1" and calls == [["man", "-w", "regret"]]
    assert doctor.man_finds(run=lambda command, **_k: subprocess.CompletedProcess(command, 16, "", "No manual entry")) is None
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    assert doctor.man_finds(run=lambda *_a, **_k: pytest.fail("no man binary")) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_config_permissions(tmp_path, capsys):
    config = tmp_path / ".regret.env"
    doctor.check_config(doctor.Report(), config)
    assert "created on first use" in capsys.readouterr().out
    config.write_text("REGRET_CONSEQUENCES=regret\n")
    config.chmod(0o644)
    doctor.check_config(doctor.Report(), config)
    out = capsys.readouterr().out
    assert "readable by other users" in out and f"chmod 600 {config}" in out


def test_service_check_reports_outage_and_newer_service(capsys):
    report = doctor.Report()

    def down(*_a, **_k):
        raise RuntimeError("API unavailable: refused")

    doctor.check_service(report, "https://x", "1.8.5", down, cli._is_newer)
    assert report.failures == 1 and "did not answer" in capsys.readouterr().out
    doctor.check_service(report, "https://x", "1.8.5", lambda *_a, **_k: ({"version": "9.0.0"}, {}), cli._is_newer)
    assert "newer than this regret 1.8.5" in capsys.readouterr().out


def test_regret_doctor_exits_nonzero_on_failure_and_changes_nothing(tmp_path, monkeypatch, capsys):
    config = tmp_path / ".regret.env"
    monkeypatch.setattr(cli, "CONFIG_PATH", config)
    monkeypatch.setattr(cli, "_request_json", lambda *_a, **_k: ({"version": "1.0.0"}, {}))
    monkeypatch.setattr(doctor, "installed_manpage", lambda: tmp_path / "regret.1")
    monkeypatch.setattr(doctor, "man_finds", lambda: None)
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    assert cli.run(["doctor"]) == 1
    captured = capsys.readouterr()
    assert "FAIL  man regret cannot find" in captured.out and "1 check(s) failed" in captured.err
    assert not config.exists(), "doctor must not write preferences"
    monkeypatch.setattr(doctor, "man_finds", lambda: "/x/regret.1")
    assert cli.run(["doctor"]) == 0
