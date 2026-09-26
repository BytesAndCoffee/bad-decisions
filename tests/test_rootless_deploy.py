from __future__ import annotations

import grp
import hashlib
import importlib.util
import json
import os
import pwd
from pathlib import Path

import pytest

from bad_decisions import __version__, operations


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bad_decisions_release_activator", ROOT / "deploy" / "activate-release.py")
assert SPEC and SPEC.loader
activator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activator)

RELEASE_ID = "20260926T120000Z-deadbeef"
PREVIOUS_ID = "20260925T120000Z"
LOCK = b"# pinned\nfastapi==0.115.0\npydantic==2.9.2\n"
USER = pwd.getpwuid(os.getuid()).pw_name
GROUP = grp.getgrgid(os.getgid()).gr_name


class _FixedNow:
    def strftime(self, _format):
        return "20260926T120000Z-"


class _FixedDatetime:
    @staticmethod
    def now(_timezone):
        return _FixedNow()


@pytest.fixture(autouse=True)
def _unprivileged_root(monkeypatch):
    """Let the activator treat the test user as root; it never runs real commands here."""
    monkeypatch.setattr(activator, "ROOT_UID", os.getuid())
    monkeypatch.setattr(activator.time, "sleep", lambda _seconds: None)


def test_local_deploy_stages_digests_and_atomic_request(tmp_path, monkeypatch, capsys):
    app = tmp_path / "app"
    (app / "incoming").mkdir(parents=True)
    (app / "activation").mkdir()
    wheel = tmp_path / f"bad_decisions-{__version__}-py3-none-any.whl"
    wheel.write_bytes(b"tested-wheel")
    lock = tmp_path / "requirements.lock"
    lock.write_bytes(LOCK)
    (app / "activation/result.json").write_text(
        json.dumps({"release_id": RELEASE_ID, "status": "ok", "version": __version__})
    )
    monkeypatch.setattr(operations, "datetime", _FixedDatetime)
    monkeypatch.setattr(operations.secrets, "token_hex", lambda _size: "deadbeef")
    monkeypatch.setattr(operations.platform, "system", lambda: "Linux")

    assert operations.deploy_local(["--app-root", str(app), "--wheel", str(wheel), "--requirements", str(lock)]) == 0
    stage = app / "incoming" / RELEASE_ID
    assert (stage / wheel.name).read_bytes() == b"tested-wheel"
    assert (stage / "requirements.lock").read_bytes() == LOCK
    request = json.loads((app / "activation/request.json").read_text())
    assert request == {
        "release_id": RELEASE_ID,
        "requirements_sha256": hashlib.sha256(LOCK).hexdigest(),
        "sha256": hashlib.sha256(b"tested-wheel").hexdigest(),
        "version": __version__,
        "wheel": wheel.name,
    }
    assert "Deployment complete" in capsys.readouterr().out
    # The staged request is accepted by the activator unchanged.
    for directory in (app, app / "incoming", app / "activation"):
        directory.chmod(0o2770 if directory != app else 0o755)
    (app / "releases").mkdir(mode=0o755)
    loaded, wheel_bytes, lock_bytes = activator.load_request(app)
    assert loaded == request and wheel_bytes == b"tested-wheel" and lock_bytes == LOCK


def test_local_deploy_requires_the_lock(tmp_path, monkeypatch):
    app = tmp_path / "app"
    (app / "incoming").mkdir(parents=True)
    (app / "activation").mkdir()
    wheel = tmp_path / f"bad_decisions-{__version__}-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    monkeypatch.setattr(operations.platform, "system", lambda: "Linux")
    with pytest.raises(SystemExit):
        operations.deploy_local(["--app-root", str(app), "--wheel", str(wheel), "--requirements", str(tmp_path / "missing.lock")])
    assert not any((app / "incoming").iterdir())


def _write_request(app: Path, request: dict) -> None:
    (app / "activation/request.json").write_text(json.dumps(request))


