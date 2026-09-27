from __future__ import annotations

import json

from unittest.mock import patch

from bad_decisions_client import cli


class Response:
    def __init__(self, payload: str):
        self.payload = payload

    def read(self, _size: int = -1) -> bytes:
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_round_prints_only_result(capsys):
    with patch("bad_decisions_client.cli.urlopen", return_value=Response('{"result":"A completed round"}')) as request:
        assert cli.run(["--packs", "maha"]) == 0
    assert capsys.readouterr().out == "A completed round\n"
    assert request.call_args.args[0].full_url.endswith("/v2/round?packs=maha")


def test_list_packs(capsys):
    payload = '[{"id":"maha","name":"MAHA Pack","counts":{"prompts":27,"answers":52}}]'
    with patch("bad_decisions_client.cli.urlopen", return_value=Response(payload)):
        assert cli.run(["--list-packs"]) == 0
    assert capsys.readouterr().out == "maha\tMAHA Pack\tprompts=27\tanswers=52\n"


def test_json_and_validation(capsys):
    with patch("bad_decisions_client.cli.urlopen", return_value=Response('{"status":"ok"}')):
        assert cli.run(["--health", "--json"]) == 0
    assert '"status": "ok"' in capsys.readouterr().out
    assert cli.run(["--api-url", "ftp://bad"]) == 1
    assert "must start" in capsys.readouterr().err


def test_deal_saves_provenance_and_command_prints_every_pack(tmp_path, capsys, monkeypatch):
    saved = tmp_path / "last-round.json"
    monkeypatch.setattr(cli, "ROUND_PATH", saved)
    payload = {
        "result": "A completed round",
        "provenance": {
            "zulu-pack": {
                "version": "2",
                "attribution": "Zulu Creator",
                "license_id": "CC-BY-SA-4.0",
                "license_url": "https://example.invalid/zulu",
                "sources": [{
                    "origin": "zulu source",
                    "edition": "Second",
                    "sha256": "a" * 64,
                    "retrieved": "2026-09-23",
                    "license_evidence": "Owner declaration",
                }],
            },
            "alpha-pack": {
                "version": "1",
                "attribution": "Alpha Creator",
                "license_id": "MIT",
                "license_url": None,
                "sources": [{
                    "origin": "https://example.invalid/alpha",
                    "edition": None,
                    "sha256": None,
                    "retrieved": None,
                    "license_evidence": None,
                }],
            },
        },
    }
    with patch("bad_decisions_client.cli.urlopen", return_value=Response(json.dumps(payload))):
        assert cli.run(["deal"]) == 0
    capsys.readouterr()

    assert cli.run(["provenance"]) == 0
    output = capsys.readouterr().out
    assert output.index("alpha-pack") < output.index("zulu-pack")
    assert "License: MIT" in output
    assert "License: CC-BY-SA-4.0" in output
    assert "Attribution: Zulu Creator" in output
    assert "License evidence: Owner declaration" in output

    assert cli.run(["provenance", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == payload["provenance"]


def test_provenance_requires_a_valid_saved_draw(tmp_path, capsys, monkeypatch):
    saved = tmp_path / "last-round.json"
    monkeypatch.setattr(cli, "ROUND_PATH", saved)
    assert cli.run(["provenance"]) == 1
    assert "no saved round provenance available" in capsys.readouterr().err
    saved.write_text('{"token":"old-feedback-only"}', encoding="utf-8")
    assert cli.run(["provenance"]) == 1
    assert "no saved round provenance available" in capsys.readouterr().err


def test_provenance_only_draw_replaces_old_feedback_capability(tmp_path, capsys, monkeypatch):
    saved = tmp_path / "last-round.json"
    saved.write_text('{"url":"/old","token":"old"}', encoding="utf-8")
    monkeypatch.setattr(cli, "ROUND_PATH", saved)
    payload = {
        "result": "A completed round",
        "provenance": {"pack": {"version": "1", "attribution": "Creator", "license_id": "MIT", "license_url": None, "sources": []}},
    }
    with patch("bad_decisions_client.cli.urlopen", return_value=Response(json.dumps(payload))):
        assert cli.run(["deal"]) == 0
    capsys.readouterr()
    assert cli.run(["feedback", "enjoy"]) == 1
    assert "no saved round with feedback available" in capsys.readouterr().err


def test_version_prints_the_package_version_without_prompting(capsys):
    from bad_decisions_client import __version__

    with patch.object(cli, "_prompt_consequences", side_effect=AssertionError("must not prompt")):
        try:
            cli.run(["--version"])
        except SystemExit as exited:
            assert exited.code == 0
    assert capsys.readouterr().out.strip() == f"regret {__version__}"


def test_feedback_url_resolves_the_servers_prefixed_path_once():
    # The server returns its path with the public prefix; 1.x doubled it (/bad-decisions/bad-decisions/...).
    url = cli._feedback_url("https://bytes.example/bad-decisions", "/bad-decisions/v2/rounds/r1/feedback")
    assert url == "https://bytes.example/bad-decisions/v2/rounds/r1/feedback"
    assert cli._feedback_url("http://127.0.0.1:8000", "/v2/rounds/r1/feedback") == "http://127.0.0.1:8000/v2/rounds/r1/feedback"


def test_feedback_url_never_sends_the_token_to_another_server():
    import pytest

    for hostile in ("https://evil.example/v2/rounds/r1/feedback", "//evil.example/steal"):
        with pytest.raises(ValueError):
            cli._feedback_url("https://bytes.example/bad-decisions", hostile)


def test_the_client_has_no_dependencies_and_compares_versions_itself():
    from pathlib import Path  # no tomllib: client tests also run on Python 3.10

    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert "\ndependencies = []\n" in pyproject and '\nrequires-python = ">=3.10"\n' in pyproject
    assert cli._is_newer("2.0.0", "1.8.5") and cli._is_newer("1.10.0", "1.9.9")
    assert not cli._is_newer("1.8.5", "1.8.5") and not cli._is_newer("1.8.4", "1.8.5")
    assert not cli._is_newer("2.1.0rc1", "2.0.0") and not cli._is_newer("junk", "2.0.0")


def _serve_raw(monkeypatch, body: bytes = b"", *, error: Exception | None = None):
    import io

    class Response(io.BytesIO):
        headers: dict = {}

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def urlopen(_request, timeout=None):
        if error is not None:
            raise error
        return Response(body)

    monkeypatch.setattr(cli, "urlopen", urlopen)


def test_request_json_reports_bad_responses_clearly(monkeypatch):
    import http.client
    import io
    import pytest
    from urllib.error import HTTPError

    cases = [
        (dict(body=b"<html>not json</html>"), "other than JSON"),
        (dict(body=b"[" * (cli.MAX_RESPONSE_BYTES + 1)), "too large"),
        (dict(error=http.client.RemoteDisconnected("closed")), "Cannot reach API"),
        (dict(error=ConnectionResetError("reset")), "Cannot reach API"),
        (dict(error=HTTPError("https://x", 502, "Bad Gateway", {}, io.BytesIO(b'["not", "a", "dict"]'))), "HTTP 502: Bad Gateway"),
        (dict(error=HTTPError("https://x", 500, "Oops", {}, io.BytesIO(b'{"error": "flat string"}'))), "HTTP 500: Oops"),
    ]
    for kwargs, message in cases:
        _serve_raw(monkeypatch, **kwargs)
        with pytest.raises(RuntimeError, match=message):
            cli._request_json("https://example.invalid/v2/round", timeout=1)
