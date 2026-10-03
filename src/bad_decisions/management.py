"""Tailnet liveness handshake for the read-only management page.

docs/MANAGEMENT_AUTH.md is the specification; keep the two in step. The public
app issues challenges and redeems them (stdlib only). The attest service, a
separate process bound to the host's tailnet IP, checks the peer with
tailscaled's LocalAPI and answers with an AES-256-GCM H2, so only it needs the
optional ``management`` extra.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import secrets
import socket
import sqlite3
import threading
import time
import urllib.parse
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

CID_BYTES = 16
SECRET_BYTES = 32  # C, S, N, and session tokens
IP_BYTES = 16
H1_LABEL = b"bd-mgmt/v1/h1"
H2_LABEL = b"bd-mgmt/v1/h2"
REDEEM_LABEL = b"bd-mgmt/v1/redeem"
GCM_IV_BYTES = 12
GCM_TAG_BYTES = 16
H2_BYTES = GCM_IV_BYTES + IP_BYTES + SECRET_BYTES + GCM_TAG_BYTES
ATTEST_WINDOW_SECONDS = 30  # challenge -> attest
REDEEM_WINDOW_SECONDS = 10  # attest -> redeem
MAX_PENDING_CHALLENGES = 500
MAX_ATTEST_BODY_BYTES = 1024
TAILSCALE_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
DEFAULT_TAILSCALE_SOCKET = "/var/run/tailscale/tailscaled.sock"

logger = logging.getLogger("bad_decisions.management")


class HandshakeError(Exception):
    """A handshake step failed. ``reason`` is for logs only; clients get a generic error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --- encoding ---------------------------------------------------------------------

def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64decode(value: object, size: int) -> bytes:
    """Decode unpadded base64url of exactly ``size`` bytes, or raise HandshakeError."""
    if not isinstance(value, str) or len(value) != (size * 4 + 2) // 3 or not value.isascii():
        raise HandshakeError("malformed field")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise HandshakeError("malformed field") from exc
    if len(decoded) != size or b64encode(decoded) != value:  # reject non-canonical encodings
        raise HandshakeError("malformed field")
    return decoded


def ip_bytes(address: str) -> bytes:
    """A tailnet address as 16 bytes, IPv4 in IPv6-mapped form."""
    parsed = ipaddress.ip_address(address)
    if isinstance(parsed, ipaddress.IPv4Address):
        parsed = ipaddress.IPv6Address(f"::ffff:{parsed}")
    return parsed.packed


def is_tailscale_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return any(parsed in network for network in TAILSCALE_NETWORKS)


# --- protocol -----------------------------------------------------------------------

