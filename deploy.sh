#!/usr/bin/env bash
# Deploy a tested Bad Decisions server wheel to a configurable Linux host.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

umask 0027
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
APP_NAME=${APP_NAME:-bad-decisions}
SERVICE_USER=${SERVICE_USER:-${APP_NAME}}
APP_ROOT=${APP_ROOT:-/opt/${APP_NAME}}
SERVICE_NAME=${SERVICE_NAME:-${APP_NAME}}
ENV_FILE=${ENV_FILE:-/etc/bad-decisions/bad-decisions.env}
PYTHON=${PYTHON:-/usr/bin/python3.12}
BUILD_PYTHON=${BUILD_PYTHON:-${SCRIPT_DIR}/.venv/bin/python}
ROOT_PATH=${ROOT_PATH:-/bad-decisions}
BIND_HOST=${BIND_HOST:-127.0.0.1}
PORT=${PORT:-8000}
WORKERS=${WORKERS:-2}
CONFIGURE_NGINX=${CONFIGURE_NGINX:-1}
NGINX_SITE_CONFIG=${NGINX_SITE_CONFIG:-}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-}
NGINX_ZONE=${NGINX_ZONE:-bad_decisions}
RELEASES_DIR=${APP_ROOT}/releases
CURRENT_LINK=${APP_ROOT}/current
UNIT_FILE=/etc/systemd/system/${SERVICE_NAME}.service
NGINX_LIMIT=/etc/nginx/conf.d/${APP_NAME}.limit.conf
NGINX_SNIPPET=/etc/nginx/snippets/${APP_NAME}.conf

