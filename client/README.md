# Bad Decisions terminal client

`bad-decisions-client` is a dependency-free terminal client for a Bad
Decisions service. It contains no card corpus and runs on Linux, macOS, and
Windows.

```bash
python -m pip install bad-decisions-client
regret health
regret deal
regret deal --packs base,maha
regret provenance
regret provenance --json
regret --list-packs
```

Join an ephemeral multiplayer room with Peer Pressure:

```bash
regret together ohno --name Michael
```

The first participant is the initial Responsible Adult. Regret securely caches
the room-local reconnect capability, maintains heartbeats while connected, and
automatically resynchronizes stale state. Peer Pressure identity is separate
from Consequences analytics identity. See the
[multiplayer documentation](../docs/PEER_PRESSURE.md).

Consequences is opt-in and disabled until you choose:

```bash
regret consequences status
regret consequences enjoy
regret consequences regret
```

The first interactive deal prompts once when no preference is stored; non-interactive
runs remain disabled. When enabled, voting and its pseudonymous telemetry are
available. The client also performs a cached, best-effort daily check against the configured API version.

`regret provenance` prints the licenses, attribution, versions, and source
records for every pack represented in the last locally saved draw. It does not
make a network request. A successful draw with provenance replaces the saved
record; feedback capability details are retained only when that same draw is
eligible for voting.

The default endpoint is `https://bytes.coffee/bad-decisions`. Use `--api-url`
for another compatible deployment. Connection and API failures return a
non-zero exit status.

## Changelog

See [CHANGELOG.md](https://github.com/BytesAndCoffee/bad-decisions/blob/main/client/CHANGELOG.md) for client release notes.

## Release

Build and validate before uploading a new immutable PyPI version:

```bash
cd client
python -m build
twine check dist/*
```