def _hmac(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF with SHA-256."""
    prk = _hmac(salt, ikm)
    output, block = b"", b""
    for counter in range(1, -(-length // 32) + 1):
        block = _hmac(prk, block + info + bytes([counter]))
        output += block
    return output[:length]


def compute_h1(c: bytes, cid: bytes, n: bytes) -> bytes:
    return _hmac(c, H1_LABEL + cid + n)


def h2_key(c: bytes, h1: bytes) -> bytes:
    return hkdf_sha256(c, h1, H2_LABEL, 32)


def h2_aad(cid: bytes, n: bytes, h1: bytes) -> bytes:
    return H2_LABEL + cid + n + h1


def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        from .errors import MissingExtraError

        raise MissingExtraError("management") from exc
    return AESGCM(key)


def seal_h2(c: bytes, cid: bytes, n: bytes, h1: bytes, ip: bytes, s: bytes, *, iv: bytes | None = None) -> bytes:
    iv = secrets.token_bytes(GCM_IV_BYTES) if iv is None else iv
    return iv + _aesgcm(h2_key(c, h1)).encrypt(iv, ip + s, h2_aad(cid, n, h1))


def open_h2(c: bytes, cid: bytes, n: bytes, h1: bytes, h2: bytes) -> tuple[bytes, bytes]:
    """The client's side of step 3, used by tests to check the JavaScript client."""
    from cryptography.exceptions import InvalidTag

    if len(h2) != H2_BYTES:
        raise HandshakeError("malformed h2")
    try:
        plain = _aesgcm(h2_key(c, h1)).decrypt(h2[:GCM_IV_BYTES], h2[GCM_IV_BYTES:], h2_aad(cid, n, h1))
    except InvalidTag as exc:
        raise HandshakeError("h2 authentication failed") from exc
    return plain[:IP_BYTES], plain[IP_BYTES:]


def compute_r(s: bytes, cid: bytes, c: bytes, ip: bytes) -> bytes:
    return _hmac(s, REDEEM_LABEL + cid + c + ip)


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


# --- shared state -------------------------------------------------------------------

@dataclass(frozen=True)
class Attestation:
    ip: str
    node_id: str
    node_name: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Session:
    node_id: str
    node_name: str
    tags: tuple[str, ...]
    last_attested_at: float
    expires_at: float


@dataclass(frozen=True)
class IssuedChallenge:
    cid: bytes
    c: bytes
    state_token: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS challenges (
    cid BLOB PRIMARY KEY,
    c BLOB NOT NULL,
    s BLOB NOT NULL,
    state_hash BLOB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'attested', 'redeemed', 'burned')),
    created_at REAL NOT NULL,
    attest_deadline REAL NOT NULL,
    redeem_deadline REAL,
    attested_ip BLOB,
    attested_node_id TEXT,
    attested_node_name TEXT,
    attested_tags TEXT,
    attested_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash BLOB PRIMARY KEY,
    node_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    tags TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_attested_at REAL NOT NULL
);
"""


class ManagementStore:
    """Challenges and session leases in one SQLite file shared by both processes.

    Every state change is a conditional UPDATE that must change exactly one row,
    so racing requests cannot both win. Nothing slow runs inside a transaction.
    """

    def __init__(self, path: str | Path, *, session_ttl_seconds: int = 180, now: Callable[[], float] = time.time):
        self.path = str(path)
        self.session_ttl_seconds = session_ttl_seconds
        self.now = now
        with closing(self._connect()) as connection, connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _write(self, sql: str, params: tuple = ()) -> int:
        with closing(self._connect()) as connection:
            return connection.execute(sql, params).rowcount

    def _read(self, sql: str, params: tuple = ()) -> tuple | None:
        with closing(self._connect()) as connection:
            return connection.execute(sql, params).fetchone()

    def cleanup(self) -> None:
        now = self.now()
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM challenges WHERE status IN ('redeemed', 'burned') OR MAX(attest_deadline, COALESCE(redeem_deadline, 0)) < ?",
                (now,),
            )
            connection.execute("DELETE FROM sessions WHERE last_attested_at + ? < ?", (self.session_ttl_seconds, now))

    # step 1
    def issue(self) -> IssuedChallenge:
        self.cleanup()
        now = self.now()
        pending = self._read("SELECT COUNT(*) FROM challenges WHERE status = 'pending' AND attest_deadline >= ?", (now,))[0]
        if pending >= MAX_PENDING_CHALLENGES:
            raise HandshakeError("too many pending challenges")
        cid, c, s = secrets.token_bytes(CID_BYTES), secrets.token_bytes(SECRET_BYTES), secrets.token_bytes(SECRET_BYTES)
        state_token = b64encode(secrets.token_bytes(SECRET_BYTES))
        self._write(
            "INSERT INTO challenges (cid, c, s, state_hash, status, created_at, attest_deadline) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (cid, c, s, _digest(state_token.encode()), now, now + ATTEST_WINDOW_SECONDS),
        )
        return IssuedChallenge(cid, c, state_token)

    def burn(self, cid: bytes, status: str) -> None:
        """Burn a challenge still in ``status``. A request that lost a race never burns the winner's newer state."""
        self._write("UPDATE challenges SET status = 'burned' WHERE cid = ? AND status = ?", (cid, status))

    # step 2 (attest service)
    def pending(self, cid: bytes) -> tuple[bytes, bytes]:
        """Return (C, S) for a live pending challenge, or raise."""
        row = self._read("SELECT c, s FROM challenges WHERE cid = ? AND status = 'pending' AND attest_deadline >= ?", (cid, self.now()))
        if row is None:
            raise HandshakeError("challenge is not pending")
        return row[0], row[1]

    def mark_attested(self, cid: bytes, attestation: Attestation) -> None:
        now = self.now()
        changed = self._write(
            """UPDATE challenges SET status = 'attested', attested_ip = ?, attested_node_id = ?, attested_node_name = ?,
                   attested_tags = ?, attested_at = ?, redeem_deadline = ?
               WHERE cid = ? AND status = 'pending' AND attest_deadline >= ?""",
            (ip_bytes(attestation.ip), attestation.node_id, attestation.node_name, json.dumps(list(attestation.tags)),
             now, now + REDEEM_WINDOW_SECONDS, cid, now),
        )
        if changed != 1:
            raise HandshakeError("lost the attest race or the challenge expired")

    # step 4 (public app)
    def redeem(self, cid: bytes, r: bytes, state_token: str | None, previous_session: str | None) -> tuple[str, Session]:
        """Verify R, move attested -> redeemed, and issue a rotated session token."""
        now = self.now()
        row = self._read(
            """SELECT c, s, state_hash, attested_ip, attested_node_id, attested_node_name, attested_tags
               FROM challenges WHERE cid = ? AND status = 'attested' AND redeem_deadline >= ?""",
            (cid, now),
        )
        if row is None:
            self.burn(cid, "attested")  # expired
            raise HandshakeError("challenge is not attested")
        c, s, state_hash, ip, node_id, node_name, tags = row
        if not state_token or not hmac.compare_digest(_digest(state_token.encode()), state_hash):
            self.burn(cid, "attested")
            raise HandshakeError("state cookie does not match")
        if not hmac.compare_digest(r, compute_r(s, cid, c, ip)):
            self.burn(cid, "attested")
            raise HandshakeError("R does not match")
        if self._write("UPDATE challenges SET status = 'redeemed' WHERE cid = ? AND status = 'attested'", (cid,)) != 1:
            raise HandshakeError("lost the redeem race")
        token = b64encode(secrets.token_bytes(SECRET_BYTES))
        with closing(self._connect()) as connection:
            if previous_session:  # rotation; a renewal from another node also revokes the old lease
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(previous_session.encode()),))
            connection.execute(
                "INSERT INTO sessions (token_hash, node_id, node_name, tags, created_at, last_attested_at) VALUES (?, ?, ?, ?, ?, ?)",
                (_digest(token.encode()), node_id, node_name, tags, now, now),
            )
        return token, Session(node_id, node_name, tuple(json.loads(tags)), now, now + self.session_ttl_seconds)

    def session(self, token: str | None) -> Session | None:
        if not token:
            return None
        row = self._read(
            "SELECT node_id, node_name, tags, last_attested_at FROM sessions WHERE token_hash = ? AND last_attested_at + ? >= ?",
            (_digest(token.encode()), self.session_ttl_seconds, self.now()),
        )
        if row is None:
            return None
        return Session(row[0], row[1], tuple(json.loads(row[2])), row[3], row[3] + self.session_ttl_seconds)

    def end_session(self, token: str | None) -> None:
        if token:
            self._write("DELETE FROM sessions WHERE token_hash = ?", (_digest(token.encode()),))


