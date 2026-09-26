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

## Rootless application updates

After the first privileged deployment, root can install a narrow release
activator and grant one existing account permission to stage wheels:

```bash
sudo DEPLOY_USER="$USER" PORT=8000 ./deploy.sh bootstrap-rootless
```

Log out and back in once so the new deployment-group membership applies. Future
application-only updates need no sudo:

```bash
.venv/bin/python -m build .
bad-decisions deploy local
```

`deploy local` accepts only the wheel for its own version, records its SHA-256,
and atomically places a request in the activator inbox. The root-owned systemd
helper validates the fixed request schema and artifact, builds the virtual
environment as the unprivileged service account, freezes the immutable release,
switches `current`, restarts the service, checks `/healthz`, and restores the
previous release on failure. The staging account cannot edit nginx, service
units, environment/secrets, the activator, or existing releases. Changes to
those resources still use the privileged `deploy.sh` path.

Optional non-default values used by the original install must also be passed to
`bootstrap-rootless` (`APP_ROOT`, `SERVICE_NAME`, `SERVICE_USER`, `BIND_HOST`,
`PORT`, and `PYTHON`). Use `--app-root` with `deploy local` if it is not
`/opt/bad-decisions`.

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
credentials from `aws login --profile decisions` as an IAM user (never the
root user and never long-term access keys), then run:

~~~bash
bad-decisions setup aws --profile decisions --region ca-west-1 \
  --certificate-arn arn:aws:acm:ca-west-1:ACCOUNT:certificate/CERTIFICATE \
  --domain-name bad-decisions.example.com \
  --hosted-zone-id ZONE_ID \
  --hosted-zone-name example.com
bad-decisions deploy aws
bad-decisions status aws
~~~

When DNS is hosted outside Route 53, omit `--hosted-zone-id` and
`--hosted-zone-name`. After deployment, create a CNAME for the requested
hostname pointing to the `ApiDomainTarget` CloudFormation output (also saved as
`BAD_DECISIONS_AWS_DOMAIN_TARGET` in `~/.bad-decisions.env`). The ACM
certificate must already be validated for that hostname. Route 53 users may
continue passing the hosted-zone arguments to have CDK create an alias record.

`setup aws` is one-time account preparation and is safe to repeat. It verifies
the AWS identity, creates or reuses an immutable ECR repository, creates the
management token in Secrets Manager only if the secret does not exist,
bootstraps CDK only if the `CDKToolkit` stack is missing (or with
`--bootstrap`, which needs IAM permissions), keeps the newest 10 ECR images,
and merges its settings into `~/.bad-decisions.env` (mode
`0600`) without discarding deploy outputs or saved domain settings. It never
rotates an existing token. Invalid HTTPS/DNS arguments are rejected before AWS
is changed.

`deploy aws` runs for every release. It builds and pushes an image of the exact
installed package version (so the image always matches the CDK stack that the
same package defines), shows `cdk diff`, asks for confirmation, then deploys.
Pass `--yes` to deploy unattended (required without a terminal), `--image` to
redeploy an already-pushed image, or `--source` to test an unreleased checkout.
Domain flags given to either command, and `--capacity spot|on-demand`, are
saved for later runs.

`rotate-token aws` replaces the management token in Secrets Manager, saves it
locally, and forces a new ECS deployment so every task picks it up together.
The old token keeps working until the old tasks stop.

`deploy aws` provisions the packaged CDK stack:

- the smallest Fargate API tasks (0.25 vCPU, 512 MiB), on Fargate Spot unless
  `--capacity on-demand`, with a Python container health check;
- an API Gateway HTTP API (throttled to 50 requests/s, burst 100) that reaches
  the tasks through a VPC link and Cloud Map; the tasks' security group admits
  only the VPC link;
- ACM HTTPS on an API Gateway custom domain plus either an external-DNS CNAME or a Route 53 alias whose
  hostname matches the certificate (the generated endpoint is then disabled);
- a private, encrypted, versioned S3 pack/archive bucket that expires
  superseded object versions after 30 days;
- a CloudFront distribution exposing only `/packs/*` over HTTPS;
- a Lambda-generated `/packs/index`, rebuilt from trusted `catalog/*.json`
  metadata whenever a pack is published;
- DynamoDB Consequences storage with on-demand billing, TTL, encryption, and
  point-in-time recovery;
- Secrets Manager injection, retained logs, API and indexer error alarms, CPU
  autoscaling, and free S3 and DynamoDB gateway endpoints.

The S3 layout deliberately separates concerns:

- `packs/<id>.carddeck`: immutable public archives;
- `runtime-packs/<id>.json`: private validated API registry objects;
- `catalog/<id>.json`: private provenance and index metadata;
- `packs/index`: public generated catalog.

Deployment seeds all bundled packs. Publish another validated archive with
`bad-decisions pack publish-aws FILE.carddeck`; it refuses overwrite, writes
catalog metadata last, rolls back exact object versions on failure, and forces
an ECS deployment so all workers load the same immutable registry. Inspect the
catalog with `bad-decisions pack list-aws`. If infrastructure deployment succeeded but initial seeding failed, run `bad-decisions pack seed-aws`; it uploads only missing bundled packs and restarts the service without another CDK deployment. Owner analytics are available with `bad-decisions consequences report aws`.

The public API and archive paths use HTTPS. Local mutations use the AWS SDK and
the caller's temporary IAM credentials over AWS HTTPS endpoints. The hidden
read-only status endpoint uses the protected local management capability;
secrets are never printed. `--allow-http` is only for disposable smoke tests:
it skips the custom domain and serves the generated `execute-api` URL (which is
still HTTPS).

The stack is sized for the lowest idle cost and scales horizontally: one task
scaling to two by CPU (raise it with `--max-count`), DynamoDB on-demand, and no
load balancer, NAT gateway, interface endpoints, or Container Insights. Tasks
run in public subnets with a public IP so they can reach ECR, CloudWatch Logs,
and Secrets Manager without paid endpoints; nothing reaches them except the VPC
link. The fixed monthly cost is roughly the task, its public IPv4 address, and
the Cloud Map private DNS zone; API Gateway, CloudFront, S3, and DynamoDB are
billed per request. Spot tasks can be interrupted with two minutes' notice and
are replaced automatically; use `--capacity on-demand` if that brief downtime
matters.
S3, DynamoDB, and CloudFront replace Garage only in AWS mode.
The original systemd/nginx, SQLite, and optional Garage deployment remain the
self-hosted mode.
