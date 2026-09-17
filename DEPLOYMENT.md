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
