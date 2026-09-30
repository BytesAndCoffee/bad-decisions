"""API v2 contract: one error envelope, v1 removed, caching, CORS, limits, token-only rooms."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from bad_decisions import __version__
from bad_decisions.api import RateLimiter, create_app


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_PEER_PRESSURE_DIR", str(tmp_path / "rooms"))

    def start(**env):
        for key, value in env.items():
            monkeypatch.setenv(f"BAD_DECISIONS_{key}", str(value))
        return TestClient(create_app())

    return start


def request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1"),
        ("HEAD", "/v1/round"),
        ("OPTIONS", "/v1/packs"),
        ("GET", "/v1/round"),
        ("POST", "/v1/peer-pressure/rooms"),
        ("DELETE", "/v1/rounds/x/feedback"),
    ],
)
def test_v1_is_gone_with_an_upgrade_hint(api, method, path):
    with api() as client:
        response = client.request(method, path)
    assert response.status_code == 410
    if method == "HEAD":
        assert response.content == b""
        return
    body = response.json()["error"]
    assert body["code"] == "api_version_removed" and "/v2" in body["message"] and "regret" in body["message"]


def test_healthz_stays_unversioned_for_old_clients(api):
    with api() as client:
        assert client.get("/healthz").json() == {"status": "ok", "version": __version__, "pack_count": 3}


def test_every_error_uses_one_envelope_with_the_request_id(api):
    with api() as client:
        for path in ("/v2/round?prompt_packs=nope", "/v2/packs/nope", "/nowhere", "/v2/peer-pressure/rooms/ohno/state"):
            response = client.get(path, headers={"X-Request-ID": "trace-123"})
            error = response.json()["error"]
            assert set(error) == {"code", "message", "details", "request_id"}, path
            assert error["request_id"] == "trace-123" == response.headers["X-Request-ID"]
        method = client.delete("/v2/packs")
        assert method.status_code == 405 and method.json()["error"]["code"] == "method_not_allowed"


def test_validation_errors_do_not_echo_the_request(api):
    secret = "x" * 5000
    with api() as client:
        response = client.post("/v2/peer-pressure/rooms", json={"room": 7, "extra": secret})
    assert response.status_code == 422
    assert secret not in response.text and '"input"' not in response.text
    assert all(set(item) == {"loc", "msg", "type"} for item in response.json()["error"]["details"]["errors"])


def test_pack_responses_revalidate_with_etags(api):
    with api() as client:
        listing = client.get("/v2/packs")
        assert listing.headers["Cache-Control"] == "public, max-age=300"
        assert client.get("/v2/packs", headers={"If-None-Match": listing.headers["ETag"]}).status_code == 304
        one = client.get("/v2/packs/maha")
        assert one.json()["counts"] == {"prompts": 27, "answers": 52}
        assert client.get("/v2/packs/maha", headers={"If-None-Match": one.headers["ETag"]}).status_code == 304
        assert one.headers["ETag"] != listing.headers["ETag"]
        assert client.get("/v2/round").headers["Cache-Control"] == "no-store"


def test_versioned_web_assets_are_immutable_and_the_page_is_not(api):
    with api() as client:
        page = client.get("/web/")
        assert page.headers["Cache-Control"] == "no-cache"
        digest = re.search(r'app\.js\?v=([0-9a-f]{16})', page.text).group(1)
        assert "immutable" in client.get(f"/web/app.js?v={digest}").headers["Cache-Control"]
        assert client.get(f"/web/app.js?v={__version__}").headers["Cache-Control"] == "no-cache"
        assert client.get("/web/app.js?v=0.0.1").headers["Cache-Control"] == "no-cache"


def test_cors_is_off_unless_origins_are_configured(api):
    with api() as client:
        assert "access-control-allow-origin" not in client.get("/v2/packs", headers={"Origin": "https://game.example"}).headers
    with api(CORS_ORIGINS="https://game.example") as client:
        allowed = client.get("/v2/packs", headers={"Origin": "https://game.example"})
        assert allowed.headers["access-control-allow-origin"] == "https://game.example"
        assert "access-control-allow-origin" not in client.get("/v2/packs", headers={"Origin": "https://evil.example"}).headers


@pytest.mark.parametrize(
    "value",
    [
        "*", "game.example", "https://game.example/path", "ftp://game.example",
        "https://game.example?origin=evil", "https://game.example#fragment",
        "https://user:password@game.example", "https://game.example:not-a-port",
    ],
)
def test_cors_origins_must_be_exact(api, value):
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        api(CORS_ORIGINS=value)


def test_room_creation_is_rate_limited_per_client(api):
    with api(RATE_LIMIT_PER_MINUTE=2) as client:
        codes = [client.post("/v2/peer-pressure/rooms", json={"room": f"room-{index}"}).status_code for index in range(3)]
        limited = client.post("/v2/peer-pressure/rooms", json={"room": "room-9"})
    assert codes == [201, 201, 429]
    assert limited.json()["error"]["code"] == "rate_limited" and int(limited.headers["Retry-After"]) >= 1


def test_rate_limiter_window_slides():
    clock = [0.0]
    limiter = RateLimiter(2, now=lambda: clock[0])
    assert limiter.retry_after("a", "join") is None and limiter.retry_after("a", "join") is None
    assert limiter.retry_after("a", "join") == 61
    assert limiter.retry_after("b", "join") is None, "clients are limited separately"
    clock[0] = 60.0
    assert limiter.retry_after("a", "join") is None


def test_room_and_player_caps(api):
    with api(PEER_PRESSURE_MAX_ROOMS=1, PEER_PRESSURE_MAX_PLAYERS=2) as client:
        assert client.post("/v2/peer-pressure/rooms", json={"room": "only"}).status_code == 201
        full = client.post("/v2/peer-pressure/rooms", json={"room": "another"})
        assert (full.status_code, full.json()["error"]["code"]) == (503, "too_many_rooms")
        for name in ("Alice", "Bob"):
            assert client.post("/v2/peer-pressure/rooms/only/join", json={"display_name": name}).status_code == 200
        third = client.post("/v2/peer-pressure/rooms/only/join", json={"display_name": "Carol"})
        assert (third.status_code, third.json()["error"]["code"]) == (409, "room_full")


def test_rooms_use_the_bearer_token_alone(api):
    with api() as client:
        joined = [client.post("/v2/peer-pressure/rooms/table/join", json={"display_name": name}).json() for name in ("Alice", "Bob", "Carol")]
        alice = {"Authorization": f"Bearer {joined[0]['session_token']}"}
        rejected = client.post("/v2/peer-pressure/rooms/table/start", headers=alice, json={"player_id": joined[0]["player_id"], "request_id": request_id(), "revision": 3})
        assert rejected.status_code == 422, "player_id is no longer accepted"
        rejoined = client.post("/v2/peer-pressure/rooms/table/join", headers=alice, json={"display_name": "ignored"}).json()
        assert rejoined["player_id"] == joined[0]["player_id"]
        started = client.post("/v2/peer-pressure/rooms/table/start", headers=alice, json={"request_id": request_id(), "revision": rejoined["revision"]})
        assert started.status_code == 200
        state = client.get("/v2/peer-pressure/rooms/table/state", headers=alice).json()
        assert state["prompt"]["slots"] >= 1 and state["you"]["id"] == joined[0]["player_id"]
        stale = client.post("/v2/peer-pressure/rooms/table/heartbeat", headers=alice, json={"revision": 0}).json()
        assert stale == {"revision": state["room"]["revision"], "resync": True}
        ended = client.post("/v2/peer-pressure/rooms/table/end", headers=alice, json={"request_id": request_id(), "revision": state["room"]["revision"]})
        assert ended.status_code == 200


def test_openapi_documents_models_and_the_error_envelope(api):
    with api() as client:
        schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    assert {"ErrorEnvelope", "RoundResponse", "PackSummary", "Health", "FeedbackResult"} <= set(components)
    assert set(components["RoundResponse"]["properties"]) >= {"prompt", "answers", "result", "selection", "provenance"}
    round_responses = schema["paths"]["/v2/round"]["get"]["responses"]
    assert round_responses["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorEnvelope")
    assert not any(path.startswith("/v1") for path in schema["paths"])
    assert "action" not in str(schema["paths"]["/v2/peer-pressure/rooms/{room}/start"])


def test_pools_resolve_once_per_selector(api, monkeypatch):
    import bad_decisions.api as api_module

    calls = []
    real = api_module.resolve_pools

    def counting(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(api_module, "resolve_pools", counting)
    with api() as client:
        for _ in range(3):
            assert client.get("/v2/round?packs=maha").status_code == 200
        for _ in range(2):
            assert client.get("/v2/round?packs=nope").status_code == 422
    assert [call["packs"] for call in calls] == ["maha", "nope", "nope"], "errors are not cached"
