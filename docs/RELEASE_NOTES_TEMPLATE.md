# Bad Decisions X.Y.Z — short human title

## Why care?

One paragraph for people who do not follow the commit log.

## Highlights

- User-visible change.
- Regret or Peer Pressure change.
- Operator or CardDeck change.

## Upgrade safety

State whether the API, CardDeck format, configuration, or stored data changes.
Say “no migration required” explicitly when that is true.

## Install or upgrade

```bash
brew upgrade bytesandcoffee/tap/regret
pipx upgrade bad-decisions-client
pipx upgrade bad-decisions
```

Self-hosted operators should follow the documented rootless or privileged
deployment path. Link to [Deployment](DEPLOYMENT.md),
[migration notes](MIGRATING-2.0.md) when applicable, and the full
[changelog](../CHANGELOG.md).
