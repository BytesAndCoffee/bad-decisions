#!/usr/bin/env bash
# Switch the service back to a release that previously passed its health checks.
# Normally run as `sudo ./deploy.sh rollback [RELEASE_ID]`; deploy.sh checks for
# root and exports the settings below. Only the `current` symlink and the
# good-releases watermark file change: env, unit, and nginx files are not
# restored (copies live in APP_ROOT/backups).
#
# APP_ROOT/good-releases lists release IDs, oldest first, that passed every
# deploy.sh health check; its last line is the watermark. The default target is
# the newest listed release that is not current. A successful rollback drops the
# release it left, so a second rollback cannot roll forward into it.
set -euo pipefail

APP_NAME=${APP_NAME:-bad-decisions}
APP_ROOT=${APP_ROOT:-/opt/${APP_NAME}}
SERVICE_NAME=${SERVICE_NAME:-${APP_NAME}}
BIND_HOST=${BIND_HOST:-127.0.0.1}
PORT=${PORT:-8000}
RELEASES_DIR=${APP_ROOT}/releases
CURRENT_LINK=${APP_ROOT}/current
GOOD_FILE=${APP_ROOT}/good-releases
ID_PATTERN='^[0-9]{8}T[0-9]{6}Z$'

if [[ $# -gt 1 ]]; then
  echo "Usage: deploy.sh rollback [RELEASE_ID]" >&2
  exit 1
fi
EXPLICIT=$#
TARGET_ID=${1:-}

if [[ ! -L ${CURRENT_LINK} ]]; then
  echo "No current release at ${CURRENT_LINK}; nothing to roll back." >&2
  exit 1
fi
CURRENT_DIR=$(readlink -f "${CURRENT_LINK}")
CURRENT_ID=$(basename "${CURRENT_DIR}")

usable_release() {
  [[ $1 =~ ${ID_PATTERN} && -d ${RELEASES_DIR}/$1 && ! -L ${RELEASES_DIR}/$1 && -x ${RELEASES_DIR}/$1/.venv/bin/python ]]
}

if [[ ${EXPLICIT} -eq 1 ]]; then
  if ! usable_release "${TARGET_ID}"; then
    echo "Release ${TARGET_ID} is not a usable release under ${RELEASES_DIR}." >&2
    exit 1
  fi
elif [[ -f ${GOOD_FILE} ]]; then
  # Watermark: the newest release that passed its health checks, other than the current one.
  while read -r candidate; do
    if [[ ${candidate} != "${CURRENT_ID}" ]] && usable_release "${candidate}"; then TARGET_ID=${candidate}; fi
  done < "${GOOD_FILE}"
  if [[ -z ${TARGET_ID} ]]; then
    echo "No known-good release other than ${CURRENT_ID} in ${GOOD_FILE}." >&2
    echo "Name one explicitly: deploy.sh rollback RELEASE_ID" >&2
    exit 1
  fi
else
  echo "warning: ${GOOD_FILE} does not exist (host deployed before it was kept);" >&2
  echo "warning: using the newest usable release older than ${CURRENT_ID}, which is not verified good." >&2
  while read -r candidate; do
    if [[ ${candidate} < ${CURRENT_ID} ]] && usable_release "${candidate}"; then TARGET_ID=${candidate}; fi
  done < <(find "${RELEASES_DIR}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort)
  if [[ -z ${TARGET_ID} ]]; then
    echo "No earlier release than ${CURRENT_ID} to roll back to." >&2
    exit 1
  fi
fi
if [[ ${TARGET_ID} == "${CURRENT_ID}" ]]; then
  echo "Release ${TARGET_ID} is already current." >&2
  exit 1
fi

activate() {
  ln -sfn "$1" "${APP_ROOT}/current.new"
  mv -Tf "${APP_ROOT}/current.new" "${CURRENT_LINK}"
  systemctl restart "${SERVICE_NAME}"
}

healthy() {
  curl --fail --silent --show-error --retry 10 --retry-delay 1 --retry-max-time 60 --retry-connrefused \
    "http://${BIND_HOST}:${PORT}/healthz" >/dev/null
}

# Drop the release we left and make sure the one we landed on is listed.
update_watermark() {
  {
    if [[ -f ${GOOD_FILE} ]]; then grep -Fxv -e "${CURRENT_ID}" -e "${TARGET_ID}" "${GOOD_FILE}" || true; fi
    echo "${TARGET_ID}"
  } | tail -n 20 > "${GOOD_FILE}.new"
  mv -f "${GOOD_FILE}.new" "${GOOD_FILE}"
}

echo "Rolling back ${SERVICE_NAME}: ${CURRENT_ID} -> ${TARGET_ID}"
if activate "${RELEASES_DIR}/${TARGET_ID}" && healthy; then
  update_watermark || echo "warning: could not update ${GOOD_FILE}" >&2
  echo "Rollback complete: ${SERVICE_NAME} is serving ${TARGET_ID}"
  echo "Logs: journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
  exit 0
fi

echo "Release ${TARGET_ID} failed its health check; restoring ${CURRENT_ID}." >&2
activate "${CURRENT_DIR}" || true
exit 1