def _request_tree(tmp_path: Path, version: str = "1.8.0"):
    app = tmp_path / "app"
    app.mkdir(mode=0o755)
    (app / "releases").mkdir(mode=0o755)
    for name in ("incoming", "activation"):
        (app / name).mkdir()
        (app / name).chmod(0o2770)
    incoming = app / "incoming" / RELEASE_ID
    incoming.mkdir()
    wheel = incoming / f"bad_decisions-{version}-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    (incoming / "requirements.lock").write_bytes(LOCK)
    request = {
        "release_id": RELEASE_ID,
        "wheel": wheel.name,
        "version": version,
        "sha256": hashlib.sha256(b"wheel").hexdigest(),
        "requirements_sha256": hashlib.sha256(LOCK).hexdigest(),
    }
    _write_request(app, request)
    return app, incoming, wheel, request


def test_activator_accepts_one_plain_digest_matched_wheel(tmp_path):
    app, _incoming, _wheel, request = _request_tree(tmp_path)
    assert activator.load_request(app) == (request, b"wheel", LOCK)


@pytest.mark.parametrize("field,value", [
    ("release_id", "../../root"),
    ("release_id", "20260926T120000Z"),
    ("wheel", "../bad_decisions-1.8.0-py3-none-any.whl"),
    ("sha256", "not-a-digest"),
    ("requirements_sha256", "0" * 63),
    ("version", "9.9.9"),
    ("extra", "field"),
])
def test_activator_rejects_hostile_request_fields(tmp_path, field, value):
    app, _incoming, _wheel, request = _request_tree(tmp_path)
    request[field] = value
    _write_request(app, request)
    with pytest.raises(activator.ActivationError):
        activator.load_request(app)


def test_activator_rejects_symlinked_hard_linked_and_changed_wheels(tmp_path):
    app, _incoming, wheel, _request = _request_tree(tmp_path)
    wheel.unlink()
    outside = tmp_path / "outside.whl"
    outside.write_bytes(b"wheel")
    wheel.symlink_to(outside)
    with pytest.raises(activator.ActivationError, match="regular"):
        activator.load_request(app)

    wheel.unlink()
    os.link(outside, wheel)
    with pytest.raises(activator.ActivationError, match="unlinked"):
        activator.load_request(app)

    wheel.unlink()
    wheel.write_bytes(b"changed")
    with pytest.raises(activator.ActivationError, match="digest"):
        activator.load_request(app)


def test_activator_rejects_changed_lock_and_symlinked_stage(tmp_path):
    app, incoming, _wheel, _request = _request_tree(tmp_path)
    (incoming / "requirements.lock").write_bytes(LOCK + b"extra==1.0\n")
    with pytest.raises(activator.ActivationError, match="requirements digest"):
        activator.load_request(app)

    elsewhere = tmp_path / "elsewhere"
    incoming.rename(elsewhere)
    incoming.symlink_to(elsewhere)
    with pytest.raises(activator.ActivationError, match="plain directory"):
        activator.load_request(app)


def test_activator_never_blocks_on_fifo_or_device_requests(tmp_path):
    app, _incoming, _wheel, _request = _request_tree(tmp_path)
    request = app / "activation/request.json"
    request.unlink()
    os.mkfifo(request)
    with pytest.raises(activator.ActivationError, match="regular"):
        activator.load_request(app)

    request.unlink()
    request.symlink_to("/dev/zero")
    with pytest.raises(activator.ActivationError, match="regular"):
        activator.load_request(app)

    request.unlink()
    request.write_bytes(b" " * (activator.MAX_REQUEST_BYTES + 1))
    with pytest.raises(activator.ActivationError, match="too large"):
        activator.load_request(app)


def test_activator_rejects_untrusted_directories(tmp_path):
    app, _incoming, _wheel, _request = _request_tree(tmp_path)
    app.chmod(0o775)
    with pytest.raises(activator.ActivationError, match="unsafe ownership"):
        activator.load_request(app)
    app.chmod(0o755)
    (app / "activation").chmod(0o2777)
    with pytest.raises(activator.ActivationError, match="unsafe ownership"):
        activator.load_request(app)


@pytest.mark.parametrize("line", [
    "--index-url https://evil.example/simple",
    "-r /etc/other.txt",
    "-e /srv/checkout",
    "evil @ https://evil.example/evil.whl",
    "git+https://evil.example/repo.git",
    "fastapi>=0.1",
    "fastapi==0.1 --hash=sha256:00",
    "/tmp/evil.whl",
])
def test_activator_lock_accepts_only_exact_pins(line):
    activator.validate_lock(b"fastapi==0.115.0\n")
    with pytest.raises(activator.ActivationError, match="pins"):
        activator.validate_lock(f"fastapi==0.115.0\n{line}\n".encode())


