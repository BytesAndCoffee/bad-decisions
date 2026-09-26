# Peer Pressure multiplayer

Peer Pressure adds ephemeral, server-authoritative multiplayer to Bad
Decisions. Start or rejoin a room from the terminal client:

```bash
regret together ohno --name Michael
```

The first participant opens the room and hosts it: once at least three people
are present, the host starts the game and becomes the first **Responsible
Adult**. If the host leaves the lobby, the next connected player in seat order
hosts instead.
Everyone else submits a private response to the current question. The
Responsible Adult sees the resulting decisions in randomized order, without
submitter identities, and chooses the consequence. Scores are room-local and
the role rotates by seating order.

Players who stop heartbeating are marked away after the disconnect timeout
(default 30 seconds), and a table never waits on them:

- If the Responsible Adult leaves or goes away, the role passes to the next
  connected player in seat order, in any phase. If that player already
  submitted this round, their decision returns to their hand so they never judge
  their own response. If that leaves nothing to judge, the round goes back to
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
round-scoped opaque submission IDs and response text, never the submitting
player. The winner is revealed only after the Responsible Adult commits a
selection.

Room sessions use an opaque player ID and an unguessable bearer token. Servers
store only SHA-256 token verifiers. Regret saves the token in
`~/.regret-peer-pressure.json` with mode `0600`; it is independent of the
optional Consequences analytics identity. Raw tokens and private hands are not
written to normal request logs.

Every mutation includes a UUID request ID and expected room revision. SQLite
`BEGIN IMMEDIATE` transactions serialize writers. Repeated request IDs return
their original ACK, while stale revisions return a resynchronization NACK.
Regret performs full state synchronization rather than replaying missed events.

## Synchronization

The application protocol uses `SYNACK`, `ACK`, `HEARTBEAT`, and `NACK`
messages. These names describe JSON messages, not raw TCP behavior. Regret runs
a background heartbeat while the interactive command is open. A revision
mismatch causes a full synchronization with the authoritative projection.

The versioned HTTP surface is under `/v1/peer-pressure/rooms`. It provides room
creation/join, synchronization, heartbeat, leave, start, submit, judge,
advance, state, and owner-only deletion operations. OpenAPI at `/docs` is the
authoritative transport reference.

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

Expired and explicitly ended room databases are deleted. Room databases are
gameplay state, not analytics or permanent history. Consequences failures do
not participate in or block room transactions.

Because SQLite files are local, all workers for a given deployment must share
the configured room directory. Horizontal multi-host deployments need sticky
room routing plus shared locking semantics, or a future distributed Peer
Pressure store; do not place these databases on an object store.
