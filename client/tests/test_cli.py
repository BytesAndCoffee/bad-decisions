from __future__ import annotations

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
