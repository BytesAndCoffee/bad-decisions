"""Rootless pack replacement: client staging, the root activator's replace_pack, and rollback."""

from __future__ import annotations

import grp
import hashlib
import importlib.util
import json
import os
import pwd
import stat
from pathlib import Path

import pytest

from bad_decisions import operations
from bad_decisions import archive as archive_module
from bad_decisions import remote as remote_module
from bad_decisions.archive import _pack_payload, export_pack
from bad_decisions.models import ID_PATTERN, Prompt, Pack, Answer
from bad_decisions.packs import load_registry
from conftest import make_pack

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bad_decisions_pack_activator", ROOT / "deploy" / "activate-release.py")
activator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activator)

REQUEST_ID = "20260926T120000Z-deadbeef"
OLD = "pyx-100228-custom-old-pack"
NEW = "furry"
URL = "https://objects.example/packs/furry.carddeck"
USER = pwd.getpwuid(os.getuid()).pw_name
GROUP = grp.getgrgid(os.getgid()).gr_name


def pack(pack_id: str, *, cards: int = 3) -> Pack:
    return make_pack(
        pack_id,
        prompts=tuple(Prompt(id=f"b{i}", text=f"Q{i} _", template=f"Q{i} {{}}", slots=1, pack=pack_id) for i in range(cards)),
        answers=tuple(Answer(id=f"w{i}", text=f"A{i}", pack=pack_id) for i in range(cards * 2)),
    )


def payload(pack_id: str = NEW) -> bytes:
    return _pack_payload(pack(pack_id))


