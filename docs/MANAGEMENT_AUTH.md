# Management authentication: the tailnet liveness handshake

The management page is served on the public HTTPS site, but you can only sign
in from a device that is on your tailnet, carries an allowed tag, and is
reachable over the tailnet *right now*. A four-message handshake proves that
liveness. It splits across two channels, the public HTTPS site and a small
attest service that listens only on the tailnet. The page repeats the
handshake silently while it stays open, so access lapses within one session
TTL after the device leaves the tailnet or loses its tag.

The page is read-only. It never imports, edits, or replaces packs; registry
changes still go only through `bad-decisions pack replace-local` and the root
activator.

## Threat model

- **The device proves the person.** An allowed device is one you control and
  have tagged. If that device is compromised, its owner has bigger problems
  than a party game server, so a compromised allowed device is out of scope.
- **In scope:** anyone on the internet; tailnet peers that are not allowed
  (other users' devices, untagged devices, nodes shared in from another
  tailnet); a malicious website opened in a browser on an allowed device; and
  replayed, forged, or tampered handshake messages.
- **Not provided:** proof of which human is at the keyboard, or protection for
  a session cookie copied off a device. The short TTL limits how long a
  copied cookie stays useful.

## Parties and channels

| Name | Where it runs | Reached at |
|---|---|---|
| **Public app** | the normal service, behind nginx | `https://<public site>/…/v2/manage/…` |
| **Attest service** | a separate process bound to the host's tailnet IP, with a `tailscale cert` certificate | `https://<host>.<tailnet>.ts.net:<port>/attest` |
| **Client** | `manage.js` in the browser, using only WebCrypto | both |

The two processes share one SQLite database for challenges and sessions, so
every worker sees the same state.

## Values and encoding

| Value | Size | Made by | Purpose |
|---|---|---|---|
| `cid` | 16 bytes | server | Opaque challenge ID: tells the server which `C` and `S` to use |
| `C` | 32 bytes | server | Challenge; sent only over HTTPS, never over the tailnet |
| `S` | 32 bytes | server | Per-challenge secret; sent only over the tailnet, and only encrypted |
| `N` | 32 bytes | client | Client nonce: freshness the client can check for itself |
| `IP` | 16 bytes | server | The client's tailnet address; IPv4 in IPv6-mapped form |

Every value is random, single-use, and fixed-length. Each MAC input starts with
a distinct version label (`bd-mgmt/v1/…`), so no concatenation can be read two
ways, a MAC from one step can't stand in for another, and a future protocol
version can't be confused with this one. JSON carries bytes as unpadded
base64url. `HMAC` means HMAC-SHA256.

## The handshake

```
 Client                         Public app (HTTPS)            Attest service (tailnet)
   |  1. POST challenge  ----------->|                                  |
   |<-------- {cid, C} + state cookie|                                  |
   |                                                                    |
   |  2. POST /attest {cid, N, H1}  ----------------------------------->|
   |                                     checks: Origin, Host, H1,      |
   |                                     peer IP, status, whois, tags   |
   |<------------------------------------------------------- {H2}  3.  |
   |                                                                    |
   |  4. POST redeem {cid, R} + state cookie -->|                       |
   |<-------------------- session cookie        |                       |
```

### 1. Challenge (HTTPS, server to client)

`POST /v2/manage/auth/challenge` creates `cid`, `C`, and `S`, stores them with
a 30-second deadline for step 2, and returns `{cid, C}`. It also sets a state
cookie (`HttpOnly`, `Secure`, `SameSite=Strict`, scoped to the auth path),
whose hash is stored with the challenge.

**Why it matters:** `C` is fresh and unpredictable, so nothing that follows
can have been computed before this moment. That gives the server its proof of
freshness. The state cookie ties the challenge to this one browser, the same
way OAuth's `state` parameter does, so a challenge issued to anyone else can't
be redeemed here.

### 2. Attest request (tailnet, client to server)

```
H1 = HMAC(key=C, "bd-mgmt/v1/h1" ‖ cid ‖ N)
```

The client picks `N` and sends `{cid, N, H1}` to the attest service as JSON.
The attest service checks, in this order, and burns the challenge on any
failure:

1. **`Origin` is exactly the public site's origin.** Browsers set this header
   and page scripts can't change it.
2. **`Host` is the attest service's own `ts.net` name**, which defeats DNS
   rebinding (the certificate does too).
3. **`cid` is pending and inside its window.** The service then marks it
   attested in the same transaction, so each challenge can be attested once.
4. **`H1` matches**, compared in constant time.
5. **The peer address is a tailnet address**, taken from the socket, not from
   any header. The service binds the tailnet IP directly with no proxy in
   front, so this is the WireGuard-authenticated sender.
6. **The address is a peer in `tailscale status --json`**, and that peer is not
   shared in from another tailnet.
7. **`tailscale whois` reports an allowed tag** (and, if configured, an allowed
   node ID).

**Why it matters:** this message proves the client can *send* on the tailnet
now. H₁ proves the sender holds `C`, which it can only have got from step 1,
yet `C` itself never crosses the tailnet. Checks 5 to 7 overlap on purpose:
WireGuard makes the source address unforgeable, `status` confirms it is a
current peer of this node, and `whois` turns it into an identity that can be
authorized. Check 1 is the one that stops a malicious website: it shares the
operator's network position, and without the `Origin` check it could attest
its own challenge from the operator's browser.

