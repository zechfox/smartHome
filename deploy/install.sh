#!/usr/bin/env bash
#
# Install smartHome for a dedicated "smartHome" user as a systemd user service.
#
# The application is installed to $HOME/.smartHome of the service user, its
# dependencies into a virtualenv in the same directory, and a systemd *user*
# unit runs it at boot (lingering is enabled, so no login is required).
#
# Usage:
#     sudo ./deploy/install.sh
#
# Re-running the script upgrades the application files and dependencies. An
# existing config.yaml is preserved.
set -euo pipefail

SERVICE_NAME="smart-home"
SERVICE_USER="smartHome"
INSTALL_DIRNAME=".smartHome"
DEFAULT_HOME="/home/${SERVICE_USER}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
UNIT_SOURCE="${SCRIPT_DIR}/${SERVICE_NAME}.user.service"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "run this script as root, e.g. sudo $0"
command -v systemctl >/dev/null 2>&1 || die "systemd (systemctl) is required"
command -v loginctl >/dev/null 2>&1 || die "systemd (loginctl) is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "Python 3.9 or newer is required (found: $(python3 --version 2>&1))"
python3 -c 'import venv' >/dev/null 2>&1 \
    || die "the python3 venv module is missing (Debian/Ubuntu: apt install python3-venv)"
[[ -d /run/systemd/system ]] \
    || die "systemd is not running as PID 1; systemd user services need a booted systemd host"
[[ -f "${SOURCE_DIR}/config.yaml" ]] || die "config.yaml not found in ${SOURCE_DIR}"
[[ -f "${UNIT_SOURCE}" ]] || die "unit file not found: ${UNIT_SOURCE}"

# --- dedicated user ---------------------------------------------------------
if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    log "user '${SERVICE_USER}' already exists"
else
    command -v useradd >/dev/null 2>&1 || die "useradd is required to create '${SERVICE_USER}'"
    nologin="$(command -v nologin || echo /bin/false)"
    log "creating user '${SERVICE_USER}' (home ${DEFAULT_HOME})"
    useradd --create-home --home-dir "${DEFAULT_HOME}" --shell "${nologin}" \
        --user-group "${SERVICE_USER}"
fi

SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
SERVICE_UID="$(id -u "${SERVICE_USER}")"
if [[ "${SERVICE_UID}" -lt 1000 ]]; then
    warn "user '${SERVICE_USER}' has a system UID (${SERVICE_UID});"
    warn "systemd-logind usually will not start a user manager for system users."
fi
SERVICE_HOME="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
[[ -n "${SERVICE_HOME}" ]] || die "cannot determine home directory of '${SERVICE_USER}'"
if [[ ! -d "${SERVICE_HOME}" ]]; then
    log "creating home directory ${SERVICE_HOME}"
    install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${SERVICE_HOME}"
fi
INSTALL_DIR="${SERVICE_HOME}/${INSTALL_DIRNAME}"

# --- application files ------------------------------------------------------
log "installing application into ${INSTALL_DIR}"
install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${INSTALL_DIR}"
rm -rf "${INSTALL_DIR}/app"
cp -a "${SOURCE_DIR}/app" "${INSTALL_DIR}/app"
cp -a "${SOURCE_DIR}/requirements.txt" "${INSTALL_DIR}/requirements.txt"
cp -a "${SOURCE_DIR}/README.md" "${INSTALL_DIR}/README.md"
find "${INSTALL_DIR}/app" -type d -name __pycache__ -prune -exec rm -rf {} +

if [[ -f "${INSTALL_DIR}/config.yaml" ]]; then
    log "keeping existing configuration ${INSTALL_DIR}/config.yaml"
else
    cp -a "${SOURCE_DIR}/config.yaml" "${INSTALL_DIR}/config.yaml"
    log "installed default configuration ${INSTALL_DIR}/config.yaml"
fi
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"

