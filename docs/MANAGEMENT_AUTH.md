# Management authentication: the tailnet liveness handshake

The management page is served on the public HTTPS site, but you can only sign
in from a device that is on your tailnet, carries an allowed tag, and is
reachable over the tailnet *right now*. A four-message handshake proves that
liveness. It splits across two channels, the public HTTPS site and a small
attest service that listens only on the tailnet. The page repeats the
handshake silently while it stays open, so the session is a short lease that
lapses within one TTL after the device leaves the tailnet or loses its tag.

This is **TailBind v1** in its co-located profile. The public app is the
relying party and the attest service is the TailBind Authority; because both
run on one host in one trust boundary, they share a single SQLite file instead
of talking over a separate control channel. Labels, field sizes, JSON member
names, and the transcript below are TailBind v1's, so a separated Authority
could replace the attest service without changing the browser client.

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
  a session cookie copied off a device. The short lease limits how long a
  copied cookie stays useful.

## Parties and channels

| Name | Where it runs | Reached at |
|---|---|---|
| **Public app** | the normal service, behind nginx | `https://<public site>/…/v2/manage/…` |
| **Attest service** | a separate process bound to the host's tailnet IP, with a `tailscale cert` certificate | `https://<host>.<tailnet>.ts.net:<port>/attest` |
| **Client** | `manage.js` in the browser, using only WebCrypto | both |

The two processes share one SQLite database for challenges and sessions, so
every worker sees the same state. The attest service asks `tailscaled` about
peers through its LocalAPI socket rather than running the `tailscale` command.

## What each message establishes

| Step | Direction | Establishes |
|---|---|---|
| 1 | public app → browser | Server freshness, and binding of the challenge to this browser |
| 2 | browser → attest service | The browser can send on the tailnet now, and holds `C` |
| 3 | attest service → browser | The browser can receive on the tailnet now, from a node that passed authorization |
| 4 | browser → public app | The same browser completed the whole exchange |

## Values and encoding

| Value | Size | Made by | Purpose |
|---|---|---|---|
| `cid` | 16 bytes | server | Opaque challenge ID: tells the server which `C` and `S` to use |
| `C` | 32 bytes | server | Challenge; sent only over HTTPS, never over the tailnet |
| `S` | 32 bytes | server | Per-challenge secret; disclosed only inside H₂ |
| `N` | 32 bytes | client | Client nonce: freshness the client can check for itself |
| `IP` | 16 bytes | server | The client's tailnet address; IPv4 in IPv6-mapped form |

Every value is random, single-use, and fixed-length. Each MAC input and each
authenticated-data string starts with a distinct version label
(`tailbind/v1/…`), so no concatenation can be read two ways, a value from one
step can't stand in for another, and a future protocol version can't be
confused with this one. JSON carries bytes as unpadded, canonical base64url,
and member names are exactly `cid`, `C`, `N`, `H1`, `H2`, and `R`; endpoints
refuse missing, unknown, or differently cased members. (Transcripts using the
earlier `bd-mgmt/v1/…` labels are not accepted.) `HMAC` means
HMAC-SHA256.

## Challenge states

Each challenge row moves through these states:

```
pending ──attest──▶ attested ──redeem──▶ redeemed
   │                    │
   └── any failure ─────┴──▶ burned
```

- Every transition is a single conditional `UPDATE … WHERE cid = ? AND status
  = '<expected>'` that must change exactly one row. That is the concurrency
  protection: if two attest or two redeem requests race, only one can win.
- Expiry is a time comparison, not a stored state. A row past its deadline is
  treated as dead and removed by cleanup.
- Any failed check burns the challenge, but only from the state that request
  expected, so a request that lost a race never burns the winner's newer
  state. The client starts over with a new challenge.
- Authorization (the LocalAPI calls) runs *before* the conditional update and
  outside any transaction. Holding SQLite's write lock across a LocalAPI call
  would block every other writer, and the conditional update already makes
  the transition safe without it.

The challenge row holds `cid`, `C`, `S`, the state cookie's hash, the status,
and the attestation record (`attested_ip`, `attested_node_id`,
`attested_tags`, `attested_at`) plus its deadlines. Sessions live in their own
table because they outlive any one challenge.

## The handshake

```
 Client                         Public app (HTTPS)            Attest service (tailnet)
   |  1. POST challenge  ----------->|                                  |
   |<-------- {cid, C} + state cookie|                                  |
   |                                                                    |
   |  2. POST /attest {cid, N, H1}  ----------------------------------->|
   |                                     checks: Origin, Host, cid, H1, |
   |                                     socket peer, node, tags        |
   |<------------------------------------------------------- {H2}  3.  |
   |                                                                    |
   |  4. POST redeem {cid, R} + state cookie -->|                       |
   |<-------------------- session cookie        |                       |
```

### 1. Challenge (HTTPS, server to client)

