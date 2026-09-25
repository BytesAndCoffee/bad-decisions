# Portable deployment

`deploy.sh` deploys this project to a Linux host using systemd and nginx. It is
deliberately parameterized: it does not name a provider, host, domain, user
home, or pre-existing nginx site.

Install Python 3.12 with venv support, nginx, and curl. Build and test the
project first. The script must run as root because it creates a service account,
release directory, systemd unit, environment file, and nginx configuration.

## Configure the target server block

Select the existing nginx server configuration that should expose the API. Add
this exact marker inside the appropriate `server` block, then validate nginx:

```nginx
# bad-decisions-location
```

The marker lets the deployer insert one managed `include` without replacing or
guessing at the rest of your nginx configuration.

## Deploy

```bash
sudo NGINX_SITE_CONFIG=/etc/nginx/sites-available/example.com \
  PUBLIC_BASE_URL=https://example.com/bad-decisions \
  ./deploy.sh
```

The supplied public URL is used only for the post-deployment HTTPS health
check. To deploy without nginx, use `CONFIGURE_NGINX=0`; no public check is
then performed.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `bad-decisions` | Release directory and systemd service name. |
| `APP_ROOT` | `/opt/$APP_NAME` | Immutable release root. |
| `SERVICE_USER` | `$APP_NAME` | Locked system account that runs the API. |
| `ROOT_PATH` | `/bad-decisions` | Public nginx route and FastAPI root path. |
| `BIND_HOST` | `127.0.0.1` | Uvicorn bind address. Keep loopback-only behind nginx. |
| `PORT` | `8000` | Uvicorn port. |
| `WORKERS` | `2` | Uvicorn worker count. |
| `NGINX_SITE_CONFIG` | required when nginx is enabled | Existing server configuration containing the marker. |
| `PUBLIC_BASE_URL` | required when nginx is enabled | HTTPS URL corresponding to `ROOT_PATH`. |
| `CONFIGURE_NGINX` | `1` | Set to `0` to install only the systemd service. |

For an update, rerun the same command. The script builds a wheel, stages an
immutable release, atomically switches `current`, restarts the service, and
retains the prior healthy release for rollback. Inspect service logs with
`journalctl -u <APP_NAME> -n 100 --no-pager`.

## Rollback

A failed deploy rolls itself back automatically (config backups, then the
previous release). To go back on demand after a deploy that passed its checks:

```bash
sudo ./deploy.sh rollback                    # the last good release
sudo ./deploy.sh rollback 20260102T000000Z   # a specific release under $APP_ROOT/releases
```

`$APP_ROOT/good-releases` is the watermark: release IDs, oldest first, that
passed every `deploy.sh` health check (the last 20; a first deploy after
upgrading seeds it with the release that was serving). The default target is the
newest listed release that is not current, so a release that failed its deploy
is never chosen. The file is kept sorted by release ID (UTC timestamps). A
successful rollback drops every listed release newer than the target, so running
it again steps further back instead of rolling forward. Hosts without the file
fall back to the newest older release, with a warning that it is not verified.

Rollback atomically repoints `current`, restarts the service, and waits for
`/healthz`. If the target fails that check, the release that was serving is
restored and the command exits non-zero. Only the release changes: the env
file, unit, and nginx files are not restored (backups are in
`$APP_ROOT/backups/<release>`). Rerunning `deploy.sh` builds a new release from
the source tree, so fix or revert the code first.


## AWS-native deployment

This is a complete alternative to the self-hosted deployment, not an extension
of it. Install the `bad-decisions` package, AWS CLI v2, Docker Engine, and
Node/npm on the management workstation. Authenticate with temporary AWS
credentials, then run:

~~~bash
bad-decisions setup aws --profile decisions --region ca-west-1 \
  --certificate-arn arn:aws:acm:ca-west-1:ACCOUNT:certificate/CERTIFICATE \
  --domain-name bad-decisions.example.com \
  --hosted-zone-id ZONE_ID \
  --hosted-zone-name example.com
bad-decisions deploy aws
bad-decisions status aws
~~~

`setup aws` is one-time account preparation and is safe to repeat. It verifies
the AWS identity, creates or reuses an immutable ECR repository, creates the
management token in Secrets Manager only if the secret does not exist,
bootstraps CDK, and merges its settings into `~/.bad-decisions.env` (mode
`0600`) without discarding deploy outputs or saved domain settings. It never
rotates an existing token. Invalid HTTPS/DNS arguments are rejected before AWS
is changed.

`deploy aws` runs for every release. It builds and pushes an image of the exact
installed package version (so the image always matches the CDK stack that the
same package defines), shows `cdk diff`, asks for confirmation, then deploys.
Pass `--yes` to deploy unattended (required without a terminal), `--image` to
redeploy an already-pushed image, or `--source` to test an unreleased checkout.
Domain flags given to either command are saved for later runs.

`rotate-token aws` replaces the management token in Secrets Manager, saves it
locally, and forces a new ECS deployment so every task picks it up together.
The old token keeps working until the old tasks stop.

`deploy aws` provisions the packaged CDK stack:

- isolated ECS/Fargate API tasks behind an ALB;
- ACM HTTPS plus a Route 53 alias whose hostname matches the certificate;
- a private, encrypted, versioned S3 pack/archive bucket;
- a CloudFront distribution exposing only `/packs/*` over HTTPS;
- a Lambda-generated `/packs/index`, rebuilt from trusted `catalog/*.json`
  metadata whenever a pack is published;
- DynamoDB Consequences storage with on-demand billing, TTL, encryption, and
  point-in-time recovery;
- Secrets Manager injection, retained logs, health alarms, autoscaling, S3 and
  DynamoDB gateway endpoints, and single-AZ ECR/Logs/Secrets endpoints.

The S3 layout deliberately separates concerns:

- `packs/<id>.carddeck`: immutable public archives;
- `runtime-packs/<id>.json`: private validated API registry objects;
- `catalog/<id>.json`: private provenance and index metadata;
- `packs/index`: public generated catalog.

Deployment seeds all bundled packs. Publish another validated archive with
`bad-decisions pack publish-aws FILE.carddeck`; it refuses overwrite, writes
catalog metadata last, rolls back exact object versions on failure, and forces
an ECS deployment so all workers load the same immutable registry. Inspect the
catalog with `bad-decisions pack list-aws`. Owner analytics are available with
`bad-decisions consequences report aws`.

The public API and archive paths use HTTPS. Local mutations use the AWS SDK and
the caller's temporary IAM credentials over AWS HTTPS endpoints. The hidden
read-only status endpoint uses the protected local management capability;
secrets are never printed. `--allow-http` is only for disposable smoke tests
and bypasses the production certificate/domain requirement.

The low-cost defaults are one task scaling to two, DynamoDB on-demand, no NAT
gateway, no Container Insights, and one-AZ interface endpoints. ALB, Fargate,
CloudFront traffic, and interface endpoints still incur charges; the
single-AZ endpoints also reduce endpoint resilience and can add cross-AZ
transfer charges. S3, DynamoDB, and CloudFront replace Garage only in AWS mode.
The original systemd/nginx, SQLite, and optional Garage deployment remain the
self-hosted mode.
