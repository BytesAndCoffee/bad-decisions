from __future__ import annotations

import stat

import pytest

from bad_decisions_client import cli
from bad_decisions_client.together import TogetherClient, choose_answers, load_session, render, save_session


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
            return {"player_id": "player_1", "session_token": "token", "revision": 2, "state": {"room": {"revision": 2}}}, {}
        if url.endswith("/sync"):
            return {"revision": 3, "state": {"room": {"revision": 3}}}, {}
        return {"request_id": kwargs["payload"].get("request_id"), "revision": 3}, {}

    client = TogetherClient("https://example.invalid/root", "ohno", 4, request_json)
    client.join("Alice")
    assert client.player_id == "player_1" and client.revision == 2
    client.sync()
    assert client.revision == 3
    assert calls[-1][1]["headers"] == {"Authorization": "Bearer token"}
    assert "payload" not in calls[-1][1], "the bearer token alone identifies the player"


def test_rejoin_sends_only_the_saved_token():
    calls = []

    def request_json(url, **kwargs):
        calls.append(kwargs)
        return {"player_id": "player_1", "session_token": "token", "revision": 2, "state": {"room": {"revision": 2}}}, {}

    TogetherClient("https://example.invalid", "ohno", 4, request_json).join("Alice", {"player_id": "player_1", "session_token": "token", "display_name": "Alice"})
    assert calls[0]["payload"] == {"display_name": "Alice", "create": True}
    assert calls[0]["headers"] == {"Authorization": "Bearer token"}


def test_heartbeat_resyncs_when_the_server_says_so():
    calls = []

    def request_json(url, **kwargs):
        calls.append(url)
        if url.endswith("/heartbeat"):
            assert kwargs["payload"] == {"revision": 4}
            return {"revision": 5, "resync": True}, {}
        return {"revision": 5, "state": {"room": {"revision": 5}}}, {}

    client = TogetherClient("https://example.invalid", "ohno", 4, request_json)
    client.session_token, client.revision = "token", 4
    assert client.heartbeat() is True
    assert calls[-1].endswith("/sync") and client.revision == 5


def test_lost_ack_retries_the_same_idempotency_key():
    attempts = []

    def request_json(url, **kwargs):
        if url.endswith("/start"):
            attempts.append(kwargs["payload"].copy())
            if len(attempts) == 1:
                raise RuntimeError("Cannot reach API: connection reset")
            return {"request_id": kwargs["payload"]["request_id"], "revision": 5}, {}
        return {"revision": 5, "state": {"room": {"revision": 5}}}, {}

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
        "prompt": {"text": "A prompt _", "slots": 1},
        "you": {"hand": [{"card_instance_id": "card_1", "text": "A response"}]},
        "result": None,
    }
    render(state)
    assert choose_answers(state, lambda _prompt: "1") == ["card_1"]
    output = capsys.readouterr().out
    assert "Responsible Adult: Alice" in output
    assert "Prompt: A prompt _" in output
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


def test_peer_pressure_errors_keep_their_code_and_message(monkeypatch):
    _nack(monkeypatch, 409, {"error": {"code": "display_name_taken", "message": "That display name is already in this room", "details": {"resync": False}, "request_id": "r"}})
    try:
        cli._request_json("https://example.invalid/v2/peer-pressure/rooms/test/join", timeout=1, method="POST", payload={})
    except RuntimeError as exc:
        assert str(exc) == "display_name_taken: That display name is already in this room"
    else:
        raise AssertionError("expected a RuntimeError")


def test_api_error_envelopes_are_still_reported(monkeypatch):
    _nack(monkeypatch, 404, {"error": {"code": "pack_not_found", "message": "Unknown pack", "details": {}}})
    try:
        cli._request_json("https://example.invalid/v2/packs/x", timeout=1)
    except RuntimeError as exc:
        assert str(exc) == "pack_not_found: Unknown pack"


def test_together_explains_a_taken_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_session", lambda *_args: None)
    _nack(monkeypatch, 409, {"error": {"code": "display_name_taken", "message": "That display name is already in this room", "details": {"resync": False}, "request_id": "r"}})
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
        return {"revision": 9, "state": {"room": {"revision": 9}}}, {}

    client = TogetherClient("https://example.invalid", "ohno", 4, request_json)
    client.player_id, client.session_token, client.revision = "player_1", "token", 4
    try:
        client.mutate("submit", card_instance_ids=["card_1"])
    except RuntimeError:
        pass
    assert synced and client.revision == 9


@pytest.mark.parametrize("reason", ["invalid_session", "room_expired", "room_not_found"])
def test_a_stale_saved_session_falls_back_to_a_fresh_join(tmp_path, monkeypatch, capsys, reason):
    saved = {"player_id": "player_old", "session_token": "stale", "display_name": "Alice"}
    monkeypatch.setattr(cli, "load_session", lambda *_args: saved)
    stored = []
    monkeypatch.setattr(cli, "save_session", lambda _base, _room, session, _write: stored.append(session))
    monkeypatch.setattr(cli, "run_together", lambda *_args, **_kwargs: 0)
    calls = []

    def request_json(url, **kwargs):
        calls.append(kwargs.get("headers"))
        if kwargs.get("headers"):
            raise RuntimeError(f"{reason}: gone")
        return {"player_id": "player_new", "session_token": "fresh", "revision": 1, "state": {"room": {"revision": 1}}}, {}

    monkeypatch.setattr(cli, "_request_json", request_json)
    assert cli.run(["together", "ohno"]) == 0
    assert calls == [{"Authorization": "Bearer stale"}, None]
    assert stored == [{"player_id": "player_new", "session_token": "fresh", "display_name": "Alice"}]
    assert "joining as a new player" in capsys.readouterr().err


def test_other_join_errors_are_not_retried(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_session", lambda *_args: {"player_id": "p", "session_token": "t", "display_name": "Alice"})
    calls = []

    def request_json(url, **kwargs):
        calls.append(url)
        raise RuntimeError("game_in_progress: started")

    monkeypatch.setattr(cli, "_request_json", request_json)
    assert cli.run(["together", "ohno"]) == 1
    assert len(calls) == 1
