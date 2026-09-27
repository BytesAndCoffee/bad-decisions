from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from bad_decisions.api import create_app
from bad_decisions import __version__


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "bad_decisions.cli", *args], text=True, capture_output=True)


def test_oneshot_is_clean_and_filters_work():
    result = run_cli("--prompt-packs", "maha", "--answer-packs", "base,maha", "--oneshot")
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
        assert "/v2/round" in howto.text
        assert "CARDDECK.md" in howto.text
        assert client.get("/healthz").json() == {"status": "ok", "version": __version__, "pack_count": 3}
        web = client.get("/web/")
        assert web.status_code == 200 and "Bad Decisions" in web.text
        assert '<base href="/web/">' in web.text
        assert client.get("/web", follow_redirects=False).status_code == 200
        app_js = client.get("/web/app.js")
        assert app_js.status_code == 200
        style = client.get("/web/style.css")
        assert style.status_code == 200
        assert 'id="indexed-packs-modal"' in web.text
        assert 'id="indexed-pack-options"' in web.text
        assert '<select id="indexed-packs"' not in web.text
        assert 'let indexedSelection = new Set();' in app_js.text
        assert 'indexedSelection = new Set(indexedDraft)' in app_js.text
        assert 'return [...selected, ...indexedSelection];' in app_js.text
        assert "backdrop-filter" not in style.text
        assert "overscroll-behavior: contain" in style.text
        assert "contain: layout paint" in style.text
        favicon = client.get("/web/favicon.svg")
        assert favicon.headers["content-type"].startswith("image/svg+xml")
        packs = client.get("/v2/packs").json()
        assert [p["id"] for p in packs] == ["base", "coffee", "maha"]
        assert client.get("/v2/packs/coffee").json()["counts"] == {"prompts": 50, "answers": 100}
        assert client.get("/v2/packs/maha").json()["counts"] == {"prompts": 27, "answers": 52}
        response = client.get("/v2/round", params={"prompt_packs": "maha", "answer_packs": "base,maha"})
        assert response.status_code == 200
        body = response.json()
        assert body["prompt"]["pack"] == "maha"
        assert body["selection"] == {"prompt_packs": ["maha"], "answer_packs": ["base", "maha"]}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-request-id"]


def test_web_client_works_with_proxy_root_path(monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_ROOT_PATH", "/bad-decisions")
    with TestClient(create_app()) as client:
        assert client.get("/web/").status_code == 200
        assert '<base href="/bad-decisions/web/">' in client.get("/web").text
        assert client.get("/web/style.css").status_code == 200
        assert client.get("/web/../api.py").status_code == 404


@pytest.mark.parametrize("root_path,prefix", [("/bad-decisions", "/bad-decisions"), ("", "")])
def test_trailing_slash_redirects_keep_the_proxy_prefix(monkeypatch, root_path, prefix):
    # nginx strips /bad-decisions before proxying; Starlette's own redirect dropped it
    # and sent /bad-decisions/docs/ to /docs.
    monkeypatch.setenv("BAD_DECISIONS_ROOT_PATH", root_path)
    with TestClient(create_app()) as client:
        for path in ("/docs/", "/healthz/", "/v2/packs/", "/v2/packs/base/"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 307
            assert response.headers["location"] == prefix + path.rstrip("/")
        response = client.get("/v2/round/?packs=base", follow_redirects=False)
        assert response.headers["location"] == f"{prefix}/v2/round?packs=base"
        for path in ("/no-such-path/", "//evil.example/"):
            response = client.get(f"http://testserver{path}", follow_redirects=False)
            assert response.status_code == 404, path
            assert response.json()["error"]["code"] == "path_not_found"
        assert client.get("/docs").status_code == 200


def test_api_normalized_errors():
    with TestClient(create_app()) as client:
        cases = [
            ("/v2/round?prompt_packs=typo", 422, "unknown_pack"),
            ("/v2/round?packs=base&packs=maha", 400, "repeated_query_parameter"),
            ("/v2/round?wat=x", 400, "unknown_query_parameter"),
            ("/v2/packs/nope", 404, "pack_not_found"),
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
        response = client.get("/v2/round")
        assert response.status_code == 200
        assert response.json()["selection"] == {"prompt_packs": ["maha"], "answer_packs": ["maha"]}
        missing = client.get("/v2/round", params={"packs": "base"})
        assert missing.status_code == 422
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
        selection = client.get("/v2/round").json()["selection"]
        assert selection == {
            "prompt_packs": ["base", "coffee", "maha"],
            "answer_packs": ["base", "coffee", "maha"],
        }


def test_management_status_requires_configured_bearer(monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_MANAGEMENT_TOKEN", "correct-token")
    with TestClient(create_app()) as client:
        denied = client.get("/v2/manage/status")
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "unauthorized"
        accepted = client.get("/v2/manage/status", headers={"Authorization": "Bearer correct-token"})
        assert accepted.status_code == 200
        assert accepted.json() == {"status": "ok", "version": __version__, "pack_count": 3}


def _node_eval(script: str) -> str:
    import shutil
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    return subprocess.run([node, "-e", script], check=True, capture_output=True, text=True).stdout


def test_indexed_packs_are_chosen_by_provenance_and_show_their_id():
    import json
    import re
    from pathlib import Path

    app_js = (Path(__file__).resolve().parents[1] / "src" / "bad_decisions" / "web" / "app.js").read_text()
    constant = re.search(r'^const PYX_EDITION = .*;$', app_js, re.M).group(0)
    function = re.search(r"^function isIndexedPack\(pack\) \{.*?^\}", app_js, re.M | re.S).group(0)
    pyx_source = {"origin": "https://github.com/ajanata/PretendYoureXyzzy/blob/x/cah_cards.sql", "edition": "Pretend You're Xyzzy SQL card set 100228: [CUSTOM] Something"}
    packs = [
        {"id": "furry", "sources": [pyx_source]},                           # renamed PYX pack: still indexed
        {"id": "pyx-103-base-game-canada", "sources": [dict(pyx_source, edition="Pretend You're Xyzzy SQL card set 103: Base Game (Canada)")]},
        {"id": "pyx-legacy", "sources": []},                               # id prefix is still honored
        {"id": "base", "sources": [{"origin": "https://s3.amazonaws.com/cah/CAH_MainGame.pdf", "edition": "Official downloadable main game PDF"}]},
        {"id": "coffee", "sources": [{"origin": "bytesandcoffee-pack-v2.json", "edition": None}]},
        {"id": "maha"},                                                   # no sources at all
    ]
    output = _node_eval(f"{constant}\n{function}\nconsole.log(JSON.stringify({json.dumps(packs)}.map(isIndexedPack)))")
    assert json.loads(output) == [True, True, True, False, False, False]
    assert "packs.filter(isIndexedPack)" in app_js and 'pack.id.startsWith("pyx-"));' not in app_js
    assert "counts.textContent = `${pack.id} · " in app_js