# --- tailscaled -----------------------------------------------------------------------

class TailnetDirectory(Protocol):
    def status(self) -> dict[str, Any]: ...

    def whois(self, address: str) -> dict[str, Any] | None: ...


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("local-tailscaled.sock", timeout=timeout)
        self.socket_path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class LocalAPI:
    """Read-only queries to tailscaled's LocalAPI over its Unix socket.

    The LocalAPI is not a documented stable interface, so every use of it lives
    here and the parsing below accepts only the fields this module needs.
    """

    def __init__(self, socket_path: str = DEFAULT_TAILSCALE_SOCKET, timeout: float = 3.0):
        self.socket_path = socket_path
        self.timeout = timeout

    def _get(self, path: str) -> tuple[int, Any]:
        with closing(_UnixHTTPConnection(self.socket_path, self.timeout)) as connection:
            connection.request("GET", path, headers={"Host": "local-tailscaled.sock", "Sec-Tailscale": "localapi"})
            response = connection.getresponse()
            body = response.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise OSError("tailscaled response too large")
            return response.status, json.loads(body) if response.status == 200 else None

    def status(self) -> dict[str, Any]:
        code, body = self._get("/localapi/v0/status")
        if code != 200 or not isinstance(body, dict):
            raise OSError(f"tailscaled status failed with HTTP {code}")
        return body

    def whois(self, address: str) -> dict[str, Any] | None:
        code, body = self._get("/localapi/v0/whois?" + urllib.parse.urlencode({"addr": address}))
        if code == 404:
            return None
        if code != 200 or not isinstance(body, dict):
            raise OSError(f"tailscaled whois failed with HTTP {code}")
        return body