def test_repository_lock_passes_activator_validation():
    activator.validate_lock((ROOT / "requirements.lock").read_bytes())


def test_result_write_replaces_planted_symlink_without_following_it(tmp_path):
    app, _incoming, _wheel, _request = _request_tree(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("do not touch")
    victim.chmod(0o600)
    (app / "activation/result.json").symlink_to(victim)
    activator._write_result(app, os.getgid(), {"release_id": RELEASE_ID, "status": "ok"})
    assert victim.read_text() == "do not touch"
    assert (victim.stat().st_mode & 0o777) == 0o600
    result = app / "activation/result.json"
    assert not result.is_symlink()
    assert json.loads(result.read_text())["status"] == "ok"
    assert (result.stat().st_mode & 0o777) == 0o640
    assert [path.name for path in (app / "activation").iterdir() if path.name.startswith(".result-")] == []


def test_freeze_rejects_hard_links_and_late_writable_entries(tmp_path):
    tree = tmp_path / "release"
    (tree / "lib").mkdir(parents=True)
    (tree / "lib/module.py").write_text("x = 1\n")
    (tree / "bin").mkdir()
    (tree / "bin/tool").write_text("#!/bin/sh\n")
    (tree / "bin/tool").chmod(0o755)
    (tree / "bin/python").symlink_to("/usr/bin/python3")
    activator._freeze_tree(tree, os.getuid(), os.getgid())
    activator._verify_frozen(tree, os.getuid())
    assert (tree / "lib/module.py").stat().st_mode & 0o777 == 0o440
    assert (tree / "bin/tool").stat().st_mode & 0o777 == 0o550
    assert (tree / "lib").stat().st_mode & 0o777 == 0o750

    # A file the service user slipped in after its directory was listed.
    (tree / "lib").chmod(0o770)
    (tree / "lib/late.py").write_text("")
    with pytest.raises(activator.ActivationError, match="changed while"):
        activator._verify_frozen(tree, os.getuid())

    linked = tmp_path / "linked"
    (linked / "lib").mkdir(parents=True)
    outside = tmp_path / "service-data.sqlite3"
    outside.write_text("data")
    os.link(outside, linked / "lib/data")
    with pytest.raises(activator.ActivationError, match="hard-linked"):
        activator._freeze_tree(linked, os.getuid(), os.getgid())
    assert outside.stat().st_mode & 0o200


class _FakeHost:
    """Stands in for runuser, pip, systemctl, and the health endpoint."""

    def __init__(self, monkeypatch, *, healthy=True, interrupt=None):
        self.commands: list[list[str]] = []
        self.restarts = 0
        self.healthy = healthy
        self.interrupt = interrupt
        self.installed: list[bytes] = []
        monkeypatch.setattr(activator, "_as_user", self.as_user)
        monkeypatch.setattr(activator, "_installed_version", lambda _user, _python: "1.8.0")
        monkeypatch.setattr(activator, "_run", self.run)
        monkeypatch.setattr(activator, "_healthy", lambda _url, _version, _attempts: self.healthy)

    def as_user(self, user, command):
        assert user == USER
        self.commands.append(command)
        if self.interrupt and self.interrupt in command:
            raise SystemExit(143)
        if command[1:3] == ["-m", "venv"]:
            bin_dir = Path(command[3]) / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "pip").write_text("#!/bin/sh\n")
            (bin_dir / "pip").chmod(0o755)
            (bin_dir / "python").symlink_to("/usr/bin/python3")
        else:
            self.installed.append(Path(command[-1]).read_bytes())

    def run(self, command):
        assert command[:2] == ["systemctl", "restart"]
        self.restarts += 1


def _activation_args(app: Path) -> list[str]:
    return [
        "--app-root", str(app), "--service-name", "bad-decisions", "--service-user", USER,
        "--deploy-group", GROUP, "--port", "8001", "--health-attempts", "1",
    ]


def _with_previous_release(app: Path) -> Path:
    previous = app / "releases" / PREVIOUS_ID
    previous.mkdir()
    (app / "current").symlink_to(previous)
    return previous


def _result(app: Path) -> dict:
    return json.loads((app / "activation/result.json").read_text())


