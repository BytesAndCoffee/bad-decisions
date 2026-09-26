from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from bad_decisions import __version__, operations


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bad_decisions_release_activator", ROOT / "deploy" / "activate-release.py")
assert SPEC and SPEC.loader
activator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activator)


class _FixedNow:
    def strftime(self, _format):
        return "20260926T120000Z-"


class _FixedDatetime:
    @staticmethod
    def now(_timezone):
        return _FixedNow()


def test_local_deploy_stages_digest_and_atomic_request(tmp_path, monkeypatch, capsys):
    app = tmp_path / "app"
    (app / "incoming").mkdir(parents=True)
    (app / "activation").mkdir()
    wheel = tmp_path / f"bad_decisions-{__version__}-py3-none-any.whl"
    wheel.write_bytes(b"tested-wheel")
    release_id = "20260926T120000Z-deadbeef"
    (app / "activation/result.json").write_text(
        json.dumps({"release_id": release_id, "status": "ok", "version": __version__})
    )
    monkeypatch.setattr(operations, "datetime", _FixedDatetime)
    monkeypatch.setattr(operations.secrets, "token_hex", lambda _size: "deadbeef")
    monkeypatch.setattr(operations.platform, "system", lambda: "Linux")

    assert operations.deploy_local(["--app-root", str(app), "--wheel", str(wheel)]) == 0
    staged = app / "incoming" / release_id / wheel.name
    assert staged.read_bytes() == b"tested-wheel"
    request = json.loads((app / "activation/request.json").read_text())
    assert request == {
        "release_id": release_id,
        "sha256": hashlib.sha256(b"tested-wheel").hexdigest(),
        "version": __version__,
        "wheel": wheel.name,
    }
    assert "Deployment complete" in capsys.readouterr().out


def _request_tree(tmp_path: Path):
    app = tmp_path / "app"
    incoming = app / "incoming" / "20260926T120000Z-deadbeef"
    incoming.mkdir(parents=True)
    (app / "activation").mkdir()
    wheel = incoming / "bad_decisions-1.8.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    request = {
        "release_id": incoming.name,
        "wheel": wheel.name,
        "version": "1.8.0",
        "sha256": hashlib.sha256(b"wheel").hexdigest(),
    }
    (app / "activation/request.json").write_text(json.dumps(request))
    return app, incoming, wheel, request


def test_activator_accepts_one_plain_digest_matched_wheel(tmp_path):
    app, _incoming, wheel, request = _request_tree(tmp_path)
    loaded, loaded_wheel = activator.load_request(app)
    assert loaded == request
    assert loaded_wheel == wheel


@pytest.mark.parametrize("field,value", [
    ("release_id", "../../root"),
    ("wheel", "../bad_decisions-1.8.0-py3-none-any.whl"),
    ("sha256", "not-a-digest"),
    ("version", "9.9.9"),
])
def test_activator_rejects_hostile_request_fields(tmp_path, field, value):
    app, _incoming, _wheel, request = _request_tree(tmp_path)
    request[field] = value
    (app / "activation/request.json").write_text(json.dumps(request))
    with pytest.raises(activator.ActivationError):
        activator.load_request(app)


def test_activator_rejects_symlinked_wheel_and_digest_changes(tmp_path):
    app, incoming, wheel, request = _request_tree(tmp_path)
    wheel.unlink()
    outside = tmp_path / "outside.whl"
    outside.write_bytes(b"wheel")
    wheel.symlink_to(outside)
    with pytest.raises(activator.ActivationError, match="regular"):
        activator.load_request(app)

    wheel.unlink()
    wheel.write_bytes(b"changed")
    with pytest.raises(activator.ActivationError, match="digest"):
        activator.load_request(app)


def test_bootstrap_assets_keep_the_privilege_boundary_narrow():
    bootstrap = (ROOT / "deploy/bootstrap-rootless.sh").read_text()
    service = (ROOT / "deploy/rootless-activate.service.template").read_text()
    assert "sudoers" not in bootstrap.lower()
    assert "usermod --append --groups" in bootstrap
    assert "ExecStart=/usr/libexec/@APP_NAME@-activate" in service
    assert "User=@" not in service
