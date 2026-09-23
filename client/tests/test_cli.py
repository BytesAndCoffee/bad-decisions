from __future__ import annotations

import json

from unittest.mock import patch

from bad_decisions_client import cli


class Response:
    def __init__(self, payload: str):
        self.payload = payload

    def read(self) -> bytes:
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_round_prints_only_result(capsys):
    with patch("bad_decisions_client.cli.urlopen", return_value=Response('{"result":"A completed round"}')) as request:
        assert cli.run(["--packs", "maha"]) == 0
    assert capsys.readouterr().out == "A completed round\n"
    assert request.call_args.args[0].full_url.endswith("/v1/round?packs=maha")


def test_list_packs(capsys):
    payload = '[{"id":"maha","name":"MAHA Pack","counts":{"black":27,"white":52}}]'
    with patch("bad_decisions_client.cli.urlopen", return_value=Response(payload)):
        assert cli.run(["--list-packs"]) == 0
    assert capsys.readouterr().out == "maha\tMAHA Pack\tblack=27\twhite=52\n"


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
            "white-pack": {
                "version": "2",
                "attribution": "White Creator",
                "license_id": "CC-BY-SA-4.0",
                "license_url": "https://example.invalid/white",
                "sources": [{
                    "origin": "white source",
                    "edition": "Second",
                    "sha256": "a" * 64,
                    "retrieved": "2026-09-23",
                    "license_evidence": "Owner declaration",
                }],
            },
            "black-pack": {
                "version": "1",
                "attribution": "Black Creator",
                "license_id": "MIT",
                "license_url": None,
                "sources": [{
                    "origin": "https://example.invalid/black",
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
    assert output.index("black-pack") < output.index("white-pack")
    assert "License: MIT" in output
    assert "License: CC-BY-SA-4.0" in output
    assert "Attribution: White Creator" in output
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
