# dev-vps deployment record

Inspected 2026-09-16 on the current machine. Its static hostname is `bytes`;
Tailscale identifies it as `dev-vps` at `100.126.191.148`. It runs Ubuntu
24.04.4, system Python 3.12.3, nginx 1.24.0, and systemd. Ports 80/443 are
active; `127.0.0.1:8000` was unused. Nginx includes `sites-enabled/*` and the
enabled TLS site `bytes.coffee` already uses Certbot. Existing `/`, `/api`, and
`/gmail-pubsub` routes must be preserved. `/cah/` was selected as the proposed
nonconflicting route, with prefix stripping and `CAH_ROOT_PATH=/cah`.

The wheel and localhost API are tested, but the durable deployment is blocked:
this session's `sudo -n` reports `a password is required`. It therefore did not
create `/opt/cah-api`, the `cah-api` account, `/etc/cah-api.env`, or a systemd
unit, and did not change/reload nginx. Public routing is additionally blocked
by the MAHA pack's unknown redistribution rights; keep the complete service on
localhost/private access unless those rights are resolved. A public base-only
registry would be a materially different deployment and was not substituted.

## Exact remaining privileged steps

The preferred path is now simply:

```bash
cd /home/michael/cards
sudo ./deploy.sh
```

The script performs the release/service steps below, configures the existing
`bytes.coffee` HTTPS site at `/cah/`, validates/reloads nginx, and tests the
routed health endpoint. MAHA deployment is owner-authorized and CC BY-SA 4.0;
the base pack remains separately CC BY-NC-SA 2.0.

Run from `/home/michael/cards` after reviewing paths and granting sudo:

```bash
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin cah-api
sudo install -d -o root -g root -m 0755 /opt/cah-api/releases
sudo install -d -o root -g root -m 0755 /opt/cah-api/releases/20260916-1
sudo /usr/bin/python3.12 -m venv /opt/cah-api/releases/20260916-1/.venv
sudo /opt/cah-api/releases/20260916-1/.venv/bin/pip install --requirement /home/michael/cards/requirements.lock
sudo /opt/cah-api/releases/20260916-1/.venv/bin/pip install --no-deps /home/michael/cards/dist/cah_engine-1.0.0-py3-none-any.whl
sudo chown -R root:root /opt/cah-api/releases/20260916-1
sudo chmod -R go-w /opt/cah-api/releases/20260916-1
sudo ln -sfn /opt/cah-api/releases/20260916-1 /opt/cah-api/current.new
sudo mv -Tf /opt/cah-api/current.new /opt/cah-api/current
printf 'CAH_LOG_LEVEL=INFO\nCAH_ROOT_PATH=/cah\n' | sudo tee /etc/cah-api.env >/dev/null
sudo chown root:root /etc/cah-api.env
sudo chmod 0644 /etc/cah-api.env
sudo install -o root -g root -m 0644 deploy/cah-api.service /etc/systemd/system/cah-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now cah-api
curl --fail-with-body http://127.0.0.1:8000/healthz
curl --fail-with-body --get http://127.0.0.1:8000/v1/round --data-urlencode 'black_packs=maha' --data-urlencode 'white_packs=base,maha'
sudo systemctl status cah-api --no-pager
sudo journalctl -u cah-api -n 100 --no-pager
```

Before any public route, resolve MAHA rights or establish private nginx access.
Then merge the two-context directives in
`deploy/nginx.bytes.coffee.location.conf` into `/etc/nginx/nginx.conf` (the
`limit_req_zone`) and the existing TLS block in
`/etc/nginx/sites-available/bytes.coffee` (the `location`) after timestamped
backups. Do not paste the whole snippet into one context. Validate and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
curl --fail-with-body https://bytes.coffee/cah/healthz
curl --fail-with-body --get https://bytes.coffee/cah/v1/round --data-urlencode 'packs=all'
```

Verify controlled recovery with `sudo systemctl restart cah-api`, then repeat
health and generation checks and inspect the journal. To roll back, atomically
repoint `/opt/cah-api/current` to the retained previous release, restore only
the timestamped env/nginx files if changed, restart `cah-api`, validate nginx,
reload if needed, and repeat health checks.

The deployer automatically rolls back only to a release that was healthy before
the update. It leaves a first failed deployment selected for diagnosis instead
of restoring a known-failed release.
