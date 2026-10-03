"""Management sign-in by tailnet liveness (docs/MANAGEMENT_AUTH.md): protocol, state, and hostile inputs."""

from __future__ import annotations

import builtins
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bad_decisions import management as m
from bad_decisions.api import create_app
from bad_decisions.errors import MissingExtraError
from bad_decisions.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = "https://games.example"
ATTEST_HOST = "box.tail1234.ts.net:8443"
ATTEST = f"https://{ATTEST_HOST}"
PEER = "100.101.102.103"
NODE_ID = "nAllowed123CNTRL"


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeTailnet:
    """status and whois answers shaped like tailscaled's LocalAPI."""

    def __init__(self, *, ip=PEER, node_id=NODE_ID, tags=("tag:mgmt",), sharee=False, sharer=0, whois_id=None, fail=False):
        self.peer = {"ID": node_id, "TailscaleIPs": [ip, "fd7a:115c:a1e0::1"], "ShareeNode": sharee, "Tags": list(tags)}
        self.node = {"StableID": whois_id or node_id, "Name": "laptop.tail1234.ts.net.", "Tags": list(tags), "Sharer": sharer}
        self.fail = fail
        self.calls = []

    def status(self):
        self.calls.append("status")
        if self.fail:
            raise OSError("tailscaled is down")
        return {"Self": {"ID": "nServer"}, "Peer": {"nodekey:abc": self.peer}}

    def whois(self, address):
        self.calls.append(("whois", address))
        return {"Node": self.node, "UserProfile": {"LoginName": "tagged-devices"}}


POLICY = m.Policy(frozenset({"tag:mgmt"}))


# --- protocol -------------------------------------------------------------------------

def test_hkdf_matches_rfc_5869_test_case_1():
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    expected = "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
    assert m.hkdf_sha256(ikm, salt, info, 42).hex() == expected


def test_h2_round_trips_and_every_tampering_fails():
    cid, c, n, s = b"\x01" * 16, b"\x02" * 32, b"\x03" * 32, b"\x04" * 32
    ip = m.ip_bytes(PEER)
    h1 = m.compute_h1(c, cid, n)
    h2 = m.seal_h2(c, cid, n, h1, ip, s)
    assert len(h2) == m.H2_BYTES == 76
    assert m.open_h2(c, cid, n, h1, h2) == (ip, s)
    for index in (0, 12, 40, 75):  # IV, ciphertext, and tag
        tampered = bytearray(h2)
        tampered[index] ^= 1
        with pytest.raises(m.HandshakeError):
            m.open_h2(c, cid, n, h1, bytes(tampered))
    with pytest.raises(m.HandshakeError):  # another N, i.e. a replayed H2 for a different request
        m.open_h2(c, cid, b"\x05" * 32, h1, h2)
    with pytest.raises(m.HandshakeError):
        m.open_h2(b"\x06" * 32, cid, n, h1, h2)
    assert m.seal_h2(c, cid, n, h1, ip, s)[:12] != h2[:12], "every H2 gets a fresh IV"


def test_ipv4_is_ipv6_mapped_and_tailscale_ranges_are_exact():
    assert m.ip_bytes("100.64.0.1") == bytes(10) + b"\xff\xff" + bytes([100, 64, 0, 1])
    assert m.is_tailscale_address("100.64.0.1") and m.is_tailscale_address("100.127.255.254")
    assert m.is_tailscale_address("fd7a:115c:a1e0::5") and m.is_tailscale_address("::ffff:100.100.1.1")
    for address in ("100.63.255.255", "100.128.0.0", "127.0.0.1", "10.0.0.1", "fd7a:115c:a1e1::1", "testclient", ""):
        assert not m.is_tailscale_address(address), address


@pytest.mark.parametrize("value", [None, 5, "", "AAAA", "A" * 21, "A" * 23, "AAAAAAAAAAAAAAAAAAAAA+", "AAAAAAAAAAAAAAAAAAAAA=", "AAAAAAAAAAAAAAAAAAAAAB", "ÀAAAAAAAAAAAAAAAAAAAAA"])
def test_base64url_fields_must_be_exact_and_canonical(value):
    with pytest.raises(m.HandshakeError):
        m.b64decode(value, 16)


