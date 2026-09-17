#!/usr/bin/env bash
# Deploy the tested wheel as an immutable local release.
# Run from any directory with: sudo /path/to/deploy.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo: sudo ./deploy.sh" >&2
  exit 1
fi

umask 0027
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
APP_ROOT=/opt/cah-api
RELEASES_DIR=${APP_ROOT}/releases
CURRENT_LINK=${APP_ROOT}/current
PYTHON=/usr/bin/python3.12
BUILD_PYTHON=${SCRIPT_DIR}/.venv/bin/python
SITE_CONFIG=/etc/nginx/sites-available/bytes.coffee
NGINX_LIMIT=/etc/nginx/conf.d/cah-api-limit.conf
NGINX_SNIPPET=/etc/nginx/snippets/cah-api-location.conf

if [[ ! -x ${PYTHON} ]]; then
  echo "Missing required interpreter: ${PYTHON}" >&2
  exit 1
fi
if [[ ! -x ${BUILD_PYTHON} ]]; then
  echo "Missing ${BUILD_PYTHON}. First create the tested project environment." >&2
  exit 1
fi
if [[ ! -f ${SITE_CONFIG} ]] || ! grep -q 'server_name bytes\.coffee;' "${SITE_CONFIG}"; then
  echo "Expected dev-vps nginx site was not found: ${SITE_CONFIG}" >&2
  exit 1
fi
if ! id -u cah-api >/dev/null 2>&1; then
  useradd --system --home /nonexistent --shell /usr/sbin/nologin cah-api
fi

echo "Building wheel from ${SCRIPT_DIR}"
"${BUILD_PYTHON}" -m build "${SCRIPT_DIR}"
VERSION=$("${BUILD_PYTHON}" -c 'from cah_engine import __version__; print(__version__)')
WHEEL=${SCRIPT_DIR}/dist/cards_against_coffee_server-${VERSION}-py3-none-any.whl
if [[ ! -f ${WHEEL} ]]; then
  echo "Expected wheel was not created: ${WHEEL}" >&2
  exit 1
fi

RELEASE_ID=$(date -u +%Y%m%dT%H%M%SZ)
RELEASE_DIR=${RELEASES_DIR}/${RELEASE_ID}
if [[ -e ${RELEASE_DIR} ]]; then
  echo "Release path already exists: ${RELEASE_DIR}" >&2
  exit 1
fi
PREVIOUS_RELEASE=
if systemctl is-active --quiet cah-api && [[ -L ${CURRENT_LINK} ]]; then
  PREVIOUS_RELEASE=$(readlink -f "${CURRENT_LINK}" || true)
fi
ACTIVATED=false
NGINX_CONFIGURED=false
BACKUP_DIR=${APP_ROOT}/backups/${RELEASE_ID}
ENV_EXISTED=false
UNIT_EXISTED=false

backup_file() {
  local source=$1 destination=$2
  if [[ -e ${source} ]]; then
    cp -p "${source}" "${destination}"
  else
    : > "${destination}.absent"
  fi
}

restore_file() {
  local backup=$1 destination=$2
  if [[ -e ${backup}.absent ]]; then
    rm -f "${destination}"
  else
    cp -p "${backup}" "${destination}"
  fi
}

rollback_on_error() {
  status=$?
  if [[ ${NGINX_CONFIGURED} == true ]]; then
    restore_file "${BACKUP_DIR}/bytes.coffee" "${SITE_CONFIG}"
    restore_file "${BACKUP_DIR}/cah-api-limit.conf" "${NGINX_LIMIT}"
    restore_file "${BACKUP_DIR}/cah-api-location.conf" "${NGINX_SNIPPET}"
    nginx -t && systemctl reload nginx || true
  fi
  if [[ ${ENV_EXISTED} == true ]]; then
    cp -p "${BACKUP_DIR}/cah-api.env" /etc/cah-api.env
  elif [[ -d ${BACKUP_DIR} ]]; then
    rm -f /etc/cah-api.env
  fi
  if [[ ${UNIT_EXISTED} == true ]]; then
    cp -p "${BACKUP_DIR}/cah-api.service" /etc/systemd/system/cah-api.service
  elif [[ -d ${BACKUP_DIR} ]]; then
    rm -f /etc/systemd/system/cah-api.service
  fi
  systemctl daemon-reload || true
  if [[ ${ACTIVATED} == true && -n ${PREVIOUS_RELEASE} && -d ${PREVIOUS_RELEASE} ]]; then
    echo "Deployment failed; restoring ${PREVIOUS_RELEASE}" >&2
    ln -sfn "${PREVIOUS_RELEASE}" "${APP_ROOT}/current.new"
    mv -Tf "${APP_ROOT}/current.new" "${CURRENT_LINK}"
    systemctl restart cah-api || true
  elif [[ ${ACTIVATED} == true ]]; then
    echo "Deployment failed, but no previously healthy release exists to restore." >&2
    echo "The newly staged release remains selected for diagnosis." >&2
  fi
  exit "${status}"
}
trap rollback_on_error ERR