def test_activation_installs_verified_bytes_freezes_switches_and_cleans_up(tmp_path, monkeypatch):
    app, incoming, _wheel, _request = _request_tree(tmp_path)
    _with_previous_release(app)
    host = _FakeHost(monkeypatch)

    assert activator.main(_activation_args(app)) == 0

    release = app / "releases" / RELEASE_ID
    assert (app / "current").resolve() == release
    assert host.installed == [LOCK, b"wheel"]
    lock_install, wheel_install = host.commands[1], host.commands[2]
    assert "--requirement" in lock_install and "--no-deps" in wheel_install
    assert not Path(wheel_install[-1]).exists(), "private copy must be removed"
    assert (release / ".venv/bin/pip").stat().st_mode & 0o777 == 0o550
    assert (app / "good-releases").read_text() == f"{PREVIOUS_ID}\n{RELEASE_ID}\n"
    assert _result(app) == {"release_id": RELEASE_ID, "status": "ok", "version": "1.8.0"}
    assert not (app / "activation/request.json").exists()
    assert not incoming.exists()
    assert host.restarts == 1


def test_failed_health_check_restores_previous_release(tmp_path, monkeypatch):
    app, incoming, _wheel, _request = _request_tree(tmp_path)
    previous = _with_previous_release(app)
    (app / "good-releases").write_text(f"{PREVIOUS_ID}\n")
    host = _FakeHost(monkeypatch, healthy=False)

    assert activator.main(_activation_args(app)) == 1

    assert (app / "current").resolve() == previous
    assert not (app / "releases" / RELEASE_ID).exists()
    assert (app / "good-releases").read_text() == f"{PREVIOUS_ID}\n"
    assert _result(app) == {"release_id": RELEASE_ID, "status": "error", "message": "new release failed its health check"}
    assert not (app / "activation/request.json").exists()
    assert not incoming.exists()
    assert host.restarts == 2


def test_failed_first_activation_does_not_leave_a_dangling_current(tmp_path, monkeypatch):
    app, _incoming, _wheel, _request = _request_tree(tmp_path)
    _FakeHost(monkeypatch, healthy=False)
    assert activator.main(_activation_args(app)) == 1
    assert not (app / "current").is_symlink()
    assert not (app / "releases" / RELEASE_ID).exists()


def test_termination_during_install_removes_partial_release(tmp_path, monkeypatch):
    app, _incoming, _wheel, _request = _request_tree(tmp_path)
    previous = _with_previous_release(app)
    host = _FakeHost(monkeypatch, interrupt="--no-deps")

    assert activator.main(_activation_args(app)) == 1

    assert (app / "current").resolve() == previous
    assert not (app / "releases" / RELEASE_ID).exists()
    assert _result(app)["status"] == "error"
    assert not (app / "activation/request.json").exists()
    assert host.restarts == 0


def test_invalid_request_is_removed_so_the_path_unit_stops(tmp_path, monkeypatch):
    app, _incoming, _wheel, request = _request_tree(tmp_path)
    request["version"] = "9.9.9"
    _write_request(app, request)
    host = _FakeHost(monkeypatch)
    assert activator.main(_activation_args(app)) == 1
    assert not (app / "activation/request.json").exists()
    assert _result(app)["release_id"] == "unknown"
    assert host.commands == []


def test_existing_release_is_never_replaced(tmp_path, monkeypatch):
    app, _incoming, _wheel, _request = _request_tree(tmp_path)
    existing = app / "releases" / RELEASE_ID
    existing.mkdir()
    (existing / "marker").write_text("keep")
    host = _FakeHost(monkeypatch)
    assert activator.main(_activation_args(app)) == 1
    assert (existing / "marker").read_text() == "keep"
    assert _result(app)["message"] == "release already exists"
    assert host.commands == []


def test_bootstrap_assets_keep_the_privilege_boundary_narrow():
    bootstrap = (ROOT / "deploy/bootstrap-rootless.sh").read_text()
    service = (ROOT / "deploy/rootless-activate.service.template").read_text()
    assert "sudoers" not in bootstrap.lower()
    assert "usermod --append --groups" in bootstrap
    assert "ExecStart=@PYTHON@ -I /usr/libexec/@APP_NAME@-activate" in service
    assert "User=@" not in service
    for directive in ("TimeoutStartSec=", "ProtectSystem=full", "NoNewPrivileges=true", "RestrictSUIDSGID=true", "PrivateTmp=true"):
        assert directive in service