def test_base64url_round_trip():
    assert m.b64decode(m.b64encode(b"\xff" * 16), 16) == b"\xff" * 16


def test_missing_cryptography_names_the_management_extra(monkeypatch):
    real_import = builtins.__import__

    def without(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without)
    with pytest.raises(MissingExtraError) as error:
        m.seal_h2(bytes(32), bytes(16), bytes(32), bytes(32), bytes(16), bytes(32))
    assert "pip install 'bad-decisions[management]'" in error.value.message


# --- settings -----------------------------------------------------------------------

MANAGEMENT_ENV = {
    "BAD_DECISIONS_MANAGEMENT_DB": "/srv/management.sqlite3",
    "BAD_DECISIONS_MANAGEMENT_PUBLIC_ORIGIN": PUBLIC,
    "BAD_DECISIONS_MANAGEMENT_ATTEST_URL": ATTEST,
    "BAD_DECISIONS_MANAGEMENT_TAGS": "tag:mgmt, tag:ops",
}


def _settings(monkeypatch, **overrides):
    for key, value in {**MANAGEMENT_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return Settings.from_env()


def test_management_is_off_by_default():
    assert not Settings.from_env().management_enabled


def test_complete_management_settings_parse(monkeypatch):
    settings = _settings(monkeypatch, BAD_DECISIONS_MANAGEMENT_NODES="nA1,nB2")
    assert settings.management_enabled
    assert settings.management_tags == ("tag:mgmt", "tag:ops") and settings.management_nodes == ("nA1", "nB2")
    assert settings.management_session_ttl_seconds == 180


@pytest.mark.parametrize("overrides,message", [
    ({"BAD_DECISIONS_MANAGEMENT_TAGS": None}, "missing BAD_DECISIONS_MANAGEMENT_TAGS"),
    ({"BAD_DECISIONS_MANAGEMENT_DB": None}, "missing BAD_DECISIONS_MANAGEMENT_DB"),
    ({"BAD_DECISIONS_MANAGEMENT_DB": "relative.sqlite3"}, "absolute"),
    ({"BAD_DECISIONS_MANAGEMENT_PUBLIC_ORIGIN": "http://games.example"}, "PUBLIC_ORIGIN"),
    ({"BAD_DECISIONS_MANAGEMENT_PUBLIC_ORIGIN": "https://games.example/path"}, "PUBLIC_ORIGIN"),
    ({"BAD_DECISIONS_MANAGEMENT_ATTEST_URL": "http://box.tail1234.ts.net"}, "ATTEST_URL"),
    ({"BAD_DECISIONS_MANAGEMENT_ATTEST_URL": f"{ATTEST}/attest"}, "ATTEST_URL"),
    ({"BAD_DECISIONS_MANAGEMENT_ATTEST_URL": "https://games.example:8443"}, "tailnet host"),
    ({"BAD_DECISIONS_MANAGEMENT_TAGS": "mgmt"}, "TAGS"),
    ({"BAD_DECISIONS_MANAGEMENT_TAGS": "tag:mg mt"}, "TAGS"),
    ({"BAD_DECISIONS_MANAGEMENT_NODES": "n-1"}, "NODES"),
    ({"BAD_DECISIONS_MANAGEMENT_SESSION_TTL_SECONDS": "60"}, "at least 90"),
    ({"BAD_DECISIONS_TAILSCALE_SOCKET": "tailscaled.sock"}, "TAILSCALE_SOCKET"),
])
def test_bad_or_partial_management_settings_fail_at_startup(monkeypatch, overrides, message):
    with pytest.raises(ValueError, match=message):
        _settings(monkeypatch, **overrides)


def test_a_stray_management_setting_alone_is_an_error(monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_MANAGEMENT_NODES", "nA1")
    with pytest.raises(ValueError, match="needs all of its settings"):
        Settings.from_env()


# --- authorization (checks 5-7) -------------------------------------------------------

def test_authorize_accepts_a_tagged_known_node():
    attestation = m.authorize(FakeTailnet(), PEER, POLICY)
    assert attestation == m.Attestation(PEER, NODE_ID, "laptop.tail1234.ts.net", ("tag:mgmt",))


@pytest.mark.parametrize("tailnet,address,policy", [
    (FakeTailnet(), "203.0.113.9", POLICY),                    # not a Tailscale address
    (FakeTailnet(), "100.64.0.9", POLICY),                     # not a known peer
    (FakeTailnet(sharee=True), PEER, POLICY),                  # shared in from another tailnet
    (FakeTailnet(sharer=42), PEER, POLICY),                    # whois says shared
    (FakeTailnet(whois_id="nSomeoneElse"), PEER, POLICY),      # status and whois disagree
    (FakeTailnet(tags=()), PEER, POLICY),                      # untagged
    (FakeTailnet(tags=("tag:ci",)), PEER, POLICY),             # wrong tag
    (FakeTailnet(), PEER, m.Policy(frozenset({"tag:mgmt"}), frozenset({"nOther"}))),  # not on node allowlist
])
def test_authorize_refuses_everything_else(tailnet, address, policy):
    with pytest.raises(m.HandshakeError):
        m.authorize(tailnet, address, policy)


def test_authorize_never_asks_tailscaled_about_non_tailscale_addresses():
    tailnet = FakeTailnet()
    with pytest.raises(m.HandshakeError):
        m.authorize(tailnet, "127.0.0.1", POLICY)
    assert tailnet.calls == []


# --- store state machine ------------------------------------------------------------------

@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return m.ManagementStore(tmp_path / "management.sqlite3", now=clock)


def _attest(store, issued, tailnet=None, *, n=b"\x07" * 32, peer=PEER):
    h1 = m.compute_h1(issued.c, issued.cid, n)
    body = {"cid": m.b64encode(issued.cid), "n": m.b64encode(n), "h1": m.b64encode(h1)}
    h2 = m.attest(store, tailnet or FakeTailnet(), POLICY, peer, body)
    return m.open_h2(issued.c, issued.cid, n, h1, h2)


def _r(issued, ip, s):
    return m.compute_r(s, issued.cid, issued.c, ip)


def test_full_handshake_issues_a_lease(store, clock):
    issued = store.issue()
    ip, s = _attest(store, issued)
    assert ip == m.ip_bytes(PEER)
    token, session = store.redeem(issued.cid, _r(issued, ip, s), issued.state_token, None)
    assert session.node_id == NODE_ID and session.expires_at == clock.now + 180
    assert store.session(token) == session
    clock.now += 181
    assert store.session(token) is None, "the lease is enforced by the server clock"


def test_attest_is_single_use_and_a_race_has_one_winner(store):
    issued = store.issue()
    ip, s = _attest(store, issued)
    with pytest.raises(m.HandshakeError):
        _attest(store, issued)
    store.redeem(issued.cid, _r(issued, ip, s), issued.state_token, None)  # the loser did not burn the winner
    second = store.issue()
    store.pending(second.cid)
    store.mark_attested(second.cid, m.Attestation(PEER, NODE_ID, "laptop", ("tag:mgmt",)))
    with pytest.raises(m.HandshakeError, match="race"):
        store.mark_attested(second.cid, m.Attestation(PEER, NODE_ID, "laptop", ("tag:mgmt",)))


def test_forged_h1_burns_the_challenge(store):
    issued = store.issue()
    body = {"cid": m.b64encode(issued.cid), "n": m.b64encode(b"\x01" * 32), "h1": m.b64encode(b"\x02" * 32)}
    with pytest.raises(m.HandshakeError, match="H1"):
        m.attest(store, FakeTailnet(), POLICY, PEER, body)
    with pytest.raises(m.HandshakeError):
        _attest(store, issued)


def test_unauthorized_node_burns_the_challenge_after_proving_h1(store):
    issued = store.issue()
    with pytest.raises(m.HandshakeError, match="tag"):
        _attest(store, issued, FakeTailnet(tags=("tag:ci",)))
    with pytest.raises(m.HandshakeError):
        _attest(store, issued)


def test_tailscaled_failure_burns_and_propagates(store):
    issued = store.issue()
    with pytest.raises(OSError):
        _attest(store, issued, FakeTailnet(fail=True))
    with pytest.raises(m.HandshakeError):
        _attest(store, issued)


def test_extra_or_missing_attest_fields_are_refused(store):
    issued = store.issue()
    for body in ({}, {"cid": m.b64encode(issued.cid)}, {"cid": m.b64encode(issued.cid), "n": "x", "h1": "y", "ip": PEER}, [], "x"):
        with pytest.raises(m.HandshakeError):
            m.attest(store, FakeTailnet(), POLICY, PEER, body)


def test_attest_window_expires(store, clock):
    issued = store.issue()
    clock.now += m.ATTEST_WINDOW_SECONDS + 1
    with pytest.raises(m.HandshakeError):
        _attest(store, issued)


def test_redeem_window_expires(store, clock):
    issued = store.issue()
    ip, s = _attest(store, issued)
    clock.now += m.REDEEM_WINDOW_SECONDS + 1
    with pytest.raises(m.HandshakeError):
        store.redeem(issued.cid, _r(issued, ip, s), issued.state_token, None)


def test_redeem_needs_an_attested_challenge(store):
    issued = store.issue()
    with pytest.raises(m.HandshakeError, match="not attested"):
        store.redeem(issued.cid, b"\x00" * 32, issued.state_token, None)


@pytest.mark.parametrize("cookie", [None, "", "wrong"])
def test_redeem_needs_the_browser_bound_state_cookie(store, cookie):
    issued = store.issue()
    ip, s = _attest(store, issued)
    with pytest.raises(m.HandshakeError, match="state cookie"):
        store.redeem(issued.cid, _r(issued, ip, s), cookie, None)
    with pytest.raises(m.HandshakeError):  # burned
        store.redeem(issued.cid, _r(issued, ip, s), issued.state_token, None)


def test_redeem_uses_the_recorded_ip_not_one_the_client_chooses(store):
    issued = store.issue()
    _, s = _attest(store, issued)
    with pytest.raises(m.HandshakeError, match="R does not match"):
        store.redeem(issued.cid, _r(issued, m.ip_bytes("100.64.0.99"), s), issued.state_token, None)


def test_s_alone_is_not_enough_without_c(store):
    issued = store.issue()
    ip, s = _attest(store, issued)
    forged = m.compute_r(s, issued.cid, b"\x00" * 32, ip)
    with pytest.raises(m.HandshakeError):
        store.redeem(issued.cid, forged, issued.state_token, None)


def test_recorded_r_cannot_be_replayed(store):
    issued = store.issue()
    ip, s = _attest(store, issued)
    r = _r(issued, ip, s)
    store.redeem(issued.cid, r, issued.state_token, None)
    with pytest.raises(m.HandshakeError):
        store.redeem(issued.cid, r, issued.state_token, None)


def test_renewal_rotates_the_session_token(store):
    first = store.issue()
    ip, s = _attest(store, first)
    old, _ = store.redeem(first.cid, _r(first, ip, s), first.state_token, None)
    second = store.issue()
    ip, s = _attest(store, second)
    new, _ = store.redeem(second.cid, _r(second, ip, s), second.state_token, old)
    assert store.session(old) is None and store.session(new) is not None


def test_pending_challenges_are_capped(store, monkeypatch):
    monkeypatch.setattr(m, "MAX_PENDING_CHALLENGES", 3)
    for _ in range(3):
        store.issue()
    with pytest.raises(m.HandshakeError, match="too many"):
        store.issue()


def test_cleanup_removes_dead_challenges_and_expired_sessions(store, clock):
    issued = store.issue()
    ip, s = _attest(store, issued)
    token, _ = store.redeem(issued.cid, _r(issued, ip, s), issued.state_token, None)
    store.issue()
    clock.now += 1000
    store.cleanup()
    assert store._read("SELECT COUNT(*) FROM challenges")[0] == 0
    assert store._read("SELECT COUNT(*) FROM sessions")[0] == 0
    assert store.session(token) is None


# --- attest service -------------------------------------------------------------------------

@pytest.fixture
def management_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_PEER_PRESSURE_DIR", str(tmp_path / "rooms"))
    for key, value in {**MANAGEMENT_ENV, "BAD_DECISIONS_MANAGEMENT_DB": str(tmp_path / "management.sqlite3")}.items():
        monkeypatch.setenv(key, value)
    return tmp_path


def _attest_client(tailnet=None, peer=PEER):
    app = m.create_attest_app(Settings.from_env(), tailnet or FakeTailnet())
    return TestClient(app, base_url=ATTEST, client=(peer, 40000))


def _public_client():
    return TestClient(create_app(), base_url=PUBLIC, headers={"Origin": PUBLIC})


ATTEST_HEADERS = {"Origin": PUBLIC, "Content-Type": "application/json"}


def _browser_handshake(public, attest, *, n=b"\x09" * 32, headers=ATTEST_HEADERS):
    """What manage.js does, step by step."""
    challenge = public.post("/v2/manage/auth/challenge").json()
    assert challenge["attest_url"] == f"{ATTEST}/attest"
    cid, c = m.b64decode(challenge["cid"], 16), m.b64decode(challenge["c"], 32)
    h1 = m.compute_h1(c, cid, n)
    response = attest.post("/attest", headers=headers, content=json.dumps({"cid": challenge["cid"], "n": m.b64encode(n), "h1": m.b64encode(h1)}))
    if response.status_code != 200:
        return challenge, response
    ip, s = m.open_h2(c, cid, n, h1, m.b64decode(response.json()["h2"], 76))
    return challenge, public.post("/v2/manage/auth/redeem", json={"cid": challenge["cid"], "r": m.b64encode(m.compute_r(s, cid, c, ip))})


def test_end_to_end_sign_in_renewal_and_logout(management_env):
    with _public_client() as public, _attest_client() as attest:
        assert public.get("/v2/manage/overview").status_code == 401
        _, redeemed = _browser_handshake(public, attest)
        assert redeemed.status_code == 200, redeemed.text
        assert redeemed.json()["node_id"] == NODE_ID and redeemed.json()["renew_after_seconds"] == 60
        cookie = redeemed.headers["set-cookie"]
        assert "bd_mgmt_session=" in cookie and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
        assert "Path=/bad-decisions/v2/manage/" in cookie or "Path=/v2/manage/" in cookie
        overview = public.get("/v2/manage/overview")
        assert overview.status_code == 200 and overview.headers["Cache-Control"] == "no-store"
        assert overview.json()["session"]["node"] == "laptop.tail1234.ts.net"
        assert overview.json()["packs"] and overview.json()["service"]["pack_count"] == len(overview.json()["packs"])
        old = public.cookies.get("bd_mgmt_session")
        _, renewed = _browser_handshake(public, attest, n=b"\x0a" * 32)
        assert renewed.status_code == 200 and public.cookies.get("bd_mgmt_session") != old
        assert public.post("/v2/manage/auth/logout").status_code == 204
        assert public.get("/v2/manage/overview").status_code == 401


def test_attest_refuses_a_foreign_origin_without_touching_the_challenge(management_env):
    with _public_client() as public, _attest_client() as attest:
        challenge, response = _browser_handshake(public, attest, headers={"Origin": "https://evil.example", "Content-Type": "application/json"})
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
        store = m.ManagementStore(management_env / "management.sqlite3")
        store.pending(m.b64decode(challenge["cid"], 16))  # still pending: a hostile page cannot burn it either


@pytest.mark.parametrize("headers", [
    {"Content-Type": "application/json"},                                   # no Origin at all
    {"Origin": "null", "Content-Type": "application/json"},
    {"Origin": PUBLIC, "Content-Type": "text/plain"},                       # a "simple" request that skips preflight
    {"Origin": PUBLIC, "Content-Type": "application/x-www-form-urlencoded"},
])
def test_attest_refuses_requests_a_hostile_page_could_send(management_env, headers):
    with _public_client() as public, _attest_client() as attest:
        _, response = _browser_handshake(public, attest, headers=headers)
        assert response.status_code == 403


def test_attest_refuses_a_rebound_host_name(management_env):
    with _public_client() as public:
        app = m.create_attest_app(Settings.from_env(), FakeTailnet())
        with TestClient(app, base_url="https://attacker.example:8443", client=(PEER, 40000)) as attest:
            _, response = _browser_handshake(public, attest)
    assert response.status_code == 403


def test_attest_refuses_non_tailscale_and_unauthorized_peers(management_env):
    with _public_client() as public:
        for client in (_attest_client(peer="127.0.0.1"), _attest_client(FakeTailnet(tags=("tag:ci",))), _attest_client(FakeTailnet(sharee=True))):
            with client as attest:
                _, response = _browser_handshake(public, attest)
                assert response.status_code == 403
                assert response.headers["access-control-allow-origin"] == PUBLIC  # the page may read the refusal
                assert response.json()["error"]["code"] == "attestation_failed"  # no hint which check failed


def test_attest_reports_tailscaled_outage_as_503(management_env):
    with _public_client() as public, _attest_client(FakeTailnet(fail=True)) as attest:
        _, response = _browser_handshake(public, attest)
    assert response.status_code == 503


def test_attest_body_is_bounded_and_must_be_json(management_env):
    with _attest_client() as attest:
        assert attest.post("/attest", headers=ATTEST_HEADERS, content=b"{" + b" " * 2000 + b"}").status_code == 403
        assert attest.post("/attest", headers=ATTEST_HEADERS, content=b"not json").status_code == 403


def test_attest_preflight_allows_only_the_public_origin(management_env):
    request = {"Origin": PUBLIC, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type",
               "Access-Control-Request-Private-Network": "true"}
    with _attest_client() as attest:
        allowed = attest.options("/attest", headers=request)
        assert allowed.status_code == 204
        assert allowed.headers["access-control-allow-origin"] == PUBLIC
        assert allowed.headers["access-control-allow-private-network"] == "true"
        assert attest.options("/attest", headers={**request, "Origin": "https://evil.example"}).status_code == 403
        assert attest.options("/attest", headers={**request, "Access-Control-Request-Method": "PUT"}).status_code == 403
        assert attest.options("/attest", headers={**request, "Access-Control-Request-Headers": "content-type, authorization"}).status_code == 403


def test_attest_service_exposes_nothing_else(management_env):
    app = m.create_attest_app(Settings.from_env(), FakeTailnet())
    paths = {route.path for route in app.routes}
    assert paths == {"/attest"}


def test_attest_service_refuses_to_start_without_management_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_PEER_PRESSURE_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="attest service"):
        m.create_attest_app(Settings.from_env(), FakeTailnet())


# --- public app ----------------------------------------------------------------------------------

def test_management_routes_are_absent_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_PEER_PRESSURE_DIR", str(tmp_path))
    with TestClient(create_app(), base_url=PUBLIC, headers={"Origin": PUBLIC}) as client:
        for method, path in (("GET", "/manage"), ("POST", "/v2/manage/auth/challenge"), ("POST", "/v2/manage/auth/redeem"),
                             ("GET", "/v2/manage/overview"), ("POST", "/v2/manage/auth/logout")):
            response = client.request(method, path, json={"cid": "x", "r": "y"} if path.endswith("redeem") else None)
            assert response.status_code == 404, path


def test_public_management_posts_refuse_other_origins(management_env):
    with TestClient(create_app(), base_url=PUBLIC) as client:
        for origin in (None, "https://evil.example"):
            headers = {"Origin": origin} if origin else {}
            assert client.post("/v2/manage/auth/challenge", headers=headers).status_code == 403
            assert client.post("/v2/manage/auth/redeem", headers=headers, json={"cid": "x", "r": "y"}).status_code == 403


def test_challenge_cookie_is_browser_bound_and_scoped(management_env):
    with _public_client() as public:
        response = public.post("/v2/manage/auth/challenge")
    cookie = response.headers["set-cookie"]
    assert response.headers["Cache-Control"] == "no-store"
    assert "bd_mgmt_state=" in cookie and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "/v2/manage/auth/" in cookie


def test_redeem_with_another_browsers_cookie_fails(management_env):
    with _public_client() as public, _public_client() as other, _attest_client() as attest:
        challenge = public.post("/v2/manage/auth/challenge").json()
        other.post("/v2/manage/auth/challenge")  # a different browser with its own state cookie
        cid, c = m.b64decode(challenge["cid"], 16), m.b64decode(challenge["c"], 32)
        n = b"\x0b" * 32
        h1 = m.compute_h1(c, cid, n)
        h2 = attest.post("/attest", headers=ATTEST_HEADERS, content=json.dumps({"cid": challenge["cid"], "n": m.b64encode(n), "h1": m.b64encode(h1)})).json()["h2"]
        ip, s = m.open_h2(c, cid, n, h1, m.b64decode(h2, 76))
        r = m.b64encode(m.compute_r(s, cid, c, ip))
        assert other.post("/v2/manage/auth/redeem", json={"cid": challenge["cid"], "r": r}).status_code == 401
        assert public.post("/v2/manage/auth/redeem", json={"cid": challenge["cid"], "r": r}).status_code == 401, "burned"


@pytest.mark.parametrize("body", [{"cid": "x", "r": "y"}, {"cid": "A" * 22, "r": "A" * 43, "extra": 1}, {"cid": 5, "r": "A" * 43}])
def test_malformed_redeem_bodies_are_refused(management_env, body):
    with _public_client() as public:
        assert public.post("/v2/manage/auth/redeem", json=body).status_code in {401, 422}


def test_management_page_is_served_only_when_enabled_and_cannot_be_framed(management_env):
    with _public_client() as public:
        page = public.get("/manage")
    assert page.status_code == 200 and page.headers["X-Frame-Options"] == "DENY"
    assert "manage.js?v=" in page.text and "__ASSET_VERSION__" not in page.text


def test_management_surface_is_read_only():
    """Only the handshake and logout may POST; nothing under /manage can change packs or rooms."""
    routes = {(method, route.path) for route in create_app().routes for method in getattr(route, "methods", ()) if "manage" in route.path}
    assert routes == {
        ("GET", "/v2/manage/status"), ("GET", "/v2/manage/overview"), ("GET", "/manage"),
        ("POST", "/v2/manage/auth/challenge"), ("POST", "/v2/manage/auth/redeem"), ("POST", "/v2/manage/auth/logout"),
    }


# --- LocalAPI client -------------------------------------------------------------------------------

def test_local_api_speaks_http_over_the_unix_socket(tmp_path):
    import http.server
    import socketserver
    import threading

    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Host"), self.headers.get("Sec-Tailscale")))
            body = json.dumps({"Peer": {}} if self.path.endswith("/status") else {"Node": {"StableID": "n1"}}).encode()
            code = 404 if "100.64.0.9" in self.path else 200
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Server(socketserver.UnixStreamServer):
        def get_request(self):
            request, _ = super().get_request()
            return request, ("local", 0)

    path = str(tmp_path / "tailscaled.sock")
    server = Server(path, Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        api = m.LocalAPI(path)
        assert api.status() == {"Peer": {}}
        assert api.whois("100.64.0.1") == {"Node": {"StableID": "n1"}}
        assert api.whois("100.64.0.9") is None
    finally:
        server.shutdown()
        server.server_close()
    assert requests[0] == ("/localapi/v0/status", "local-tailscaled.sock", "localapi")
    assert requests[1][0] == "/localapi/v0/whois?addr=100.64.0.1"


# --- the browser client agrees with the server ------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_manage_js_matches_the_python_protocol():
    cid, c, n, s = bytes(range(16)), bytes(range(32, 64)), bytes(range(64, 96)), bytes(range(96, 128))
    ip = m.ip_bytes(PEER)
    h1 = m.compute_h1(c, cid, n)
    h2 = m.seal_h2(c, cid, n, h1, ip, s)
    values = {name: m.b64encode(value) for name, value in {"cid": cid, "c": c, "n": n, "h2": h2}.items()}
    script = f"""
const P = require({json.dumps(str(ROOT / "src/bad_decisions/web/manage.js"))});
const v = {json.dumps(values)};
(async () => {{
  const cid = P.b64decode(v.cid, 16), c = P.b64decode(v.c, 32), n = P.b64decode(v.n, 32);
  const h1 = await P.computeH1(c, cid, n);
  const {{ ip, s }} = await P.openH2(c, cid, n, h1, P.b64decode(v.h2, 76));
  const r = await P.computeR(s, cid, c, ip);
  let tamperRejected = false;
  const bad = P.b64decode(v.h2, 76); bad[20] ^= 1;
  try {{ await P.openH2(c, cid, n, h1, bad); }} catch (_) {{ tamperRejected = true; }}
  let lengthRejected = false;
  try {{ P.b64decode(v.cid, 32); }} catch (_) {{ lengthRejected = true; }}
  console.log(JSON.stringify({{ h1: P.b64encode(h1), ip: P.b64encode(ip), s: P.b64encode(s), r: P.b64encode(r), tamperRejected, lengthRejected }}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    output = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True).stdout
    result = json.loads(output)
    assert result["h1"] == m.b64encode(h1)
    assert result["ip"] == m.b64encode(ip) and result["s"] == m.b64encode(s)
    assert result["r"] == m.b64encode(m.compute_r(s, cid, c, ip))
    assert result["tamperRejected"] and result["lengthRejected"]


# --- deployment (static: tests never run deploy.sh) ---------------------------------------------------

def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_deploy_dispatches_install_management_and_keeps_its_dependencies():
    script = _read("deploy.sh")
    assert "[[ ${1:-} == install-management ]]" in script and "deploy/install-management.sh" in script
    assert '[[ -d ${APP_ROOT}/management ]] && LOCK_FILE=${SCRIPT_DIR}/requirements-management.lock' in script
    for path in ("deploy.sh", "deploy/install-management.sh"):
        assert subprocess.run(["bash", "-n", str(ROOT / path)]).returncode == 0


def test_attest_unit_binds_the_tailnet_address_with_tls_and_no_proxy_headers():
    unit = _read("deploy/management-attest.service.template")
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert "bad_decisions.management:create_attest_app --factory" in exec_start
    assert "--host @ATTEST_BIND@" in exec_start and "0.0.0.0" not in exec_start
    assert "--no-proxy-headers" in exec_start and "--forwarded-allow-ips" not in exec_start
    assert "--ssl-certfile @TLS_DIR@/attest.crt" in exec_start and "--ssl-keyfile @TLS_DIR@/attest.key" in exec_start
    assert "--workers 1" in exec_start
    assert "PartOf=@SERVICE_NAME@.service" in unit and "ReadWritePaths=@APP_ROOT@/management" in unit


def test_install_management_is_portable_and_verifies_origin_enforcement():
    script = _read("deploy/install-management.sh")
    for template in ("management-attest.service.template", "management-cert.service.template", "management-cert.timer.template"):
        assert template in script
        text = _read(f"deploy/{template}")
        assert "ts.net" not in text and "/home/" not in text, template
    assert "ts.net" not in script.replace("# ", "")  # names come from tailscale status, never hard-coded
    assert 'MANAGEMENT_PUBLIC_ORIGIN}' in script and "https://example.invalid" in script, "smoke test proves a foreign origin is refused"
    assert "import cryptography" in script, "refuses a serving release without the [management] dependencies"
    assert "trap restore_on_error ERR" in script
