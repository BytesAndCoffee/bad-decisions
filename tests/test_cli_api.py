from __future__ import annotations

import subprocess
import sys

from fastapi.testclient import TestClient

from bad_decisions.api import create_app
from bad_decisions import __version__


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "bad_decisions.cli", *args], text=True, capture_output=True)


def test_oneshot_is_clean_and_filters_work():
    result = run_cli("--black-packs", "maha", "--white-packs", "base,maha", "--oneshot")
    assert result.returncode == 0
    assert result.stdout.endswith("\n") and result.stdout.strip()
    assert result.stderr == ""


def test_cli_errors_and_non_tty():
    result = run_cli("--rapid", "--oneshot")
    assert result.returncode == 2
    assert run_cli("--delay", "1").returncode == 2
    assert run_cli("--rapid", "--delay", "nan").returncode == 2
    assert run_cli("--packs", "typo", "--oneshot").returncode == 2
    plain = run_cli()
    assert plain.returncode == 2 and "--oneshot" in plain.stderr


def test_api_success_metadata_and_cache():
    with TestClient(create_app()) as client:
        howto = client.get("/")
        assert howto.status_code == 200
        assert howto.headers["content-type"].startswith("text/plain")
        assert "/v1/round" in howto.text
        assert "CARDDECK.md" in howto.text
        assert client.get("/healthz").json() == {"status": "ok", "version": __version__, "pack_count": 3}
        web = client.get("/web/")
        assert web.status_code == 200 and "Bad Decisions" in web.text
        assert '<base href="/web/">' in web.text
        assert client.get("/web", follow_redirects=False).status_code == 200
        assert client.get("/web/app.js").status_code == 200
        favicon = client.get("/web/favicon.svg")
        assert favicon.headers["content-type"].startswith("image/svg+xml")
        packs = client.get("/v1/packs").json()
        assert [p["id"] for p in packs] == ["base", "coffee", "maha"]
        assert client.get("/v1/packs/coffee").json()["counts"] == {"black": 50, "white": 100}
        assert client.get("/v1/packs/maha").json()["counts"] == {"black": 27, "white": 52}
        response = client.get("/v1/round", params={"black_packs": "maha", "white_packs": "base,maha"})
        assert response.status_code == 200
        body = response.json()
        assert body["black"]["pack"] == "maha"
        assert body["selection"] == {"black_packs": ["maha"], "white_packs": ["base", "maha"]}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-request-id"]


def test_web_client_works_with_proxy_root_path(monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_ROOT_PATH", "/bad-decisions")
    with TestClient(create_app()) as client:
        assert client.get("/web/").status_code == 200
        assert '<base href="/bad-decisions/web/">' in client.get("/web").text
        assert client.get("/web/style.css").status_code == 200
        assert client.get("/web/../api.py").status_code == 404


def test_api_normalized_errors():
    with TestClient(create_app()) as client:
        cases = [
            ("/v1/round?black_packs=typo", 400, "unknown_pack"),
            ("/v1/round?packs=base&packs=maha", 400, "repeated_query_parameter"),
            ("/v1/round?wat=x", 400, "unknown_query_parameter"),
            ("/v1/packs/nope", 404, "pack_not_found"),
            ("/no-such-path", 404, "path_not_found"),
        ]
        for path, status, code in cases:
            response = client.get(path)
            assert response.status_code == status
            assert response.json()["error"]["code"] == code


def custom_registry_without_base(tmp_path):
    from importlib.resources import files

    directory = tmp_path / "registry"
    directory.mkdir()
    source = files("bad_decisions").joinpath("data/packs/maha.json")
    (directory / "maha.json").write_bytes(source.read_bytes())
    return directory


def test_default_packs_work_without_base_in_custom_registry(tmp_path, monkeypatch):
    directory = custom_registry_without_base(tmp_path)
    monkeypatch.setenv("BAD_DECISIONS_PACK_DIR", str(directory))
    with TestClient(create_app()) as client:
        response = client.get("/v1/round")
        assert response.status_code == 200
        assert response.json()["selection"] == {"black_packs": ["maha"], "white_packs": ["maha"]}
        missing = client.get("/v1/round", params={"packs": "base"})
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == "unknown_pack"
    result = run_cli_env({"BAD_DECISIONS_PACK_DIR": str(directory)}, "--oneshot")
    assert result.returncode == 0
    assert result.stdout.strip() and result.stderr == ""


def run_cli_env(env, *args):
    import os

    return subprocess.run(
        [sys.executable, "-m", "bad_decisions.cli", *args],
        text=True,
        capture_output=True,
        env={**os.environ, **env},
    )


def test_api_default_selects_every_bundled_pack():
    with TestClient(create_app()) as client:
        selection = client.get("/v1/round").json()["selection"]
        assert selection == {
            "black_packs": ["base", "coffee", "maha"],
            "white_packs": ["base", "coffee", "maha"],
        }