class Host:
    """An APP_ROOT, a service-owned registry, and fakes for systemctl, /healthz, and the release."""

    def __init__(self, tmp_path: Path, monkeypatch, *, healthy=True):
        self.app = tmp_path / "app"
        self.app.mkdir(mode=0o755)
        for name in ("releases", "backups"):
            (self.app / name).mkdir(mode=0o755)
        for name in ("incoming", "activation"):
            (self.app / name).mkdir()
            (self.app / name).chmod(0o2770)
        self.registry = tmp_path / "packs"
        self.registry.mkdir()
        self.registry.chmod(0o755)
        (self.registry / "base.json").write_bytes(payload("base"))
        (self.registry / f"{OLD}.json").write_bytes(payload(OLD))
        (self.registry / f"{OLD}.json").chmod(0o644)
        self.restarts: list[str] = []
        self.healthy = healthy
        self.restart_error: BaseException | None = None
        self.runtime_override: set[str] | None = None
        monkeypatch.setattr(activator, "ROOT_UID", os.getuid())
        monkeypatch.setattr(activator.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(activator, "_run", self._run)
        monkeypatch.setattr(activator, "_healthy", lambda _url, _version, _attempts: self.healthy() if callable(self.healthy) else self.healthy)
        monkeypatch.setattr(activator, "_validate_with_release", self._validate)
        monkeypatch.setattr(activator, "_runtime_pack_ids", self._runtime)

    def _run(self, command):
        assert command[:2] == ["systemctl", "restart"]
        self.restarts.append(command[2])
        if self.restart_error is not None and len(self.restarts) == 1:
            raise self.restart_error

    @staticmethod
    def _validate(_args, data: bytes, new_id: str) -> None:
        try:
            parsed = Pack.model_validate_json(data)
        except ValueError as exc:
            raise activator.ActivationError(f"the deployed release rejected the new pack ({exc})") from exc
        if parsed.metadata.id != new_id:
            raise activator.ActivationError("the deployed release rejected the new pack")

    def _runtime(self, _args) -> set[str]:
        # A restart reloads the registry from disk, exactly as the service does.
        return self.runtime_override if self.runtime_override is not None else set(load_registry(self.registry).ids)

    def stage(self, data: bytes | None = None, **request) -> dict:
        data = payload() if data is None else data
        stage = self.app / "incoming" / REQUEST_ID
        stage.mkdir(exist_ok=True)
        staged = stage / "pack.json"
        if not staged.is_symlink() and not staged.exists():
            staged.write_bytes(data)
        value = {"action": "replace_pack", "request_id": REQUEST_ID, "old_pack_id": OLD, "new_pack_id": NEW, "pack_sha256": hashlib.sha256(data).hexdigest()}
        value.update(request)
        value = {key: item for key, item in value.items() if item is not ...}
        (self.app / "activation" / "request.json").write_text(json.dumps(value))
        return value

    def run(self, *, pack_dir: bool = True) -> int:
        args = ["--app-root", str(self.app), "--service-name", "svc", "--service-user", USER,
                "--deploy-group", GROUP, "--port", "8000", "--health-attempts", "1"]
        return activator.main(args + (["--pack-dir", str(self.registry)] if pack_dir else []))

    def result(self) -> dict:
        return json.loads((self.app / "activation" / "result.json").read_text())

    def snapshot(self) -> dict:
        """Exact registry state: every entry's bytes, inode, mode, owner, and mtime."""
        entries = {}
        for path in sorted(self.registry.iterdir()):
            info = path.lstat()
            entries[path.name] = (path.read_bytes() if stat.S_ISREG(info.st_mode) else os.readlink(path) if path.is_symlink() else None,
                                  info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid, info.st_mtime_ns)
        return entries

    def assert_cleaned(self) -> None:
        assert not (self.app / "activation" / "request.json").exists()
        assert not (self.app / "incoming" / REQUEST_ID).exists()


@pytest.fixture
def host(tmp_path, monkeypatch):
    return Host(tmp_path, monkeypatch)


# --- success ----------------------------------------------------------------

def test_replacement_publishes_new_pack_retires_old_and_leaves_no_partial_files(host):
    old = host.snapshot()[f"{OLD}.json"]
    host.stage()
    assert host.run() == 0
    assert host.result() == {"release_id": REQUEST_ID, "status": "ok", "action": "replace_pack", "old_pack_id": OLD, "new_pack_id": NEW}
    names = sorted(path.name for path in host.registry.iterdir())
    assert names == ["base.json", f"{NEW}.json"]  # no temp, retired, or lock files left behind
    new = host.registry / f"{NEW}.json"
    assert new.read_bytes() == payload()
    assert stat.S_IMODE(new.stat().st_mode) == old[2] and new.stat().st_uid == old[3]
    assert set(load_registry(host.registry).ids) == {"base", NEW}
    assert (host.app / "backups" / f"pack-{REQUEST_ID}" / f"{OLD}.json").read_bytes() == old[0]
    assert host.restarts == ["svc"]
    host.assert_cleaned()


def test_client_and_activator_replace_a_pack_end_to_end(host, tmp_path, monkeypatch):
    archive = export_pack(pack(NEW), tmp_path / "furry.carddeck")
    monkeypatch.setattr(remote_module, "_read_url", lambda url, *, limit, carddeck=False: archive.read_bytes())
    _fixed_request_id(monkeypatch)
    ran = []

    def activator_runs_while_client_waits(_seconds):
        if not ran:
            ran.append(host.run())

    monkeypatch.setattr(operations.time, "sleep", activator_runs_while_client_waits)
    assert operations.pack_replace_local([OLD, URL, "--new-id", NEW, "--app-root", str(host.app)]) == 0
    assert ran == [0]
    assert sorted(path.name for path in host.registry.iterdir()) == ["base.json", f"{NEW}.json"]
    assert (host.registry / f"{NEW}.json").read_bytes() == archive_module._read_archive(archive)[2]


# --- hostile requests: nothing changes, nothing restarts ---------------------

@pytest.mark.parametrize("field,value", [
    ("old_pack_id", "../base"),
    ("old_pack_id", "a/b"),
    ("old_pack_id", ".hidden"),
    ("old_pack_id", ""),
    ("old_pack_id", "Base"),
    ("old_pack_id", "x" * 200),
    ("new_pack_id", "../../etc/furry"),
    ("new_pack_id", "furry.json"),
    ("new_pack_id", OLD),
    ("request_id", "../../root"),
    ("pack_sha256", "abc"),
    ("action", "replace_pack_everything"),
    ("extra", "field"),
    ("old_pack_id", ...),
    ("new_pack_id", 7),
])
def test_hostile_requests_change_nothing(host, field, value):
    before = host.snapshot()
    host.stage(**{field: value})
    assert host.run() == 1
    assert host.snapshot() == before
    assert host.restarts == []
    assert host.result()["status"] == "error"


def test_staged_symlink_hard_link_fifo_and_oversize_are_refused(host, tmp_path):
    stage = host.app / "incoming" / REQUEST_ID
    stage.mkdir()
    staged = stage / "pack.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(payload())
    before = host.snapshot()
    for make in (
        lambda: staged.symlink_to(outside),
        lambda: os.link(outside, staged),
        lambda: os.mkfifo(staged),
        lambda: staged.write_bytes(b" " * (activator.MAX_PACK_BYTES + 1)),
    ):
        make()
        host.stage(pack_sha256=hashlib.sha256(outside.read_bytes()).hexdigest())
        assert host.run() == 1, make
        assert host.snapshot() == before
        stage.mkdir(exist_ok=True)
        if staged.exists() or staged.is_symlink():
            staged.unlink()
    assert host.restarts == []


def test_symlinked_stage_directory_is_refused(host, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "pack.json").write_bytes(payload())
    (host.app / "incoming" / REQUEST_ID).symlink_to(elsewhere)
    before = host.snapshot()
    (host.app / "activation" / "request.json").write_text(json.dumps(
        {"action": "replace_pack", "request_id": REQUEST_ID, "old_pack_id": OLD, "new_pack_id": NEW, "pack_sha256": hashlib.sha256(payload()).hexdigest()}
    ))
    assert host.run() == 1
    assert "plain directory" in host.result()["message"]
    assert host.snapshot() == before


def test_digest_mismatch_is_refused(host):
    before = host.snapshot()
    host.stage(pack_sha256=hashlib.sha256(b"something else").hexdigest())
    assert host.run() == 1
    assert "digest" in host.result()["message"]
    assert host.snapshot() == before


@pytest.mark.parametrize("data", [
    payload("somebody-else"),                                        # declares another id
    payload(NEW).replace(b'"pack":"furry"', b'"pack":"base"', 1),    # a card claims another pack
    b"not json",
])
def test_staged_pack_must_declare_the_new_id(host, data):
    before = host.snapshot()
    host.stage(data)
    assert host.run() == 1
    assert "does not declare the requested new pack id" in host.result()["message"]
    assert host.snapshot() == before


def test_release_schema_validation_runs_before_the_registry_changes(host):
    broken = json.loads(payload())
    del broken["metadata"]["license_id"]
    data = json.dumps(broken).encode()
    before = host.snapshot()
    host.stage(data)
    assert host.run() == 1
    assert "rejected the new pack" in host.result()["message"]
    assert host.snapshot() == before


# --- registry preconditions --------------------------------------------------

def test_missing_old_pack_is_refused(host):
    (host.registry / f"{OLD}.json").unlink()
    before = host.snapshot()
    host.stage()
    assert host.run() == 1
    assert "not in the registry" in host.result()["message"]
    assert host.snapshot() == before


@pytest.mark.parametrize("problem", ["symlink", "hard link", "wrong id", "fifo"])
def test_old_pack_must_be_a_plain_file_declaring_the_old_id(host, tmp_path, problem):
    old = host.registry / f"{OLD}.json"
    target = tmp_path / "real.json"
    target.write_bytes(payload(OLD))
    old.unlink()
    if problem == "symlink":
        old.symlink_to(target)
    elif problem == "hard link":
        os.link(target, old)
    elif problem == "wrong id":
        old.write_bytes(payload("base-copy"))
    else:
        os.mkfifo(old)
    before = host.snapshot()
    host.stage()
    assert host.run() == 1
    assert host.snapshot() == before
    assert host.restarts == []


def test_existing_new_id_is_never_overwritten(host):
    (host.registry / f"{NEW}.json").write_bytes(payload(NEW))
    before = host.snapshot()
    host.stage()
    assert host.run() == 1
    assert "already exists" in host.result()["message"]
    assert host.snapshot() == before


def test_another_file_declaring_the_new_id_is_a_collision(host):
    (host.registry / "renamed-elsewhere.json").write_bytes(payload(NEW))
    before = host.snapshot()
    host.stage()
    assert host.run() == 1
    assert "already declared" in host.result()["message"]
    assert host.snapshot() == before


def test_pack_management_is_off_without_a_configured_registry(host):
    before = host.snapshot()
    host.stage()
    assert host.run(pack_dir=False) == 1
    assert "not enabled" in host.result()["message"]
    assert host.snapshot() == before
    host.assert_cleaned()


def test_writable_registry_directory_is_refused(host):
    host.registry.chmod(0o777)
    try:
        before = host.snapshot()
        host.stage()
        assert host.run() == 1
        assert "unsafe ownership or permissions" in host.result()["message"]
        assert host.snapshot() == before
    finally:
        host.registry.chmod(0o755)


# --- failures after the files changed restore the exact previous state -------

@pytest.mark.parametrize("failure", ["restart fails", "terminated during restart", "health check fails", "old id still served", "new id missing"])
def test_failures_after_replacement_restore_the_exact_registry(host, failure):
    before = host.snapshot()
    if failure == "restart fails":
        host.restart_error = activator.ActivationError("command failed (1): systemctl restart svc")
    elif failure == "terminated during restart":
        host.restart_error = SystemExit(143)
    elif failure == "health check fails":
        host.healthy = False
    elif failure == "old id still served":
        host.runtime_override = {"base", NEW, OLD}
    else:
        host.runtime_override = {"base"}
    host.stage()
    assert host.run() == 1
    assert host.snapshot() == before  # same inode, bytes, mode, owner, and mtime; no new file
    assert host.restarts == ["svc", "svc"]  # the replacement restart, then the restoring one
    assert host.result()["status"] == "error"
    host.assert_cleaned()


def test_restore_restarts_even_when_the_service_stays_unhealthy(host, capsys):
    host.healthy = False
    host.stage()
    assert host.run() == 1
    assert "still unhealthy after restoring" in capsys.readouterr().err
    assert (host.registry / f"{OLD}.json").exists() and not (host.registry / f"{NEW}.json").exists()


# --- client --------------------------------------------------------------------

def _fixed_request_id(monkeypatch):
    class _Now:
        def strftime(self, _format):
            return "20260926T120000Z-"

    class _Datetime:
        @staticmethod
        def now(_timezone):
            return _Now()

    monkeypatch.setattr(operations, "datetime", _Datetime)
    monkeypatch.setattr(operations.secrets, "token_hex", lambda _size: "deadbeef")
    monkeypatch.setattr(operations.platform, "system", lambda: "Linux")


@pytest.fixture
def client_app(tmp_path, monkeypatch):
    app = tmp_path / "client-app"
    (app / "incoming").mkdir(parents=True)
    (app / "activation").mkdir()
    _fixed_request_id(monkeypatch)
    monkeypatch.setattr(operations.time, "sleep", lambda _seconds: None)
    return app


def _serve_archive(monkeypatch, tmp_path, pack_id=NEW):
    archive = export_pack(pack(pack_id), tmp_path / f"{pack_id}-served.carddeck")
    fetched = []

    def read_url(url, *, limit, carddeck=False):
        fetched.append((url, limit, carddeck))
        return archive.read_bytes()

    monkeypatch.setattr(remote_module, "_read_url", read_url)
    return archive, fetched


def test_client_stages_canonical_pack_json_with_an_exact_request(client_app, tmp_path, monkeypatch, capsys):
    archive, fetched = _serve_archive(monkeypatch, tmp_path)
    (client_app / "activation" / "result.json").write_text(json.dumps(
        {"release_id": REQUEST_ID, "status": "ok", "action": "replace_pack", "old_pack_id": OLD, "new_pack_id": NEW}
    ))
    assert operations.pack_replace_local([OLD, URL, "--new-id", NEW, "--app-root", str(client_app)]) == 0
    staged = client_app / "incoming" / REQUEST_ID / "pack.json"
    assert staged.read_bytes() == archive_module._read_archive(archive)[2]
    request = json.loads((client_app / "activation" / "request.json").read_text())
    assert request == {"action": "replace_pack", "request_id": REQUEST_ID, "old_pack_id": OLD, "new_pack_id": NEW,
                       "pack_sha256": hashlib.sha256(staged.read_bytes()).hexdigest()}
    assert activator.parse_request(json.dumps(request).encode()) == request
    assert fetched == [(URL, archive_module.MAX_ARCHIVE_BYTES, True)]
    assert f"Replaced pack {OLD} with {NEW}" in capsys.readouterr().out


def test_client_refuses_an_archive_declaring_another_id(client_app, tmp_path, monkeypatch, capsys):
    _serve_archive(monkeypatch, tmp_path, pack_id="not-furry")
    assert operations.pack_replace_local([OLD, URL, "--new-id", NEW, "--app-root", str(client_app)]) == 1
    assert "does not match downloaded archive" in capsys.readouterr().err
    assert list((client_app / "incoming").iterdir()) == []
    assert not (client_app / "activation" / "request.json").exists()


@pytest.mark.parametrize("url", ["http://objects.example/packs/furry.carddeck", "https://objects.example/packs/furry.zip", "file:///etc/passwd"])
def test_client_requires_an_https_carddeck_url(client_app, capsys, url):
    assert operations.pack_replace_local([OLD, url, "--new-id", NEW, "--app-root", str(client_app)]) == 1
    assert list((client_app / "incoming").iterdir()) == []


@pytest.mark.parametrize("argv", [["../base", URL, "--new-id", NEW], [OLD, URL, "--new-id", "../furry"], [OLD, URL, "--new-id", OLD], [OLD, URL]])
def test_client_rejects_bad_ids_before_downloading(client_app, monkeypatch, argv):
    monkeypatch.setattr(remote_module, "_read_url", lambda *_a, **_k: pytest.fail("must not download"))
    with pytest.raises(SystemExit):
        operations.pack_replace_local([*argv, "--app-root", str(client_app)])


def test_client_refuses_while_another_request_is_pending(client_app, tmp_path, monkeypatch, capsys):
    _serve_archive(monkeypatch, tmp_path)
    (client_app / "activation" / "request.json").write_text("{}")
    with pytest.raises(SystemExit):
        operations.pack_replace_local([OLD, URL, "--new-id", NEW, "--app-root", str(client_app)])
    assert "still pending" in capsys.readouterr().err
    assert list((client_app / "incoming").iterdir()) == []
    assert (client_app / "activation" / "request.json").read_text() == "{}"


def test_client_reports_failure_with_the_activator_log(client_app, tmp_path, monkeypatch, capsys):
    _serve_archive(monkeypatch, tmp_path)
    (client_app / "activation" / "log.txt").write_text(f"request {REQUEST_ID}\npack replacement failed; restoring {OLD}.json\n")
    (client_app / "activation" / "result.json").write_text(json.dumps({"release_id": REQUEST_ID, "status": "error", "message": "health check failed after the replacement"}))
    assert operations.pack_replace_local([OLD, URL, "--new-id", NEW, "--app-root", str(client_app)]) == 1
    err = capsys.readouterr().err
    assert "Pack replacement failed: health check failed" in err and f"restoring {OLD}.json" in err


def test_activator_limits_mirror_the_project(tmp_path):
    assert activator.PACK_ID_RE.pattern == ID_PATTERN
    assert activator.MAX_PACK_BYTES == archive_module.MAX_MEMBER_BYTES


def test_bootstrap_makes_pack_replacement_opt_in():
    bootstrap = (ROOT / "deploy/bootstrap-rootless.sh").read_text()
    service = (ROOT / "deploy/rootless-activate.service.template").read_text()
    assert "@PACK_DIR_ARG@" in service
    assert 'PACK_DIR=${PACK_DIR:-}' in bootstrap and "! -L ${PACK_DIR}" in bootstrap
    assert "PACK_DIR" in (ROOT / "deploy.sh").read_text()