@dataclass(frozen=True)
class Policy:
    tags: frozenset[str]
    nodes: frozenset[str] = frozenset()  # empty: any node carrying an allowed tag


def authorize(directory: TailnetDirectory, address: str, policy: Policy) -> Attestation:
    """Checks 5-7: Tailscale address, known non-shared node, allowed tag (and node)."""
    if not is_tailscale_address(address):
        raise HandshakeError("peer is not a Tailscale address")
    canonical = str(ipaddress.ip_address(address))
    peer = None
    for candidate in (directory.status().get("Peer") or {}).values():
        if isinstance(candidate, dict) and canonical in [str(ip) for ip in candidate.get("TailscaleIPs") or ()]:
            peer = candidate
            break
    if peer is None:
        raise HandshakeError("address is not a known peer")
    if peer.get("ShareeNode"):
        raise HandshakeError("peer is shared in from another tailnet")
    who = directory.whois(canonical)
    node = who.get("Node") if isinstance(who, dict) else None
    if not isinstance(node, dict):
        raise HandshakeError("whois has no node")
    if node.get("Sharer"):
        raise HandshakeError("whois reports a shared node")
    node_id = node.get("StableID")
    if not isinstance(node_id, str) or not node_id or node_id != peer.get("ID"):
        raise HandshakeError("status and whois disagree about the node")
    tags = node.get("Tags") or ()
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise HandshakeError("whois tags are malformed")
    if not policy.tags.intersection(tags):
        raise HandshakeError("node has no allowed tag")
    if policy.nodes and node_id not in policy.nodes:
        raise HandshakeError("node is not on the node allowlist")
    name = node.get("Name") if isinstance(node.get("Name"), str) else node_id
    return Attestation(canonical, node_id, name.rstrip("."), tuple(sorted(tags)))


# --- attest service -------------------------------------------------------------------

class _PeerLimiter:
    def __init__(self, per_minute: int = 30, now: Callable[[], float] = time.monotonic):
        self.per_minute, self.now = per_minute, now
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def allow(self, peer: str) -> bool:
        now = self.now()
        with self.lock:
            if len(self.hits) > 10_000:
                self.hits = {key: times for key, times in self.hits.items() if times and now - times[-1] < 60}
            times = [hit for hit in self.hits.get(peer, ()) if now - hit < 60]
            allowed = len(times) < self.per_minute
            if allowed:
                times.append(now)
            self.hits[peer] = times
            return allowed