### 3. Attest response (tailnet, server to client)

H₂ carries `IP` and `S` back, encrypted and authenticated with keys only the
holder of `C` can derive:

```
K_enc ‖ K_mac = HKDF-SHA256(ikm=C, salt=H1, info="bd-mgmt/v1/h2", length=64)
keystream     = HMAC(K_enc, cid ‖ 0x00) ‖ HMAC(K_enc, cid ‖ 0x01)
E             = (IP ‖ S) XOR keystream[0:48]
T             = HMAC(K_mac, "bd-mgmt/v1/h2" ‖ cid ‖ N ‖ E)
H2            = E ‖ T                                   (80 bytes)
```

The client derives the same keys, checks `T` first, and only then XORs to
recover `IP` and `S`.

**Why it matters:** receiving this proves the client can also *receive* on the
tailnet now; a one-way path would fail here. Because `T` covers the client's
own `N`, the client knows H₂ answers this request and is not a replay. That is
the client's freshness guarantee, matching the server's from step 1. The keys
come from `C`, which never crossed the tailnet, salted with H₁, so only the
party that ran step 1 can open H₂, and only for this attest request. A fresh
`C` per challenge means a key is never reused, so the keystream needs no
separate nonce. Encryption is the extra layer here, not the main protection:
WireGuard and TLS already protect this leg, and the H₂ construction means a
TLS-terminating proxy or a request log on the tailnet side would still never
see `S`.

### 4. Redeem (HTTPS, client to server)

```
R = HMAC(key=S, "bd-mgmt/v1/redeem" ‖ cid ‖ C ‖ IP)
```

The client sends `{cid, R}` to `POST /v2/manage/auth/redeem` within 10 seconds
of step 3, together with the state cookie. The server checks the cookie
against `cid`, that `cid` was attested and is inside its window, and `R` in
constant time against the `IP` it recorded in step 2. It then deletes the
challenge, records the node, and issues a session cookie.

**Why it matters:** this closes the loop. `R` can only be computed by the
party that received `S` over the tailnet in step 3 *and* holds `C` from step 1,
and it arrives over HTTPS with the cookie from step 1. So one party controls
both channels within a few seconds. Neither `S` nor `C` is sent in plain form,
and binding `IP` ties the session to the node that attested.

## Sessions and continuous liveness

One handshake only proves liveness at one moment, so the session depends on
repeating it:

- The server stores `last_attested_at` and the node with each session.
  Middleware rejects a session once `now - last_attested_at` exceeds the TTL
  (default 180 seconds). Enforcement is on the server; it doesn't rely on the
  cookie's own expiry, which the client controls.
- The page reruns the handshake about every 60 seconds. Each successful redeem
  rotates the session token.
- Every renewal reruns every check, so removing the device's tag, removing it
  from the tailnet, or simply leaving the tailnet ends access within one TTL.
- A renewal from a different node starts a new session for that node and
  revokes the old one.

## Where the security actually lives

Each layer is there on purpose, but they don't carry equal weight. If you
change this code, these are the checks that must not weaken:

1. **WireGuard source authentication plus `whois` tag allowlisting.** Together
   they decide who may attest.
2. **The `Origin` check on `/attest`.** It is the only thing that stops a
   malicious website from borrowing an allowed device's network position.
   CORS alone only stops the response being *read*; the request must be
   refused outright.
3. **Single-use, short-lived server state.** Challenges are attested once and
   redeemed once, inside tight windows; sessions expire on the server.
4. **TLS on both channels.**

The H₁ and H₂ constructions, the redundant `status` check, and the binding of
`IP` into `R` add depth. They make the liveness proof explicit and keep
secrets out of tailnet-side logs, but no single one of them is load-bearing.

## Tailscale setup

- **Tag your management devices** (for example `tag:mgmt`) and keep that tag's
  `tagOwners` to yourself, or `autogroup:admin` if you are the only admin.
  Whoever can apply the tag can add a device that signs in.
- **Tagged nodes have key expiry disabled by default.** Turn it back on per
  device in the admin console if you want periodic re-authentication.
- **Add a policy grant** so only the management tag can reach the attest
  service's port. The network then enforces the same rule the app checks.
- **Enable Tailnet Lock** so the coordination server can't add nodes you never
  signed.
- **Issue and renew the certificate** for the attest service's `ts.net` name
  with `tailscale cert`. These certificates last 90 days, so renewal must be
  scheduled.

## Browser notes

- The attest service must use HTTPS. A page loaded over public HTTPS can't
  call `http://100.x.y.z`; browsers block it as mixed content.
- The attest service answers CORS preflights only for the public origin and
  allows only `POST` with `Content-Type: application/json`, which forces a
  preflight. It also answers Private Network Access preflights.
- Some Chromium versions ask the user for permission before a public page may
  contact a private-network address. Depending on how a browser classifies
  tailnet addresses (`100.64.0.0/10`), the first sign-in may show that prompt.
