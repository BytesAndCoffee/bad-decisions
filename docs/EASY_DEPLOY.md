# Easy deploy

The short path for a single Linux host with systemd and nginx: one privileged
install, one privileged bootstrap, then every update and rollback without sudo.
[DEPLOYMENT.md](DEPLOYMENT.md) is the full reference.

## Sane defaults

Unless you need something else, keep these. Every command below assumes them.

| Setting | Default | Notes |
| --- | --- | --- |
| `APP_NAME` / service | `bad-decisions` | systemd unit `bad-decisions.service` |
| `APP_ROOT` | `/opt/bad-decisions` | immutable releases live in `releases/` |
| `SERVICE_USER` | `bad-decisions` | locked system account that runs the API |
| `BIND_HOST` / `PORT` | `127.0.0.1` / `8000` | loopback only; nginx is the public edge |
| `ROOT_PATH` | `/bad-decisions` | public route under your site |
| `WORKERS` | `2` | Uvicorn workers |
| `PYTHON` | `/usr/bin/python3.12` | interpreter for release virtualenvs |

If you change any of these, pass the same values to every privileged command
below (`deploy.sh` and `deploy.sh bootstrap-rootless`). A mismatched `PORT` is
the classic mistake: the health check probes the wrong service and the deploy
rolls itself back.

## 1. First install (sudo, once)

Prerequisites: Python 3.12 with venv support, nginx, curl, and an HTTPS site.

```bash
git clone https://github.com/BytesAndCoffee/bad-decisions.git
cd bad-decisions
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Add this marker inside the nginx `server` block that should serve the API, then
check nginx with `sudo nginx -t`:

```nginx
# bad-decisions-location
```

Deploy:

```bash
sudo NGINX_SITE_CONFIG=/etc/nginx/sites-available/example.com \
  PUBLIC_BASE_URL=https://example.com/bad-decisions \
  ./deploy.sh
```

This creates the service account, `/etc/bad-decisions/bad-decisions.env`, the
systemd unit, the nginx include, and the first release. It finishes only after
the local and public health checks pass. Check it:

```bash
curl -s https://example.com/bad-decisions/healthz
```

## 2. Bootstrap rootless deploys (sudo, once)

```bash
sudo DEPLOY_USER="$USER" ./deploy.sh bootstrap-rootless
```

This installs a root-owned release activator and adds `DEPLOY_USER` to the
`bad-decisions-deploy` group. It adds no sudoers rule. **Log out and back in**
so the new group applies (`id -nG` should list `bad-decisions-deploy`).

## 3. Everyday: deploy and roll back (no sudo)

Install the command once in its own virtualenv (system pip is locked on current
Ubuntu):

```bash
python3.12 -m venv ~/.venvs/bad-decisions
. ~/.venvs/bad-decisions/bin/activate
```

Deploy the latest release:

```bash
pip install --upgrade bad-decisions && bad-decisions deploy local
```

`deploy local` deploys exactly the installed version. It downloads that
version's wheel from PyPI, checks its SHA-256, and installs it with the pinned
dependencies it ships with. The activator builds the release as the service
account, locks it read-only, switches to it, and waits for `/healthz` to report
the new version. If anything fails, the previous release is restored
automatically.

Roll back:

```bash
bad-decisions rollback local                   # newest known-good release that is not current
bad-decisions rollback local 20260102T000000Z  # a specific release (see /opt/bad-decisions/releases)
```

Repeating a plain rollback steps further back; it never rolls forward. To go
forward again, deploy, or name the newer release explicitly.

If you use a non-default `APP_ROOT`, add `--app-root PATH` to both commands.

### If something goes wrong

- **"PyPI has a newer version" warning:** the index can lag for a few minutes
  after a release. Rerun the `pip install --upgrade`, then deploy again.
- **Deployment or rollback failed:** the command prints the end of
  `/opt/bad-decisions/activation/log.txt`, which includes pip's output. No
  journal access is needed.
- **"cannot stage a release" or permission errors:** your login session
  predates the group change. Log out and back in.
- **"another local activation request is already pending":** wait for it to
  finish. Only one request runs at a time.
- **Timed out:** read `activation/log.txt`, or with journal access,
  `journalctl -u bad-decisions-activate.service`.

## When to run the full sudo deploy instead

Rootless deploys change only the application release. Use the privileged path
when a change touches anything outside a release:

| Change | Run |
| --- | --- |
| First install on a host | `sudo ... ./deploy.sh` (step 1) |
| nginx site, public URL, or `ROOT_PATH` | `sudo NGINX_SITE_CONFIG=... PUBLIC_BASE_URL=... ./deploy.sh` |
| `PORT`, `BIND_HOST`, `WORKERS`, `SERVICE_USER`, or the systemd unit | `sudo ... ./deploy.sh`, then rerun `bootstrap-rootless` with the same values (the activator's unit records `PORT`, `BIND_HOST`, and `SERVICE_USER`) |
| Python interpreter (`PYTHON`) | `sudo PYTHON=... ./deploy.sh`, then rerun `bootstrap-rootless` with the same `PYTHON` |
| A release note says it needs new host directories, environment keys, or unit changes | `sudo CONFIGURE_NGINX=0 PORT=... ./deploy.sh` from that version's checkout |
| A new version changes the activator (`deploy/activate-release.py`) | `sudo DEPLOY_USER=... ./deploy.sh bootstrap-rootless` from that version's checkout (safe to rerun) |
| Secrets or settings in `/etc/bad-decisions/bad-decisions.env` | edit with sudo, then `sudo systemctl restart bad-decisions` |
| Rootless path unavailable (activator broken, not bootstrapped) | `sudo CONFIGURE_NGINX=0 PORT=... ./deploy.sh`, or `sudo ./deploy.sh rollback [RELEASE_ID]` |

For a privileged update of an existing install that should leave nginx alone,
use `CONFIGURE_NGINX=0` and the running service's `PORT`:

```bash
sudo CONFIGURE_NGINX=0 PORT=8000 ./deploy.sh
```

Both paths share one release history and one known-good list, so
`sudo ./deploy.sh rollback` and `bad-decisions rollback local` apply the same
rules and can be used interchangeably.