# --- helper to run commands as the service user -----------------------------
run_as_service_user() {
    local uid runtime
    uid="$(id -u "${SERVICE_USER}")"
    runtime="/run/user/${uid}"
    if command -v runuser >/dev/null 2>&1; then
        local -a env_args=("HOME=${SERVICE_HOME}" "XDG_RUNTIME_DIR=${runtime}")
        if [[ -S "${runtime}/bus" ]]; then
            env_args+=("DBUS_SESSION_BUS_ADDRESS=unix:path=${runtime}/bus")
        fi
        runuser -u "${SERVICE_USER}" -- env "${env_args[@]}" "$@"
    elif [[ -S "${runtime}/bus" ]]; then
        # shellcheck disable=SC2086
        su -s /bin/sh -c \
            "exec env HOME=$(printf '%q' "${SERVICE_HOME}") XDG_RUNTIME_DIR=${runtime} DBUS_SESSION_BUS_ADDRESS=unix:path=${runtime}/bus $(printf '%q ' "$@")" \
            "${SERVICE_USER}"
    else
        # shellcheck disable=SC2086
        su -s /bin/sh -c \
            "exec env HOME=$(printf '%q' "${SERVICE_HOME}") XDG_RUNTIME_DIR=${runtime} $(printf '%q ' "$@")" \
            "${SERVICE_USER}"
    fi
}

# --- virtualenv and dependencies --------------------------------------------
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
    log "creating virtualenv ${INSTALL_DIR}/.venv"
    run_as_service_user python3 -m venv "${INSTALL_DIR}/.venv"
fi
log "installing Python dependencies"
run_as_service_user "${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
run_as_service_user "${INSTALL_DIR}/.venv/bin/pip" install --quiet \
    -r "${INSTALL_DIR}/requirements.txt"

# --- systemd user service ---------------------------------------------------
UNIT_DIR="${SERVICE_HOME}/.config/systemd/user"
log "installing systemd user unit ${UNIT_DIR}/${SERVICE_NAME}.service"
install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${UNIT_DIR}"
install -m 0644 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${UNIT_SOURCE}" "${UNIT_DIR}/${SERVICE_NAME}.service"

log "enabling linger for '${SERVICE_USER}' (start at boot without login)"
loginctl enable-linger "${SERVICE_USER}"

uid="${SERVICE_UID}"
runtime_dir="/run/user/${uid}"
if [[ "$(loginctl show-user "${SERVICE_USER}" -p Linger --value 2>/dev/null)" != "yes" ]]; then
    die "could not enable lingering for '${SERVICE_USER}'"
fi

log "waiting for the systemd user manager of '${SERVICE_USER}'"
for _ in {1..30}; do
    if [[ -S "${runtime_dir}/systemd/private" ]]; then
        break
    fi
    sleep 1
done

if [[ ! -S "${runtime_dir}/systemd/private" ]]; then
    warn "the systemd user manager did not start within 30s; diagnostics:"
    loginctl show-user "${SERVICE_USER}" --no-pager || true
    systemctl status "user@${uid}.service" --no-pager || true
    journalctl -b -u "user@${uid}.service" --no-pager -n 30 || true
    die "systemd user manager for '${SERVICE_USER}' is not running (see diagnostics above)"
fi

run_as_service_user systemctl --user daemon-reload
run_as_service_user systemctl --user enable "${SERVICE_NAME}.service"
run_as_service_user systemctl --user restart "${SERVICE_NAME}.service"

sleep 2
run_as_service_user systemctl --user status "${SERVICE_NAME}.service" --no-pager || true

cat <<EOF

smartHome is installed.

  service user : ${SERVICE_USER}
  install dir  : ${INSTALL_DIR}
  config file  : ${INSTALL_DIR}/config.yaml
  unit file    : ${UNIT_DIR}/${SERVICE_NAME}.service

Manage the service as the service user:

  sudo -u ${SERVICE_USER} env XDG_RUNTIME_DIR=${runtime_dir} \\
      systemctl --user {status|restart|stop} ${SERVICE_NAME}

Follow the logs:

  sudo journalctl _UID=${uid} -f

EOF

if grep -q "change-me-to-a-long-random-string" "${INSTALL_DIR}/config.yaml"; then
    warn "config.yaml still contains the placeholder api_token."
    warn "set a strong token in ${INSTALL_DIR}/config.yaml and restart the service."
fi
