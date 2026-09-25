# Bad Decisions

**Bad Decisions** is a reusable engine and service for fill-in-the-blank party
card games. It provides a validated pack registry, a stateless REST API, a
browser client, a terminal client, and CardDeck portable-pack archives.

The engine makes no network requests while serving a hand and never changes
packs through the HTTP API. Prompt and response selectors are independent; the
registry is loaded at startup and can be run with multiple workers.

## Install

```bash
python -m pip install bad-decisions
bad-decisions --help
bad-decisions --oneshot
```

The cross-platform client is separate:

```bash
python -m pip install bad-decisions-client
regret health
regret deal
```

The configured hosted browser client is at
[`/bad-decisions/web/`](https://bytes.coffee/bad-decisions/web/).

## Packs

Set `BAD_DECISIONS_PACK_DIR` to an absolute registry directory. It replaces
the bundled registry and is loaded only at startup.

```bash
bad-decisions pack validate example.carddeck
bad-decisions pack init-registry /absolute/pack/registry
bad-decisions pack import example.carddeck /absolute/pack/registry
```

[CardDeck 1](docs/CARDDECK.md) defines the portable format. See
[REMOTE_IMPORTS.md](docs/REMOTE_IMPORTS.md) for the remote catalog and archive
workflow. Its ZIP validation
rejects traversal, symlinks, unexpected members, checksum mismatches,
oversized content, and dangerous compression ratios before any pack is written.

### Public CardDeck catalog and remote imports

The live [public CardDeck catalog](https://bad-decisions.objects.us-west-1.bytes.coffee/packs/index)
lists every intentionally public archive with metadata, licensing/provenance,
SHA-256, and direct download URLs.

The runtime remains immutable: it loads packs at startup and has no endpoint to
upload or alter them. To add a public pack, use the explicit remote-import CLI,
then restart with `BAD_DECISIONS_PACK_DIR` pointing to the registry:

```bash
bad-decisions pack import --remote \
  https://bad-decisions-native.objects.us-west-1.bytes.coffee/packs/coffee.carddeck \
  /absolute/pack/registry

bad-decisions pack import --index \
  https://bad-decisions.objects.us-west-1.bytes.coffee/packs/index \
  /absolute/pack/registry \
  --pack coffee \
  --pack pyx-2-base-game-us
```

Remote imports accept HTTPS only and reject redirects, URL credentials,
oversized responses, malformed catalogs, duplicate selections, and invalid
archives before the normal atomic, non-overwrite import occurs.

### Pretend You're Xyzzy imports

The distribution does not bundle Pretend You're Xyzzy card data. If you have a
lawfully acquired `cah_cards.sql` dump, the separate importer emits one
attributed `.carddeck` archive per active card set and records the supplied
source URL and SHA-256. Those generated packs are CC BY-NC-SA 3.0 and must stay
non-commercial and share-alike.

```bash
PYTHONPATH=src .venv/bin/python scripts/import_pyx.py /path/to/cah_cards.sql ./pyx-carddecks \
  --source-url 'https://raw.githubusercontent.com/ajanata/PretendYoureXyzzy/<commit>/cah_cards.sql' \
  --retrieved 2026-09-17
```

Bundled content retains its own provenance and licensing. The `coffee` pack is
owner-authorized material based on IRC messages, distributed under CC BY-SA
4.0; its raw source-message corpus is not included. The `base` pack is licensed
CC BY-NC-SA 2.0; operators distributing it must honor those terms.

## Licensing

Bad Decisions is free and open-source software licensed under the
[MIT License](LICENSE).

Card packs are separate works and may be distributed under different licenses.
A card pack's own declared license, attribution, provenance, and modification
information governs use of that pack; the Bad Decisions MIT license does not
grant additional rights to third-party content. In particular, BytesAndCoffee
cannot grant commercial rights to third-party card-pack content beyond the
rights provided by that content's owner and license.

The official BytesAndCoffee distribution and hosted service are currently
provided free of charge. BytesAndCoffee does not sell access, offer paid API
access, or monetize the hosted service with advertising. This describes how
BytesAndCoffee operates its official distribution; it is not a restriction on
downstream use of the MIT-licensed software.

In other words: use the engine however the MIT License permits, but check the
license on the cards you put into it. If your Bad Decisions have Consequences,
that is between you and the stew.

## Documentation

- [CardDeck format](docs/CARDDECK.md)
- [Remote imports](docs/REMOTE_IMPORTS.md)
- [Attribution and content rights](docs/ATTRIBUTION.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Release process](docs/RELEASING.md)
- [Changelog](CHANGELOG.md)
- [bad-decisions(1)](man/bad-decisions.1)
- [regret(1)](man/regret.1)
- [Terminal client](client/README.md)

## API

```bash
uvicorn bad_decisions.api:create_app --factory --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/v1/packs
curl http://127.0.0.1:8000/v1/round
curl 'http://127.0.0.1:8000/v1/round?packs=maha'
```

Without a `packs` parameter (or `--packs` in the CLI), rounds draw from every pack
in the loaded registry, so custom `BAD_DECISIONS_PACK_DIR` registries need no `base`.

`/docs` exposes OpenAPI documentation. Errors use a stable JSON envelope and
request responses include a request ID.


## Consequences (optional analytics and feedback)

Consequences is disabled by default. Enable it only with a local, persistent SQLite
path owned by the service user:

    BAD_DECISIONS_CONSEQUENCES_DB=/var/lib/bad-decisions/consequences.sqlite3

The database must not live in a release directory, an object store, or a shared
filesystem. SQLite uses bounded writer waits; durable draws and feedback are
transactional, while request telemetry is best effort. If the store is unavailable,
dealing still succeeds and the response advertises feedback as unavailable.

Consequences records route templates, method, status, duration, optional random
client/session UUIDs, and authoritative drawn-card/provenance records. It does not
record IP addresses, user agents, raw query strings, raw request headers, or
feedback capabilities. Capability tokens are returned only in
`X-Regret-Feedback-Token`, are stored as verifiers, and expire after seven days.

Set `BAD_DECISIONS_CONSEQUENCES_FEEDBACK=0` to retain analytics without voting.
Public combination summaries are off unless
`BAD_DECISIONS_CONSEQUENCES_PUBLIC_STATS=1`. Other controls include bounded
writer timeout, feedback TTL, and retention days; feedback TTL may not outlive
retention.

Private operator commands:

`bad-decisions consequences tui` opens an interactive Textual dashboard with dashboard, combination, prompt, and recent-draw views. Select rows for drill-down; the TUI is included in the core `bad-decisions` package.


    bad-decisions consequences report
    bad-decisions consequences tui
    bad-decisions consequences report /absolute/path/consequences.sqlite3
    bad-decisions consequences rebuild /absolute/path/consequences.sqlite3
    bad-decisions consequences purge /absolute/path/consequences.sqlite3 --retention-days 90

For `report`, the database argument is optional: the CLI first uses
`BAD_DECISIONS_CONSEQUENCES_DB`, then discovers the persistent
`consequences/consequences.sqlite3` beside an installed immutable release.
`rebuild` and `purge` continue to require an explicit path.

The Regret client keeps Consequences disabled until the user explicitly chooses
`regret consequences enjoy`; `regret consequences regret` disables voting and its
pseudonymous telemetry. The preference is stored locally and is never enabled by
a non-interactive invocation.

The Regret client automatically sends a random per-installation UUID when it can
safely persist it in `~/.regret.env`, and a new session UUID per invocation.
Use `regret identity reset` or `regret identity off` for control. After a deal,
`regret feedback enjoy`, `regret feedback regret`, and `regret feedback clear`
operate on its securely cached last eligible draw. Feedback is one mutable vote per
draw; retries do not duplicate it, and the last committed concurrent vote wins.
`regret provenance` reads that local last-draw record and prints the license,
attribution, version, and source metadata for every represented pack without
contacting the API; add `--json` for machine-readable output.

## Linux deployment

Bad Decisions is Linux-native for production deployment:

```bash
bad-decisions setup
sudo NGINX_SITE_CONFIG=/etc/nginx/sites-available/example.com \
  PUBLIC_BASE_URL=https://example.com/bad-decisions \
  ./deploy.sh
```

The deployer creates an unprivileged service account, immutable wheel releases,
a `bad-decisions.service` unit, and optional loopback nginx proxy configuration.
See [DEPLOYMENT.md](docs/DEPLOYMENT.md).

### AWS-native deployment

Bad Decisions has two complete deployment modes:

- **Self-hosted:** the existing systemd/nginx service, SQLite Consequences, and
  optional Garage archive remain supported.
- **AWS-native:** a pip-installed local management client builds the image and
  provisions the entire service without a source checkout.

~~~bash
bad-decisions setup aws --profile decisions \
  --certificate-arn "$ACM_CERTIFICATE_ARN" \
  --domain-name bad-decisions.example.com \
  --hosted-zone-id "$ROUTE53_ZONE_ID"
bad-decisions deploy aws        # builds the image, shows the diff, asks to deploy
bad-decisions status aws
bad-decisions rotate-token aws  # new management token, restarts tasks
bad-decisions pack publish-aws ./my-pack.carddeck
bad-decisions pack seed-aws  # recover a deployment whose initial seeding failed
bad-decisions pack list-aws
bad-decisions consequences report aws
~~~

AWS mode creates minimum-size Fargate Spot tasks behind an API Gateway HTTP
API with an HTTPS custom domain and Route 53 alias (no load balancer), a
private versioned S3 bucket, a CloudFront HTTPS pack distribution, an
event-driven dynamic CardDeck index, DynamoDB-backed Consequences, Secrets
Manager injection, autoscaling, logs, and alarms. Runtime pack JSON, public
archives, and catalog metadata use separate prefixes; CloudFront exposes only
`/packs/*`. The deployment seeds the bundled packs and rolls tasks whenever a
new pack is published, preserving the API's immutable-at-startup registry.

The local client stores deployment coordinates and the management capability
in `~/.bad-decisions.env` with mode `0600`. Pack and deployment mutations use
the caller's temporary AWS credentials over HTTPS; the application API exposes
only a hidden authenticated status read. The cost-conscious default is one
task scaling to two, on-demand DynamoDB, no NAT gateway, and no Container
Insights. `--allow-http` exists only for disposable smoke tests.

See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for architecture, prerequisites,
security boundaries, costs, and commands.

## Development

```bash
.venv/bin/python -m pytest
PYTHONPATH=client/src .venv/bin/python -m pytest client/tests
bash -n deploy.sh
.venv/bin/python -m build .
.venv/bin/python -m build client
```
