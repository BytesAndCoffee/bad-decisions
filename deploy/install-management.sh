#!/usr/bin/env bash
# Install the tailnet-only management sign-in (docs/MANAGEMENT_AUTH.md): the attest
# service, its tailscale cert renewal, and the management settings in ENV_FILE.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then echo "Run this script with sudo." >&2; exit 1; fi
umask 0027
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
APP_NAME=${APP_NAME:-bad-decisions}
SERVICE_NAME=${SERVICE_NAME:-${APP_NAME}}
SERVICE_USER=${SERVICE_USER:-${APP_NAME}}
APP_ROOT=${APP_ROOT:-/opt/${APP_NAME}}
ENV_FILE=${ENV_FILE:-/etc/bad-decisions/bad-decisions.env}
BIND_HOST=${BIND_HOST:-127.0.0.1}
PORT=${PORT:-8000}
PYTHON=${PYTHON:-/usr/bin/python3.12}
TAILSCALE=${TAILSCALE:-$(command -v tailscale || true)}
# Required: the public site's exact origin and the Tailscale tags allowed to sign in.
MANAGEMENT_PUBLIC_ORIGIN=${MANAGEMENT_PUBLIC_ORIGIN:-}
MANAGEMENT_TAGS=${MANAGEMENT_TAGS:-}
# Optional: stable node IDs that must also match, the attest port, name, and bind address.
MANAGEMENT_NODES=${MANAGEMENT_NODES:-}
MANAGEMENT_ATTEST_PORT=${MANAGEMENT_ATTEST_PORT:-8443}
MANAGEMENT_ATTEST_NAME=${MANAGEMENT_ATTEST_NAME:-}
MANAGEMENT_ATTEST_BIND=${MANAGEMENT_ATTEST_BIND:-}

ATTEST_UNIT=${SERVICE_NAME}-management-attest
CERT_UNIT=${SERVICE_NAME}-management-cert
STATE_DIR=${APP_ROOT}/management
TLS_DIR=${APP_ROOT}/management-tls
UNIT_DIR=/etc/systemd/system