if [[ ! ${APP_NAME} =~ ^[a-z0-9][a-z0-9-]*$ ]] || [[ ! ${SERVICE_USER} =~ ^[a-z_][a-z0-9_-]*$ ]] || [[ ! ${SERVICE_NAME} =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "APP_NAME, SERVICE_NAME, and SERVICE_USER must be safe Linux identifiers." >&2
  exit 1
fi
if [[ ! ${ROOT_PATH} =~ ^/[a-zA-Z0-9._/-]+$ ]] || [[ ${ROOT_PATH} == / ]]; then
  echo "ROOT_PATH must be a non-root absolute URL path." >&2
  exit 1
fi
ROOT_PATH=${ROOT_PATH%/}
if [[ ! ${PORT} =~ ^[0-9]+$ ]] || [[ ! ${WORKERS} =~ ^[1-9][0-9]*$ ]] || [[ ! ${NGINX_ZONE} =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "PORT, WORKERS, or NGINX_ZONE is invalid." >&2
  exit 1
fi
if [[ ! -x ${PYTHON} ]] || [[ ! -x ${BUILD_PYTHON} ]]; then
  echo "Missing required Python interpreter or project build environment." >&2
  exit 1
fi
if [[ ${CONFIGURE_NGINX} != 0 && ${CONFIGURE_NGINX} != 1 ]]; then
  echo "CONFIGURE_NGINX must be 0 or 1." >&2
  exit 1
fi
if [[ ${CONFIGURE_NGINX} == 1 ]]; then
  if [[ -z ${NGINX_SITE_CONFIG} || ! -f ${NGINX_SITE_CONFIG} || -z ${PUBLIC_BASE_URL} ]]; then
    echo "Set NGINX_SITE_CONFIG and PUBLIC_BASE_URL, or use CONFIGURE_NGINX=0." >&2
    exit 1
  fi
  if [[ ! ${PUBLIC_BASE_URL} =~ ^https:// ]]; then
    echo "PUBLIC_BASE_URL must be an HTTPS URL." >&2
    exit 1
  fi
  PUBLIC_BASE_URL=${PUBLIC_BASE_URL%/}
fi

echo "Building ${APP_NAME} wheel"
"${BUILD_PYTHON}" -m build "${SCRIPT_DIR}"
VERSION=$("${BUILD_PYTHON}" -c 'from bad_decisions import __version__; print(__version__)')
WHEEL=${SCRIPT_DIR}/dist/bad_decisions-${VERSION}-py3-none-any.whl
if [[ ! -f ${WHEEL} ]]; then
  echo "Expected wheel was not created: ${WHEEL}" >&2
  exit 1
fi

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home /nonexistent --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

RELEASE_ID=$(date -u +%Y%m%dT%H%M%SZ)
RELEASE_DIR=${RELEASES_DIR}/${RELEASE_ID}
PREVIOUS_RELEASE=
if systemctl is-active --quiet "${SERVICE_NAME}" && [[ -L ${CURRENT_LINK} ]]; then
  PREVIOUS_RELEASE=$(readlink -f "${CURRENT_LINK}" || true)
fi
ACTIVATED=false
BACKUP_DIR=${APP_ROOT}/backups/${RELEASE_ID}

backup_file() {
  local source=$1 destination=$2
  if [[ -e ${source} ]]; then cp -p "${source}" "${destination}"; else : > "${destination}.absent"; fi
}

restore_file() {
  local backup=$1 destination=$2
  if [[ -e ${backup}.absent ]]; then rm -f "${destination}"; else cp -p "${backup}" "${destination}"; fi
}

rollback_on_error() {
  local status=$?
  if [[ -d ${BACKUP_DIR} ]]; then
    restore_file "${BACKUP_DIR}/env" "${ENV_FILE}" || true
    restore_file "${BACKUP_DIR}/unit" "${UNIT_FILE}" || true
    if [[ ${CONFIGURE_NGINX} == 1 ]]; then
      for name in site limit snippet; do
        backup="${BACKUP_DIR}/${name}"
        destination=${NGINX_SITE_CONFIG}
        [[ ${name} == limit ]] && destination=${NGINX_LIMIT}
        [[ ${name} == snippet ]] && destination=${NGINX_SNIPPET}
        if [[ -e ${backup} || -e ${backup}.absent ]]; then restore_file "${backup}" "${destination}" || true; fi
      done
      nginx -t && systemctl reload nginx || true
    fi
  fi
  systemctl daemon-reload || true
  if [[ ${ACTIVATED} == true && -n ${PREVIOUS_RELEASE} && -d ${PREVIOUS_RELEASE} ]]; then
    ln -sfn "${PREVIOUS_RELEASE}" "${APP_ROOT}/current.new"
    mv -Tf "${APP_ROOT}/current.new" "${CURRENT_LINK}"
    systemctl restart "${SERVICE_NAME}" || true
  fi
  exit "${status}"
}
trap rollback_on_error ERR

install -d -o root -g root -m 0755 "${RELEASES_DIR}" "${RELEASE_DIR}" "${BACKUP_DIR}"
"${PYTHON}" -m venv "${RELEASE_DIR}/.venv"
"${RELEASE_DIR}/.venv/bin/pip" install --requirement "${SCRIPT_DIR}/requirements.lock"
"${RELEASE_DIR}/.venv/bin/pip" install --no-deps "${WHEEL}"
chown -R root:"${SERVICE_USER}" "${RELEASE_DIR}"
chmod -R u=rwX,g=rX,o= "${RELEASE_DIR}"

backup_file "${ENV_FILE}" "${BACKUP_DIR}/env"
backup_file "${UNIT_FILE}" "${BACKUP_DIR}/unit"
install -d -o root -g root -m 0755 "$(dirname "${ENV_FILE}")"
if [[ ! -f ${ENV_FILE} ]]; then
  install -o root -g root -m 0644 "${SCRIPT_DIR}/deploy/server.env.example" "${ENV_FILE}"
fi
if grep -q '^BAD_DECISIONS_ROOT_PATH=' "${ENV_FILE}"; then
  sed -i "s|^BAD_DECISIONS_ROOT_PATH=.*|BAD_DECISIONS_ROOT_PATH=${ROOT_PATH}|" "${ENV_FILE}"
else
  printf '\nBAD_DECISIONS_ROOT_PATH=%s\n' "${ROOT_PATH}" >> "${ENV_FILE}"
fi
sed \
  -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
  -e "s|@APP_ROOT@|${APP_ROOT}|g" \
  -e "s|@ENV_FILE@|${ENV_FILE}|g" \
  -e "s|@BIND_HOST@|${BIND_HOST}|g" \
  -e "s|@PORT@|${PORT}|g" \
  -e "s|@WORKERS@|${WORKERS}|g" \
  "${SCRIPT_DIR}/deploy/server.service.template" > "${UNIT_FILE}"
chmod 0644 "${UNIT_FILE}"

if [[ ${CONFIGURE_NGINX} == 1 ]]; then
  backup_file "${NGINX_SITE_CONFIG}" "${BACKUP_DIR}/site"
  backup_file "${NGINX_LIMIT}" "${BACKUP_DIR}/limit"
  backup_file "${NGINX_SNIPPET}" "${BACKUP_DIR}/snippet"
  marker_count=$(grep -Fc '# bad-decisions-location' "${NGINX_SITE_CONFIG}" || true)
  if [[ ${marker_count} -gt 1 ]]; then
    echo "NGINX_SITE_CONFIG contains multiple '# bad-decisions-location' markers; leave exactly one." >&2
    exit 1
  fi
  if [[ ${marker_count} == 0 ]] && ! grep -Fq "include ${NGINX_SNIPPET};" "${NGINX_SITE_CONFIG}"; then
    echo "Add '# bad-decisions-location' inside the intended nginx server block first." >&2
    exit 1
  fi
  install -d -o root -g root -m 0755 /etc/nginx/snippets
  printf 'limit_req_zone $binary_remote_addr zone=%s:10m rate=10r/s;\n' "${NGINX_ZONE}" > "${NGINX_LIMIT}"
  cat > "${NGINX_SNIPPET}" <<EOF
location ${ROOT_PATH}/ {
    limit_req zone=${NGINX_ZONE} burst=30 nodelay;
    proxy_pass http://${BIND_HOST}:${PORT}/;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Prefix ${ROOT_PATH};
}
EOF
  if ! grep -Fq "include ${NGINX_SNIPPET};" "${NGINX_SITE_CONFIG}"; then
    sed -i "s|# bad-decisions-location|    include ${NGINX_SNIPPET};\\n    # bad-decisions-location|" "${NGINX_SITE_CONFIG}"
  fi
  nginx -t
fi

ln -sfn "${RELEASE_DIR}" "${APP_ROOT}/current.new"
mv -Tf "${APP_ROOT}/current.new" "${CURRENT_LINK}"
ACTIVATED=true
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
curl --fail --silent --show-error --retry 10 --retry-connrefused "http://${BIND_HOST}:${PORT}/healthz"
curl --fail --silent --show-error --get "http://${BIND_HOST}:${PORT}/v1/round" --data-urlencode 'packs=base'
curl --fail --silent --show-error "http://${BIND_HOST}:${PORT}/web/" | grep -Fq '<base href='
curl --fail --silent --show-error "http://${BIND_HOST}:${PORT}/web/app.js" | grep -Fq 'loadPacks'
if [[ ${CONFIGURE_NGINX} == 1 ]]; then
  systemctl reload nginx
  curl --fail --silent --show-error "${PUBLIC_BASE_URL}/healthz"
  curl --fail --silent --show-error "${PUBLIC_BASE_URL}/web/" | grep -Fq '<base href='
  curl --fail --silent --show-error "${PUBLIC_BASE_URL}/web/app.js" | grep -Fq 'loadPacks'
fi

trap - ERR
echo "Deployment complete: ${APP_NAME} ${VERSION}"
echo "Logs: journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