def attest(store: ManagementStore, directory: TailnetDirectory, policy: Policy, peer: str, body: dict[str, Any]) -> bytes:
    """Steps 2 and 3 after the Origin and Host checks: verify, transition, seal H2."""
    if not isinstance(body, dict) or set(body) != {"cid", "n", "h1"}:
        raise HandshakeError("malformed body")
    cid = b64decode(body["cid"], CID_BYTES)
    try:
        n, h1 = b64decode(body["n"], SECRET_BYTES), b64decode(body["h1"], SECRET_BYTES)
        c, s = store.pending(cid)
        if not hmac.compare_digest(h1, compute_h1(c, cid, n)):
            raise HandshakeError("H1 does not match")
        attestation = authorize(directory, peer, policy)  # outside any transaction
        store.mark_attested(cid, attestation)
    except (HandshakeError, OSError):
        store.burn(cid, "pending")
        raise
    logger.info("attested node=%s name=%s tags=%s", attestation.node_id, attestation.node_name, ",".join(attestation.tags))
    return seal_h2(c, cid, n, h1, ip_bytes(attestation.ip), s)


def create_attest_app(settings=None, directory: TailnetDirectory | None = None):
    """The tailnet-only attest service. Run it bound to the tailnet IP, with TLS and no proxy headers."""
    from .settings import Settings

    settings = settings or Settings.from_env()
    if not settings.management_enabled:
        raise ValueError("the attest service needs BAD_DECISIONS_MANAGEMENT_DB and the other management settings")
    _aesgcm(bytes(32))  # fail at startup, not per request, when the extra is missing
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    store = ManagementStore(settings.management_db, session_ttl_seconds=settings.management_session_ttl_seconds)
    directory = directory or LocalAPI(settings.tailscale_socket)
    policy = Policy(frozenset(settings.management_tags), frozenset(settings.management_nodes))
    origin = settings.management_public_origin
    attest_url = urllib.parse.urlsplit(settings.management_attest_url)
    hosts = {attest_url.netloc.lower()}
    if attest_url.port in (None, 443):
        hosts |= {attest_url.hostname.lower(), f"{attest_url.hostname.lower()}:443"}
    limiter = _PeerLimiter()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def refuse(status: int = 403, *, cors: bool = False) -> JSONResponse:
        headers = {"Cache-Control": "no-store"}
        if cors:
            headers |= {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
        return JSONResponse({"error": {"code": "attestation_failed", "message": "Attestation failed", "details": {}}}, status, headers=headers)

    def trusted_request(request: Request) -> bool:
        # Origin first: browsers set it and page scripts cannot, so this is what stops
        # a malicious website from borrowing an allowed device's network position.
        return request.headers.get("origin") == origin and request.headers.get("host", "").lower() in hosts

    @app.options("/attest")
    def preflight(request: Request):
        if not trusted_request(request) or request.headers.get("access-control-request-method") != "POST":
            return Response(status_code=403)
        requested = {item.strip().lower() for item in request.headers.get("access-control-request-headers", "").split(",") if item.strip()}
        if requested - {"content-type"}:
            return Response(status_code=403)
        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        }
        if request.headers.get("access-control-request-private-network") == "true":
            headers["Access-Control-Allow-Private-Network"] = "true"
        return Response(status_code=204, headers=headers)

    @app.post("/attest")
    async def attest_endpoint(request: Request):
        if not trusted_request(request):
            logger.warning("attest refused: untrusted Origin or Host")
            return refuse()
        peer = request.client.host if request.client else ""  # the socket peer; no proxy headers
        if not limiter.allow(peer):
            return JSONResponse({"error": {"code": "rate_limited", "message": "Too many requests", "details": {}}}, 429,
                                headers={"Retry-After": "60", "Access-Control-Allow-Origin": origin, "Vary": "Origin"})
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return refuse(cors=True)
        raw = await request.body()
        if len(raw) > MAX_ATTEST_BODY_BYTES:
            return refuse(cors=True)
        try:
            body = json.loads(raw)
            h2 = attest(store, directory, policy, peer, body)
        except (HandshakeError, ValueError) as exc:
            logger.warning("attest refused peer=%s: %s", peer, getattr(exc, "reason", "malformed JSON"))
            return refuse(cors=True)
        except OSError:
            logger.exception("attest failed: tailscaled is unavailable")
            return refuse(503, cors=True)
        return JSONResponse({"h2": b64encode(h2)}, headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": origin, "Vary": "Origin"})

    return app