for value in "${APP_NAME}" "${SERVICE_NAME}"; do
  [[ ${value} =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "Unsafe application or service name." >&2; exit 1; }
done
[[ ${SERVICE_USER} =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Unsafe service user." >&2; exit 1; }
[[ ${APP_ROOT} =~ ^/[A-Za-z0-9._/-]+$ && ${APP_ROOT} != / && ${ENV_FILE} =~ ^/[A-Za-z0-9._/-]+$ ]] || { echo "Invalid APP_ROOT or ENV_FILE." >&2; exit 1; }
[[ ${PORT} =~ ^[0-9]+$ && ${MANAGEMENT_ATTEST_PORT} =~ ^[0-9]+$ ]] || { echo "Invalid PORT or MANAGEMENT_ATTEST_PORT." >&2; exit 1; }
[[ ${MANAGEMENT_PUBLIC_ORIGIN} =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || { echo "Set MANAGEMENT_PUBLIC_ORIGIN to the public site's exact https://host[:port] origin." >&2; exit 1; }
[[ ${MANAGEMENT_TAGS} =~ ^tag:[A-Za-z0-9][A-Za-z0-9-]*(,tag:[A-Za-z0-9][A-Za-z0-9-]*)*$ ]] || { echo "Set MANAGEMENT_TAGS to Tailscale tags such as tag:mgmt, comma-separated." >&2; exit 1; }
[[ -z ${MANAGEMENT_NODES} || ${MANAGEMENT_NODES} =~ ^[A-Za-z0-9]+(,[A-Za-z0-9]+)*$ ]] || { echo "MANAGEMENT_NODES must be stable node IDs, comma-separated." >&2; exit 1; }
[[ ${TAILSCALE} =~ ^/[A-Za-z0-9._/-]+$ && -x ${TAILSCALE} ]] || { echo "Install Tailscale, or set TAILSCALE to its absolute path." >&2; exit 1; }
id "${SERVICE_USER}" >/dev/null 2>&1 || { echo "Service user ${SERVICE_USER} does not exist; deploy the service first." >&2; exit 1; }
[[ -f ${ENV_FILE} && -L ${APP_ROOT}/current ]] || { echo "No deployed release at ${APP_ROOT}/current; deploy the service first." >&2; exit 1; }

if [[ -z ${MANAGEMENT_ATTEST_NAME} ]]; then
  MANAGEMENT_ATTEST_NAME=$("${TAILSCALE}" status --json | "${PYTHON}" -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
fi
[[ -z ${MANAGEMENT_ATTEST_BIND} ]] && MANAGEMENT_ATTEST_BIND=$("${TAILSCALE}" ip -4 | head -n 1)
[[ ${MANAGEMENT_ATTEST_NAME} =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Could not determine this node's MagicDNS name; set MANAGEMENT_ATTEST_NAME." >&2; exit 1; }
[[ ${MANAGEMENT_ATTEST_BIND} =~ ^100\.[0-9.]+$|^fd7a:115c:a1e0:[0-9a-f:]+$ ]] || { echo "MANAGEMENT_ATTEST_BIND must be this node's tailnet address." >&2; exit 1; }
ATTEST_URL=https://${MANAGEMENT_ATTEST_NAME}:${MANAGEMENT_ATTEST_PORT}

# The marker that makes every later deploy install requirements-management.lock.
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0700 "${STATE_DIR}"
if ! "${APP_ROOT}/current/.venv/bin/python" -c 'import cryptography' 2>/dev/null; then
  echo "The serving release lacks the [management] dependencies. ${STATE_DIR} now exists, so the next" >&2
  echo "deploy installs them: redeploy (deploy.sh, or bad-decisions deploy local --redeploy), then rerun this." >&2
  exit 1
fi

BACKUP_DIR=${APP_ROOT}/backups/management-$(date -u +%Y%m%dT%H%M%SZ)
install -d -o root -g root -m 0700 "${BACKUP_DIR}"
backup() { if [[ -e $1 ]]; then cp -p "$1" "${BACKUP_DIR}/$2"; else : > "${BACKUP_DIR}/$2.absent"; fi; }
restore() { if [[ -e ${BACKUP_DIR}/$2.absent ]]; then rm -f "$1"; else cp -p "${BACKUP_DIR}/$2" "$1"; fi; }
backup "${ENV_FILE}" env
backup "${UNIT_DIR}/${ATTEST_UNIT}.service" attest
backup "${UNIT_DIR}/${CERT_UNIT}.service" cert
backup "${UNIT_DIR}/${CERT_UNIT}.timer" timer

restore_on_error() {
  local status=$?
  echo "install-management failed; restoring the previous configuration." >&2
  restore "${ENV_FILE}" env || true
  restore "${UNIT_DIR}/${ATTEST_UNIT}.service" attest || true
  restore "${UNIT_DIR}/${CERT_UNIT}.service" cert || true
  restore "${UNIT_DIR}/${CERT_UNIT}.timer" timer || true
  systemctl daemon-reload || true
  [[ -e ${BACKUP_DIR}/attest.absent ]] && systemctl disable --now "${ATTEST_UNIT}.service" || true
  [[ -e ${BACKUP_DIR}/timer.absent ]] && systemctl disable --now "${CERT_UNIT}.timer" || true
  systemctl restart "${SERVICE_NAME}" || true
  exit "${status}"
}
trap restore_on_error ERR

set_env() {
  local name=$1 value=$2
  if [[ -z ${value} ]]; then
    sed -i "/^${name}=/d" "${ENV_FILE}"
  elif grep -q "^${name}=" "${ENV_FILE}"; then
    sed -i "s|^${name}=.*|${name}=${value}|" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${name}" "${value}" >> "${ENV_FILE}"
  fi
}
set_env BAD_DECISIONS_MANAGEMENT_DB "${STATE_DIR}/management.sqlite3"
set_env BAD_DECISIONS_MANAGEMENT_PUBLIC_ORIGIN "${MANAGEMENT_PUBLIC_ORIGIN}"
set_env BAD_DECISIONS_MANAGEMENT_ATTEST_URL "${ATTEST_URL}"
set_env BAD_DECISIONS_MANAGEMENT_TAGS "${MANAGEMENT_TAGS}"
set_env BAD_DECISIONS_MANAGEMENT_NODES "${MANAGEMENT_NODES}"

install -d -o root -g "${SERVICE_USER}" -m 0750 "${TLS_DIR}"
render() {
  sed -e "s|@APP_ROOT@|${APP_ROOT}|g" -e "s|@SERVICE_NAME@|${SERVICE_NAME}|g" -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
    -e "s|@ENV_FILE@|${ENV_FILE}|g" -e "s|@ATTEST_BIND@|${MANAGEMENT_ATTEST_BIND}|g" -e "s|@ATTEST_PORT@|${MANAGEMENT_ATTEST_PORT}|g" \
    -e "s|@ATTEST_NAME@|${MANAGEMENT_ATTEST_NAME}|g" -e "s|@TLS_DIR@|${TLS_DIR}|g" -e "s|@TAILSCALE@|${TAILSCALE}|g" "$1" > "$2"
  chmod 0644 "$2"
}
render "${SCRIPT_DIR}/deploy/management-attest.service.template" "${UNIT_DIR}/${ATTEST_UNIT}.service"
render "${SCRIPT_DIR}/deploy/management-cert.service.template" "${UNIT_DIR}/${CERT_UNIT}.service"
render "${SCRIPT_DIR}/deploy/management-cert.timer.template" "${UNIT_DIR}/${CERT_UNIT}.timer"
systemctl daemon-reload
systemctl start "${CERT_UNIT}.service"
systemctl enable --now "${CERT_UNIT}.timer"
systemctl restart "${SERVICE_NAME}"
systemctl enable "${ATTEST_UNIT}.service"
systemctl restart "${ATTEST_UNIT}.service"

curl --fail --silent --show-error --retry 10 --retry-delay 1 --retry-max-time 60 --retry-connrefused "http://${BIND_HOST}:${PORT}/healthz" >/dev/null
# A preflight from the public origin must be allowed; one from anywhere else must not.
curl --fail --silent --show-error --retry 10 --retry-delay 1 --retry-max-time 60 --retry-connrefused -o /dev/null -X OPTIONS \
  -H "Origin: ${MANAGEMENT_PUBLIC_ORIGIN}" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type" \
  "${ATTEST_URL}/attest"
if curl --fail --silent -o /dev/null -X OPTIONS -H "Origin: https://example.invalid" -H "Access-Control-Request-Method: POST" "${ATTEST_URL}/attest"; then
  echo "The attest service accepted a foreign origin." >&2
  false
fi

trap - ERR
echo "Management sign-in is installed. Open ${MANAGEMENT_PUBLIC_ORIGIN}<root path>/manage from a device tagged ${MANAGEMENT_TAGS}."
echo "Attest service: ${ATTEST_URL} (bound to ${MANAGEMENT_ATTEST_BIND}). Logs: journalctl -u ${ATTEST_UNIT} -n 100 --no-pager"
