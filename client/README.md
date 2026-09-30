# Regret — the Bad Decisions terminal client

Regret is a dependency-free terminal client for Bad Decisions services. Its
optional full-screen Peer Pressure interface uses Textual. The client contains
no card corpus, runs on Linux, macOS, and Windows, and uses the free
BytesAndCoffee-hosted service by default.

```console
$ regret deal --packs coffee
I started by talking about workplace accommodations. Somehow the channel is now discussing one hell of a robustly deployed pastebin.
```

## Install and try it

Homebrew is the recommended macOS and Linux path and installs `man regret`:

```bash
brew install bytesandcoffee/tap/regret
regret health
regret deal
```

Cross-platform pipx alternative:

```bash
pipx install bad-decisions-client
regret health
regret deal
```

Plain pip also works: `python -m pip install bad-decisions-client`.
`regret doctor` checks the installation, manual-page discovery, configuration,
and service compatibility without changing anything.

Native Windows ZIPs are attached to each GitHub release. They contain a
self-contained `regret.exe`, including the Peer Pressure TUI, and do not
require Python. WinGet and Chocolatey metadata are built from the same checked
release artifact; public package-manager listings are planned.

## Use

```bash
regret --list-packs
regret deal --packs base,maha
regret provenance
regret provenance --json
regret doctor
```

Join an ephemeral multiplayer room with Peer Pressure:

```bash
regret together ohno --name Michael
```

No installation is required for the hosted
[browser table](https://bytes.coffee/bad-decisions/peerpressure). It uses the
same rooms and protocol as Regret; the room creator chooses the deck before
joining, and later participants inherit that selection.

For the full-screen table, install the optional interface and add `--tui`:

```bash
pipx install 'bad-decisions-client[tui]'
regret together ohno --name Michael --tui
```

The TUI shows the table, scores, Responsible Adult, prompt, private hand,
anonymous decisions, and round result in one continuously synchronized view.
The ordinary line-oriented interface remains the dependency-free default.
Homebrew installs the complete client, including the TUI.

The default endpoint is `https://bytes.coffee/bad-decisions`. Use `--api-url`
for another compatible deployment. Connection and API
failures return a non-zero status. See the [60-second guide](../docs/GETTING_STARTED.md)
and [multiplayer documentation](../docs/PEER_PRESSURE.md).

## Consequences and local state

Consequences is opt-in:

```bash
regret consequences status
regret consequences enjoy
regret consequences regret
```

The first interactive deal asks once when no preference exists; non-interactive
runs remain disabled. `regret provenance` reads the protected last-draw record
without contacting the API. Feedback is one mutable vote per eligible draw.
Use `regret identity reset` or `regret identity off` to control the optional
pseudonymous analytics identity. See [Consequences](../docs/CONSEQUENCES.md).

## Manual page

pipx links the page into `~/.local/share/man`. A pyenv/asdf shim can hide a page
installed by pip; `regret doctor` prints the correct `MANPATH` fix. The generic
form is:

```bash
export MANPATH=":$(python3 -c 'import sys; print(sys.prefix)')/share/man"
```

## Project links

- [Bad Decisions repository](https://github.com/BytesAndCoffee/bad-decisions)
- [Hosted browser client](https://bytes.coffee/bad-decisions/web/)
- [Hosted Peer Pressure table](https://bytes.coffee/bad-decisions/peerpressure)
- [OpenAPI](https://bytes.coffee/bad-decisions/docs/)
- [Client changelog](https://github.com/BytesAndCoffee/bad-decisions/blob/main/client/CHANGELOG.md)
- [Issue tracker](https://github.com/BytesAndCoffee/bad-decisions/issues)
