from __future__ import annotations

import http.server
import json
import shutil
import threading
from pathlib import Path

import pytest

from bad_decisions import __version__, cli, doctor

PREFIX = "/bad-decisions"


def _serve(responses: dict[str, tuple[int, dict[str, str], str]]):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = responses.get(self.path, (404, {}, ""))
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}{PREFIX}"


def _healthy(**overrides):
    responses = {
        f"{PREFIX}/healthz": (200, {}, json.dumps({"status": "ok", "version": __version__, "pack_count": 2})),
        f"{PREFIX}/v2/packs": (200, {}, json.dumps([{"id": "a"}, {"id": "b"}])),
        f"{PREFIX}/v2/round": (200, {}, json.dumps({"result": "A bad decision."})),
        f"{PREFIX}/web/": (200, {}, "<!doctype html><html></html>"),
        f"{PREFIX}/docs/": (307, {"Location": f"{PREFIX}/docs"}, ""),
    }
    responses.update(overrides)
    return responses


@pytest.fixture
def served():
    servers = []

    def start(responses):
        server, url = _serve(responses)
        servers.append(server)
        return url

    yield start
    for server in servers:
        server.shutdown()


def _app_root(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    (app / "activation").mkdir(parents=True)
    (app / "incoming").mkdir()
    return app


def test_a_healthy_deployment_passes_every_check(served, tmp_path, capsys):
    url = served(_healthy())
    assert cli.run(["doctor", "--url", url, "--app-root", str(_app_root(tmp_path)), "--min-free-mb", "0"]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and out.count("ok ") == 7


@pytest.mark.parametrize("override,message", [
    ({f"{PREFIX}/healthz": (200, {}, json.dumps({"status": "ok", "version": "0.0.1", "pack_count": 2}))}, "expected " + __version__),
    ({f"{PREFIX}/v2/packs": (200, {}, json.dumps([{"id": "a"}]))}, "lists 1 packs but /healthz counts 2"),
    ({f"{PREFIX}/v2/round": (500, {}, "{}")}, "/v2/round failed: HTTP 500"),
    ({f"{PREFIX}/web/": (404, {}, "")}, "/web/ returned HTTP 404"),
    ({f"{PREFIX}/docs/": (307, {"Location": "/docs"}, "")}, "/docs/ redirects outside"),
])
def test_each_broken_surface_is_reported(served, capsys, override, message):
    url = served(_healthy(**override))
    assert doctor.main(["--url", url, "--app-root", "/nonexistent"]) == 1
    captured = capsys.readouterr()
    assert message in captured.out and "1 check(s) failed" in captured.err


def test_any_version_accepts_an_older_service(served, capsys):
    url = served(_healthy(**{f"{PREFIX}/healthz": (200, {}, json.dumps({"status": "ok", "version": "0.0.1", "pack_count": 2}))}))
    assert doctor.main(["--url", url, "--any-version", "--app-root", "/nonexistent"]) == 0


def test_an_unreachable_service_fails_without_a_traceback(capsys):
    assert doctor.main(["--url", "http://127.0.0.1:9", "--app-root", "/nonexistent", "--timeout", "2"]) == 1
    assert "/healthz did not answer" in capsys.readouterr().out


def test_pending_requests_leftovers_and_low_disk_are_reported(served, tmp_path, monkeypatch, capsys):
    url = served(_healthy())
    app = _app_root(tmp_path)
    (app / "activation/request.json").write_text("{}")
    (app / "incoming/20260101T000000Z-00000000").mkdir()
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda _path: shutil._ntuple_diskusage(100, 99, 10 * 1024 * 1024))
    assert doctor.main(["--url", url, "--app-root", str(app)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  an activation request is still pending" in out
    assert "warn  " in out and "20260101T000000Z-00000000" in out
    assert "FAIL  only 10 MiB free" in out


def test_doctor_rejects_non_http_urls():
    with pytest.raises(SystemExit):
        doctor.main(["--url", "file:///etc/passwd"])
