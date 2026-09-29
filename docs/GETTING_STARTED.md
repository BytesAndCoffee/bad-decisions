# Getting started in 60 seconds

Regret is the dependency-free terminal client for Bad Decisions. By default it
talks to the free hosted service at `https://bytes.coffee/bad-decisions`; no
account or API key is required.

## Install

Homebrew is the recommended macOS and Linux path:

```bash
brew install bytesandcoffee/tap/regret
regret health
regret deal
```

On any supported Python 3.10+ platform, use pipx:

```bash
pipx install bad-decisions-client
regret health
regret deal
```

`regret doctor` checks the installation, manual page, configuration, and
service compatibility without changing anything.

pipx links the manual page into `~/.local/share/man`. If a version manager such
as pyenv hides it behind shims, retain the system manual path while adding that
interpreter's pages:

```bash
export MANPATH=":$(pyenv prefix)/share/man"
```

## Choose packs

```bash
regret --list-packs
regret deal --packs coffee
regret deal --prompt-packs maha --answer-packs coffee,maha
regret provenance
```

`regret provenance` reports the source, attribution, and license of the last
draw from its protected local record; it does not make a network request.

## Play together

Pick a room ID and share it with the other players:

```bash
regret together ohno --name Michael
```

Prefer a full-screen table? Install `pipx install
'bad-decisions-client[tui]'`, then run `regret together ohno --name Michael
--tui`. Homebrew already includes the TUI; the standard Python package remains
dependency-free unless the optional extra is selected.

Peer Pressure rooms are ephemeral. Each player receives a room-local bearer
session, and no persistent account is created. See [Peer Pressure](PEER_PRESSURE.md).

## Configuration and privacy

- `--api-url URL` selects another compatible service.
- `~/.regret.env` stores preferences and the optional pseudonymous Consequences
  identity with user-only permissions.
- `~/.regret-last-round.json` supports offline provenance and feedback commands.
- `~/.regret-peer-pressure.json` stores room-local reconnect capabilities.
- `regret consequences regret` keeps analytics and voting disabled.
- `regret identity off` omits the optional analytics identity.

Read [Consequences](CONSEQUENCES.md) for the exact data boundary.

## Next steps

- [Use the browser client](https://bytes.coffee/bad-decisions/web/)
- [Build a client from OpenAPI](https://bytes.coffee/bad-decisions/docs/)
- [Make a CardDeck](../examples/minimal-pack/)
- [Self-host Bad Decisions](EASY_DEPLOY.md)