`POST /v2/manage/auth/challenge` creates `cid`, `C`, and `S`, stores them as
`pending` with a 30-second deadline for step 2, and returns
`{"cid", "C", "authority", "expires_at"}`: `authority` is the attest
service's base URL and `expires_at` the RFC 3339 end of the window. It refuses
any request whose `Origin` isn't the public site's, so a malicious website
can't start a sign-in in your browser. It also sets a state cookie (`HttpOnly`, `Secure`, `SameSite=Strict`, scoped to the
auth path), whose hash is stored with the challenge.

**Why it matters:** `C` is fresh and unpredictable, so nothing that follows
can have been computed before this moment. That gives the server its proof of
freshness. The state cookie is browser-instance binding for an outstanding
challenge: only the browser that received this challenge can redeem it in
step 4.

### 2. Attest request (tailnet, client to server)

```
H1 = HMAC(key=C, "tailbind/v1/h1" ‖ cid ‖ N)
```

The client picks `N` and sends `{cid, N, H1}` to `<authority>/attest` as JSON;
the response is `{H2}`.
The attest service checks, in this order, and burns the challenge on any
failure that names a real `cid`:

1. **`Origin` is exactly the public site's origin.** Browsers set this header
   and page scripts can't change it.
2. **`Host` is the attest service's own `ts.net` name**, which defeats DNS
   rebinding (the certificate does too).
3. **`cid` is `pending` and inside its deadline.**
4. **`H1` matches**, compared in constant time.
5. **The socket peer address is a Tailscale address.** It comes from the
   connection itself; no forwarded-address header is trusted. The service
   binds the tailnet IP directly with no proxy in front.
6. **Tailscale identifies that address as a node known to this tailnet**, and
   confirms it is not a node shared in from another tailnet.
7. **Tailscale's identity information for that address contains an allowed
   tag** and, when configured, an allowed node ID.

The service then moves the challenge `pending → attested`, recording the
address, node ID, tags, and time. If the update changes no rows, another
request won the race and this one fails.

**Why it matters:** the successful request itself proves the browser can
*send* on the tailnet now. Tailscale's authenticated data plane binds the
connection to its source node, so the socket address can't be forged, while
the node metadata and identity lookups in checks 6 and 7 decide whether that
node is authorized. H₁ proves the sender holds `C`, which it can only have got
from step 1, yet `C` itself never crosses the tailnet. Check 1 is the one that
stops a malicious website: it shares the operator's network position, and
without the `Origin` check it could attest its own challenge from the
operator's browser.

### 3. Attest response (tailnet, server to client)

H₂ uses AES-256-GCM with a per-challenge HKDF-derived key and a fresh 96-bit
IV:

```
K   = HKDF-SHA256(ikm=C, salt=H1, info="tailbind/v1/h2", length=32)
IV  = random(12)
AAD = "tailbind/v1/h2" ‖ cid ‖ N ‖ H1
E   = AES-256-GCM-Encrypt(key=K, iv=IV, plaintext=IP ‖ S, aad=AAD)   (includes the 16-byte tag)
H2  = IV ‖ E                                                          (76 bytes)
```

The client derives the same `K` and decrypts. WebCrypto checks the GCM tag
before releasing any plaintext.

**Why it matters:** receiving this proves the browser can also *receive* on
the tailnet now; a one-way path would fail here. Because the authenticated
data covers the client's own `N`, the client knows H₂ answers this request and
is not a replay. That is the client's freshness guarantee, matching the
server's from step 1. The key comes from `C`, which never crossed the tailnet,
salted with H₁, so only the party that ran step 1 can open H₂, and only for
this attest request. Encryption is the extra layer here, not the main
protection: WireGuard and TLS already protect this leg, but with H₂ even a
TLS-terminating proxy or a request log on the tailnet side would never see
`S`.

### 4. Redeem (HTTPS, client to server)

```
R = HMAC(key=S, "tailbind/v1/redeem" ‖ cid ‖ C ‖ IP)
```

The client sends only `{cid, R}` to `POST /v2/manage/auth/redeem` (which, like
the challenge endpoint, refuses any other `Origin`), within 10
seconds of step 3, together with the state cookie. It never sends `IP`; the
server uses the address the attest service recorded. The server checks that:

1. `cid` exists and is `attested`, not merely `pending`;
2. the challenge is inside its deadline;
3. the state cookie's hash matches the one stored in step 1;
4. `R` equals `HMAC(S, "tailbind/v1/redeem" ‖ cid ‖ C ‖ attested_ip)`, compared
   in constant time.

It then moves the challenge `attested → redeemed`, requiring exactly one
changed row, and issues a session.

**Why it matters:** `R` proves possession of `S`. `S` was disclosed only inside
H₂, and H₂ could only be opened with key material derived from `C`. So a
successful redeem proves that the browser which received the HTTPS challenge
also received the successful tailnet attestation response, within a few
seconds. Binding the recorded `IP` ties the session to the node that attested.

## Sessions: a lease renewed by liveness