install -d -o root -g root -m 0755 "${RELEASES_DIR}" "${RELEASE_DIR}"
"${PYTHON}" -m venv "${RELEASE_DIR}/.venv"
"${RELEASE_DIR}/.venv/bin/pip" install --requirement "${SCRIPT_DIR}/requirements.lock"
"${RELEASE_DIR}/.venv/bin/pip" install --no-deps "${WHEEL}"

# The service account must traverse the release and execute the venv, but it
# must never be able to write it. Root remains the owner; cah-api gets read/X.
chown -R root:cah-api "${RELEASE_DIR}"
chmod -R u=rwX,g=rX,o= "${RELEASE_DIR}"

install -d -o root -g root -m 0755 "${APP_ROOT}/backups" "${BACKUP_DIR}"
if [[ -f /etc/cah-api.env ]]; then
  ENV_EXISTED=true
  cp -p /etc/cah-api.env "${BACKUP_DIR}/cah-api.env"
else
  install -o root -g root -m 0644 "${SCRIPT_DIR}/deploy/cah-api.env.example" /etc/cah-api.env
fi
if [[ -f /etc/systemd/system/cah-api.service ]]; then
  UNIT_EXISTED=true
  cp -p /etc/systemd/system/cah-api.service "${BACKUP_DIR}/cah-api.service"
fi
install -o root -g root -m 0644 "${SCRIPT_DIR}/deploy/cah-api.service" /etc/systemd/system/cah-api.service
if grep -q '^CAH_ROOT_PATH=' /etc/cah-api.env; then
  sed -i 's|^CAH_ROOT_PATH=.*|CAH_ROOT_PATH=/cah|' /etc/cah-api.env
else
  printf '\nCAH_ROOT_PATH=/cah\n' >> /etc/cah-api.env
fi

backup_file "${SITE_CONFIG}" "${BACKUP_DIR}/bytes.coffee"
backup_file "${NGINX_LIMIT}" "${BACKUP_DIR}/cah-api-limit.conf"
backup_file "${NGINX_SNIPPET}" "${BACKUP_DIR}/cah-api-location.conf"
install -d -o root -g root -m 0755 /etc/nginx/snippets
install -o root -g root -m 0644 /dev/stdin "${NGINX_LIMIT}" <<'EOF'
limit_req_zone $binary_remote_addr zone=cah_api:10m rate=5r/s;
EOF
install -o root -g root -m 0644 /dev/stdin "${NGINX_SNIPPET}" <<EOF
location /cah/ {
    limit_req zone=cah_api burst=10 nodelay;
    limit_req_status 429;
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_connect_timeout 5s;
    proxy_read_timeout 15s;
}
EOF
if ! grep -Fq 'include /etc/nginx/snippets/cah-api-location.conf;' "${SITE_CONFIG}"; then
  if ! grep -q '^[[:space:]]*include fcgiwrap\.conf;' "${SITE_CONFIG}"; then
    echo "Cannot find the expected bytes.coffee insertion point; nginx was not changed." >&2
    exit 1
  fi
  sed -i '/^[[:space:]]*include fcgiwrap\.conf;/i\    include /etc/nginx/snippets/cah-api-location.conf;' "${SITE_CONFIG}"
fi
NGINX_CONFIGURED=true
nginx -t

ln -sfn "${RELEASE_DIR}" "${APP_ROOT}/current.new"
mv -Tf "${APP_ROOT}/current.new" "${CURRENT_LINK}"
ACTIVATED=true

systemctl daemon-reload
systemctl enable cah-api
systemctl restart cah-api
curl --fail --silent --show-error --retry 10 --retry-connrefused http://127.0.0.1:8000/healthz
curl --fail --silent --show-error --get http://127.0.0.1:8000/v1/round \
  --data-urlencode 'black_packs=maha' \
  --data-urlencode 'white_packs=base,maha' \
  --output /dev/null
systemctl reload nginx
curl --fail --silent --show-error --resolve bytes.coffee:443:127.0.0.1 \
  https://bytes.coffee/cah/healthz

trap - ERR
echo
echo "Deployed ${RELEASE_ID} to ${CURRENT_LINK}"
echo "Check logs with: journalctl -u cah-api -n 100 --no-pager"
echo "Nginx route: https://bytes.coffee/cah/"
