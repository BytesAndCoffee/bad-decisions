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


def _nack(monkeypatch, status, body):
    import io
    import json
    from urllib.error import HTTPError

    def urlopen(request, timeout=None):
        raise HTTPError(request.full_url, status, "refused", {}, io.BytesIO(json.dumps(body).encode()))

    monkeypatch.setattr(cli, "urlopen", urlopen)


def test_peer_pressure_nacks_keep_their_reason_and_message(monkeypatch):
    _nack(monkeypatch, 409, {"type": "NACK", "reason": "display_name_taken", "message": "That display name is already in this room", "resync": False})
    try:
        cli._request_json("https://example.invalid/v1/peer-pressure/rooms/test/join", timeout=1, method="POST", payload={})
    except RuntimeError as exc:
        assert str(exc) == "display_name_taken: That display name is already in this room"
    else:
        raise AssertionError("expected a RuntimeError")


def test_api_error_envelopes_are_still_reported(monkeypatch):
    _nack(monkeypatch, 404, {"error": {"code": "pack_not_found", "message": "Unknown pack", "details": {}}})
    try:
        cli._request_json("https://example.invalid/v1/packs/x", timeout=1)
    except RuntimeError as exc:
        assert str(exc) == "pack_not_found: Unknown pack"


def test_together_explains_a_taken_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_session", lambda *_args: None)
    _nack(monkeypatch, 409, {"type": "NACK", "reason": "display_name_taken", "message": "That display name is already in this room", "resync": False})
    assert cli.run(["together", "test", "--name", "michael"]) == 1
    err = capsys.readouterr().err
    assert "display_name_taken: That display name is already in this room" in err
    assert "--name" in err and "HTTP 409" not in err


def test_stale_revision_nack_triggers_a_resync(monkeypatch):
    synced = []

    def request_json(url, **kwargs):
        if url.endswith("/submit"):
            raise RuntimeError("stale_revision: Room state changed; synchronize and try again")
        synced.append(url)
        return {"type": "SYNACK", "revision": 9, "state": {"room": {"revision": 9}}}, {}

    client = TogetherClient("https://example.invalid", "ohno", 4, request_json)
    client.player_id, client.session_token, client.revision = "player_1", "token", 4
    try:
        client.mutate("submit", card_instance_ids=["card_1"])
    except RuntimeError:
        pass
    assert synced and client.revision == 9
