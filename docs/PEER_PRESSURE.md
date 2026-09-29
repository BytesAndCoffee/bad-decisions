# Peer Pressure multiplayer

Peer Pressure adds ephemeral, server-authoritative multiplayer to Bad
Decisions. Start or rejoin a room from the terminal client:

```bash
regret together ohno --name Michael
```

For a full-screen Textual table, install the optional client extra and add
`--tui`:

```bash
pipx install 'bad-decisions-client[tui]'
regret together ohno --name Michael --tui
```

The TUI follows the same authenticated revision protocol as the line-oriented
client. It keeps the prompt, private hand, anonymous judging choices, players,
scores, presence, Responsible Adult, and result visible together. Use Enter to
select the highlighted choice, the action buttons to move the round forward,
`r` to synchronize immediately, and `q` to leave. If the table cannot be
reached, `q` still closes the TUI and your seat expires with the room. The
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
written to normal request logs.

Every mutation includes a request ID (`req_` followed by 32 lowercase
hexadecimal digits, unique per player) and the room revision it expects. SQLite
`BEGIN IMMEDIATE` transactions serialize writers. Repeating a request ID
returns the original result, so retries after a lost response are safe. A stale
revision is refused with HTTP 409 `stale_revision`, whose error details carry
`resync: true` and the current `revision`. Regret performs full state
synchronization rather than replaying missed events.

## Synchronization

Each room has one revision number that only increases. Clients never merge
changes: they send the revision they last saw, and on any mismatch they fetch
the full state again rather than replaying missed events. Joining or syncing
returns `{room, revision, state}` (joining also returns the `player_id` and
`session_token`); a change returns `{request_id, revision}`; a heartbeat
returns `{revision, resync}` and also keeps the player marked present. Regret
runs the heartbeat in the background while `regret together` is open and
resynchronizes whenever `resync` is true or a change is refused as
`stale_revision`. Refusals use the API's standard error envelope, not a
separate message format. Server-sent events remain a future option and will use
the revision as the event ID.

The versioned HTTP surface is under `/v2/peer-pressure/rooms`. A bearer
session token alone identifies its player. Joining with the saved token
rejoins that player; mutations carry a request ID and expected revision.
Heartbeats return the authoritative revision and a `resync` signal. The
surface provides room creation/join, synchronization, heartbeat, leave,
start, submit, judge, advance, end, and state operations. OpenAPI at `/docs`
is the authoritative transport reference. Errors use the same stable JSON
envelope as the rest of the API.

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
