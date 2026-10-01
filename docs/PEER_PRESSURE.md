# Peer Pressure multiplayer

Peer Pressure adds ephemeral, server-authoritative multiplayer to Bad
Decisions, in the browser or the terminal.

The hosted browser table is available at
[bytes.coffee/bad-decisions/peerpressure](https://bytes.coffee/bad-decisions/peerpressure).
Enter a room code and display name, then share the room link with at least two
friends. It uses the same server-authoritative game and revision protocol as
Regret, while adapting the prompt, private hand, players, anonymous judging,
and table controls to the browser. The room creator chooses its packs before
joining. The hosted browser defaults new rooms to the regular base pack, keeps
the owner-authorized custom packs immediately visible, and offers catalog
packs in a multi-select dialog. That selection belongs to the room and cannot
be changed by later joiners.

In the terminal, pick a room ID, share it with the other players, and run the
same command on every computer:

```bash
regret together ohno --name Michael
```

The first person to use a new room ID creates the room. Later players join it;
returning players rejoin their existing seats while the room is alive. Add
`--tui` for the full-screen table:

```bash
regret together ohno --name Michael --tui
```

No account or API key is required for the free BytesAndCoffee-hosted service.

## Install on macOS or Linux

Homebrew is the recommended installation and includes the full-screen TUI:

```bash
brew install bytesandcoffee/tap/regret
regret doctor
regret together ohno --name Michael --tui
```

Or install the TUI-enabled client with Python 3.10+ and pipx:

```bash
pipx install 'bad-decisions-client[tui]'
regret doctor
regret together ohno --name Michael --tui
```

Omit `[tui]` and `--tui` for the dependency-free, line-oriented interface.

## Install on Windows

Install Python 3.10 or newer, then install pipx and Regret from PowerShell:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

Restart PowerShell so the updated `PATH` takes effect, then run:

```powershell
pipx install "bad-decisions-client[tui]"
regret doctor
regret together ohno --name Michael --tui
```

A self-contained `regret.exe` is also attached to each GitHub release. A
WinGet installation is coming soon; until its community listing is approved,
pipx is the supported Windows package-manager path.

## At the table

The TUI follows the same authenticated revision protocol as the line-oriented
client. It keeps the prompt, private hand, anonymous judging choices, players,
scores, presence, Responsible Adult, and result visible together. Use Enter to
select the highlighted choice, the action buttons to move the round forward,
`r` to synchronize immediately, and `q` to leave. If the table cannot be
reached, `q` still closes the TUI and your seat expires with the room. The
room's host can end the room for everyone: press `e` (or **End room**) twice
in the TUI, or answer `e` and then `y` at any line-mode prompt. Leaving never
ends a room; an abandoned room expires six hours (by default) after its last change. The
default interface still has no third-party dependencies; Homebrew installs the
complete client, including the TUI.

The first participant opens the room and hosts it: once at least three people
are present, the host starts the game and becomes the first **Responsible
Adult**. If the host leaves the lobby, the next connected player in seat order
hosts instead.
Everyone else submits a private answer to the current prompt. The
Responsible Adult sees the resulting decisions in randomized order, without
submitter identities, and chooses the consequence. Scores are room-local and
the role rotates by seating order.

Players who stop heartbeating are marked away after the disconnect timeout
(default 30 seconds), and a table never waits on them:

- If the Responsible Adult leaves or goes away, the role passes to the next
  connected player in seat order, in any phase. If that player already
  submitted this round, their decision returns to their hand so they never judge
  their own answer. If that leaves nothing to judge, the round goes back to
  collecting decisions.
- Judging begins once every connected player has submitted.
- A returning player rejoins with their hand and score; a returning former
  Responsible Adult does not take the role back.

Peer Pressure uses Bad Decisions-native language and presentation. It does not
use third-party game logos, trade dress, or role names.

## State and privacy model

Each room is a separate short-lived SQLite database, built privately and linked
into place so a half-created room is never visible. Cards are streamed: a room
records only the cards it has drawn, and each draw comes straight from the
loaded packs, so opening a room costs the same however many packs are
installed, and no card repeats within a room. The server owns hands,
rounds, submissions, scores, role rotation, and the monotonically increasing
room revision. A client receives only its own hand. During judgment it receives
round-scoped opaque submission IDs and answer text, never the submitting
player. The winner is revealed only after the Responsible Adult commits a
selection.

Room sessions use an opaque player ID and an unguessable bearer token. Servers
store only SHA-256 token verifiers. Regret saves the token in
`~/.regret-peer-pressure.json` with mode `0600`; it is independent of the
optional Consequences analytics identity. Raw tokens and private hands are not
written to normal request logs. The browser table keeps its room session in
that browser's local storage so it can reconnect after a refresh; leaving the
room removes the saved browser session.

Every mutation includes a request ID (`req_` followed by 32 lowercase
hexadecimal digits, unique per player) and the room revision it expects. SQLite
`BEGIN IMMEDIATE` transactions serialize writers. Repeating a request ID
returns the original result, so retries after a lost response are safe. A stale
revision is refused with HTTP 409 `stale_revision`, whose error details carry
`resync: true` and the current `revision`. Regret performs full state
synchronization before deciding whether a refused action is still valid.

## Synchronization

Each room has one revision number that only increases. After joining, clients
open the authenticated `GET .../events` Server-Sent Events stream. Every
`state` event has the revision as its event ID and carries that player's full,
private projection; clients replace their local projection rather than merging
patches. The stream sends comment keepalives and reconnects with bounded
backoff. Regret implements the stream with Python's standard library, so the
line client remains dependency-free; the browser uses an authenticated
streaming `fetch` because the native `EventSource` API cannot attach the bearer
header.

Joining or explicitly syncing returns `{room, revision, state}` (joining also
returns the `player_id` and `session_token`); a change returns
`{request_id, revision}`. Heartbeats remain separate and return
`{revision, resync}` because they are presence leases, not the state-delivery
transport. A stale mutation still triggers one authoritative synchronization
before Regret determines whether it is safe to retry. Refusals use the API's
standard error envelope; an error after an SSE response has begun is delivered
as an `error` event and closes that stream.

The versioned HTTP surface is under `/v2/peer-pressure/rooms`. A bearer
session token alone identifies its player. Joining with the saved token
rejoins that player; mutations carry a request ID and expected revision.
Heartbeats return the authoritative revision and a `resync` signal; the SSE
stream is the normal state-delivery path. The surface provides room
creation/join, events, synchronization, heartbeat, leave,
start, submit, judge, advance, end, and state operations. OpenAPI at `/docs`
is the authoritative transport reference. Errors use the same stable JSON
envelope as the rest of the API.

The join request accepts an optional `packs` array when `create` is true. If
the room does not exist, those IDs become its immutable prompt and answer
pools; omitting `packs` preserves the API-wide rule and selects every pack in
the loaded registry. Once the room exists, later join requests cannot change
its deck. Every room projection reports the authoritative selection as
`room.packs`. The hosted browser deliberately sends an explicit selection and
defaults that selection to the hosted registry's regular non-custom pack.

## Configuration

`BAD_DECISIONS_PEER_PRESSURE_DIR` selects the absolute room-database directory.
It defaults to the platform temporary directory under
`bad-decisions-peer-pressure`. Production operators should use a private,
service-owned runtime directory when rooms must survive ordinary process
restarts.

Additional controls:

- `BAD_DECISIONS_PEER_PRESSURE_ROOM_TTL_SECONDS` (default: 21600)
- `BAD_DECISIONS_PEER_PRESSURE_HAND_SIZE` (default: 10)
- `BAD_DECISIONS_PEER_PRESSURE_MINIMUM_PLAYERS` (default: 3)
- `BAD_DECISIONS_PEER_PRESSURE_DISCONNECT_TIMEOUT_SECONDS` (default: 30)
- `BAD_DECISIONS_PEER_PRESSURE_MAX_ROOMS` (default: 200)
- `BAD_DECISIONS_PEER_PRESSURE_MAX_PLAYERS` (default: 12)
- `BAD_DECISIONS_PEER_PRESSURE_MIN_FREE_MB` (default: 256)
- `BAD_DECISIONS_RATE_LIMIT_PER_MINUTE` (default: 30 per client, per worker)

New room creation is refused with 503 when the room or free-space cap is
reached. A full room returns 409; rate-limited creation, joining, and feedback
return 429 with `Retry-After`.

Expired and explicitly ended room databases are deleted. Room databases are
gameplay state, not analytics or permanent history. Consequences failures do
not participate in or block room transactions.

Because SQLite files are local, all workers for a given deployment must share
the configured room directory. Horizontal multi-host deployments need sticky
room routing plus shared locking semantics, or a future distributed Peer
Pressure store; do not place these databases on an object store.
