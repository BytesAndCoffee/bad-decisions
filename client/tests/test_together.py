from __future__ import annotations

import stat

from bad_decisions_client import cli
from bad_decisions_client.together import TogetherClient, choose_responses, load_session, render, save_session


def test_room_session_is_private_and_scoped_by_service(tmp_path):
    path = tmp_path / "sessions.json"
    session = {"player_id": "player_1", "session_token": "secret", "display_name": "Alice"}
    save_session("https://one.invalid", "ohno", session, cli._atomic_json, path)
    assert load_session("https://one.invalid", "ohno", path) == session
    assert load_session("https://two.invalid", "ohno", path) is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_protocol_client_authenticates_and_tracks_revisions():
    calls = []

    def request_json(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/join"):
            return {"type": "SYNACK", "player_id": "player_1", "session_token": "token", "revision": 2, "state": {"room": {"revision": 2}}}, {}
        if url.endswith("/sync"):
            return {"type": "SYNACK", "revision": 3, "state": {"room": {"revision": 3}}}, {}
        return {"type": "ACK", "request_id": kwargs["payload"].get("request_id"), "revision": 3}, {}

    client = TogetherClient("https://example.invalid/root", "ohno", 4, request_json)
    client.join("Alice")
    assert client.player_id == "player_1" and client.revision == 2
    client.sync()
    assert client.revision == 3
    assert calls[-1][1]["headers"] == {"Authorization": "Bearer token"}
    assert calls[-1][1]["payload"] == {"player_id": "player_1"}


def test_lost_ack_retries_the_same_idempotency_key():
    attempts = []

    def request_json(url, **kwargs):
        if url.endswith("/start"):
            attempts.append(kwargs["payload"].copy())
            if len(attempts) == 1:
                raise RuntimeError("Cannot reach API: connection reset")
            return {"type": "ACK", "request_id": kwargs["payload"]["request_id"], "revision": 5}, {}
        return {"type": "SYNACK", "revision": 5, "state": {"room": {"revision": 5}}}, {}

    client = TogetherClient("https://example.invalid", "ohno", 4, request_json)
    client.player_id = "player_1"
    client.session_token = "token"
    client.revision = 4
    client.mutate("start")
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert attempts[0]["request_id"].startswith("req_")


def test_trade_dress_safe_render_and_response_selection(capsys):
    state = {
        "room": {"code": "ohno", "round": 1, "state": "PLAYING"},
        "players": [{"name": "Alice", "score": 0, "connected": True}],
        "responsible_adult": {"id": "player_1", "name": "Alice"},
        "question": {"text": "A question _", "slots": 1},
        "you": {"hand": [{"card_instance_id": "card_1", "text": "A response"}]},
        "result": None,
    }
    render(state)
    assert choose_responses(state, lambda _prompt: "1") == ["card_1"]
    output = capsys.readouterr().out
    assert "Responsible Adult: Alice" in output
    assert "Question: A question _" in output
    assert "card czar" not in output.lower()


def test_together_requires_name_without_saved_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("bad_decisions_client.together.SESSION_PATH", tmp_path / "none.json")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.run(["together", "ohno", "--api-url", "https://example.invalid"]) == 1
    assert "--name is required" in capsys.readouterr().err