One handshake only proves liveness at one moment, so the session is a lease
that only a fresh handshake can extend:

```
        successful handshake
                │
                ▼
      ┌───────────────────┐
      │ lease: 180 seconds │
      └───────────────────┘
          ▲            │
  renewal │            │ no successful
          │            │ re-attestation
          │            ▼
    new handshake   lease expires
```

- The sessions table stores each session token's hash, the node, and
  `last_attested_at`. Middleware rejects a session once `now -
  last_attested_at` exceeds the TTL (default 180 seconds). Enforcement is on
  the server; it doesn't rely on the cookie's own expiry, which the client
  controls.
- The page reruns the handshake about every 60 seconds. Each successful redeem
  rotates the session token.
- Every renewal reruns every check, so removing the device's tag, removing it
  from the tailnet, or simply leaving the tailnet ends access within one TTL.
  Nothing has to happen when that occurs: the lease just stops being extended.
- A renewal from a different node starts a new session for that node and
  revokes the old one.

## What each attacker runs into

| Attacker | Stopped by |
|---|---|
| Steals `{cid, C}` | Can't attest without being an authorized tailnet node |
| On the tailnet, but not authorized | Node identity and tag checks |
| Malicious website on an authorized device | `Origin` check on `/attest` |
| Records H₁ | Can't open H₂ without `C` |
| Records H₂ | A new `C` and `N` make it useless for any other handshake |
| Records `R` | `cid` is single-use |
| Obtains `S` alone | `R` also binds `cid`, `C`, and the attested `IP` of one live challenge |
| Races a second attest or redeem | Conditional state transition: exactly one wins |
| Leaves the tailnet with a session | Lease expires within one TTL |

## Where the security actually lives

Each layer is there on purpose, but they don't carry equal weight. If you
change this code, these are the checks that must not weaken:

1. **Tailscale source authentication plus tag authorization.** Together they
   decide who may attest.
2. **The `Origin` check on `/attest`.** It is the only thing that stops a
   malicious website from borrowing an allowed device's network position.
   CORS alone only stops the response being *read*; the request must be
   refused outright.
3. **Single-use, short-lived server state.** Conditional state transitions,
   tight deadlines, and server-enforced session leases.
4. **TLS on both channels.**

H₁, the encryption of H₂, and the binding of `IP` into `R` add depth. They make
the liveness proof explicit and keep secrets out of tailnet-side logs, but no
single one of them is load-bearing.

## Installation

The attest service needs AES-GCM, which Python's standard library lacks, so it
uses the optional `management` extra (`pip install 'bad-decisions[management]'`).
The public app needs only the standard library for its half. On a deployed
host:

1. Do the Tailscale setup below, then run, as root from the checkout:

   ```bash
   sudo MANAGEMENT_PUBLIC_ORIGIN=https://games.example \
        MANAGEMENT_TAGS=tag:mgmt \
        ./deploy.sh install-management
   ```

   Optional: `MANAGEMENT_NODES` (stable node IDs that must also match),
   `MANAGEMENT_ATTEST_PORT` (default 8443), `MANAGEMENT_ATTEST_NAME` and
   `MANAGEMENT_ATTEST_BIND` (default: this node's MagicDNS name and tailnet
   IPv4 address, from `tailscale status`), and the usual `APP_ROOT`,
   `SERVICE_NAME`, `ENV_FILE`, `BIND_HOST`, and `PORT`.

2. The first run creates `APP_ROOT/management`. If the serving release lacks the
   extra, it stops and asks you to redeploy: from then on, `deploy.sh` and
   `bad-decisions deploy local` install `requirements-management.lock` instead
   of `requirements.lock` (or pass `deploy local --with-management`). Redeploy,
   then run step 1 again.

3. It then writes the `BAD_DECISIONS_MANAGEMENT_*` settings into the service's
   env file; installs `<service>-management-attest.service`, bound to the
   tailnet address with TLS and `PartOf` the API so deploys and rollbacks
   restart it; issues the certificate with a weekly `tailscale cert` renewal
   timer; and restarts the API. It finishes by checking that the attest
   service allows a preflight from the public origin and refuses one from any
   other. On failure it restores the previous env file and units.

Open `<public site><root path>/manage` from a tagged device. Rolling back to a
release without the extra stops the attest service until you redeploy.

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
- **Check LocalAPI access.** The attest service's user must be allowed to
  query `tailscaled`'s socket for peer identity.

## Browser notes

- The attest service must use HTTPS. A page loaded over public HTTPS can't
  call `http://100.x.y.z`; browsers block it as mixed content.
- The attest service answers CORS preflights only for the public origin and
  allows only `POST` with `Content-Type: application/json`, which forces a
  preflight. It also answers Private Network Access preflights.
- Some Chromium versions ask the user for permission before a public page may
  contact a private-network address. Depending on how a browser classifies
  tailnet addresses (`100.64.0.0/10`), the first sign-in may show that prompt.
