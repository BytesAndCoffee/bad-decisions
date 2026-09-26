#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then echo "Run this script with sudo." >&2; exit 1; fi
umask 0027
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
APP_NAME=${APP_NAME:-bad-decisions}
SERVICE_NAME=${SERVICE_NAME:-${APP_NAME}}
SERVICE_USER=${SERVICE_USER:-${APP_NAME}}
APP_ROOT=${APP_ROOT:-/opt/${APP_NAME}}
DEPLOY_USER=${DEPLOY_USER:-${SUDO_USER:-}}
DEPLOY_GROUP=${DEPLOY_GROUP:-${APP_NAME}-deploy}
BIND_HOST=${BIND_HOST:-127.0.0.1}
PORT=${PORT:-8000}
PYTHON=${PYTHON:-/usr/bin/python3.12}

if [[ -z ${DEPLOY_USER} ]] || ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
  echo "Set DEPLOY_USER to the existing account allowed to stage releases." >&2; exit 1
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "Service user ${SERVICE_USER} does not exist; run the first privileged deployment first." >&2; exit 1
fi
for value in "${APP_NAME}" "${SERVICE_NAME}"; do
  [[ ${value} =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "Unsafe application or service name." >&2; exit 1; }
done
[[ ${SERVICE_USER} =~ ^[a-z_][a-z0-9_-]*$ && ${DEPLOY_GROUP} =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Unsafe user or group name." >&2; exit 1; }
[[ ${APP_ROOT} == /* && ${APP_ROOT} != / && ${PORT} =~ ^[0-9]+$ ]] || { echo "Invalid APP_ROOT or PORT." >&2; exit 1; }

getent group "${DEPLOY_GROUP}" >/dev/null || groupadd --system "${DEPLOY_GROUP}"
usermod --append --groups "${DEPLOY_GROUP}" "${DEPLOY_USER}"
install -d -o root -g root -m 0755 "${APP_ROOT}" "${APP_ROOT}/releases"
install -d -o root -g "${DEPLOY_GROUP}" -m 2770 "${APP_ROOT}/incoming" "${APP_ROOT}/activation"
install -o root -g root -m 0755 "${SCRIPT_DIR}/deploy/activate-release.py" "/usr/libexec/${APP_NAME}-activate"

render() {
  sed -e "s|@APP_NAME@|${APP_NAME}|g" -e "s|@APP_ROOT@|${APP_ROOT}|g" \
    -e "s|@SERVICE_NAME@|${SERVICE_NAME}|g" -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
    -e "s|@DEPLOY_GROUP@|${DEPLOY_GROUP}|g" -e "s|@BIND_HOST@|${BIND_HOST}|g" \
    -e "s|@PORT@|${PORT}|g" -e "s|@PYTHON@|${PYTHON}|g" "$1" > "$2"
  chmod 0644 "$2"
}
render "${SCRIPT_DIR}/deploy/rootless-activate.service.template" "/etc/systemd/system/${SERVICE_NAME}-activate.service"
render "${SCRIPT_DIR}/deploy/rootless-activate.path.template" "/etc/systemd/system/${SERVICE_NAME}-activate.path"
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}-activate.path"
echo "Rootless release activation is ready for ${DEPLOY_USER}. Log out and back in once so the new group applies."
