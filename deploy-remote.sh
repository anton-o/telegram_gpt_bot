#!/usr/bin/env bash

set -Eeuo pipefail

readonly APP_DIR="/root/python"
readonly SERVICE_NAME="tlggptbot.service"
readonly UNIT_PATH="/etc/systemd/system/tlggptbot.service"
readonly UV_BIN="/usr/local/bin/uv"
readonly BACKUP_ROOT="/root/tlggptbot-backups/releases"
readonly UV_PYTHON_INSTALL_DIR="/opt/uv/python"
readonly UV_CACHE_DIR="/var/cache/uv"

STAGING_DIR=""
DEPLOYMENT_ID=""
NEW_APP=""
BACKUP_DIR=""
CUTOVER_STARTED=0
OLD_APP_MOVED=0
NEW_APP_MOVED=0
WAS_ENABLED=0

usage() {
    echo "Usage: deploy-remote.sh --staging-dir PATH"
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

safe_remove_dir() {
    local target="$1"
    local prefix="$2"
    case "$target" in
        "$prefix"*) [[ -d "$target" && ! -L "$target" ]] && rm -rf -- "$target" ;;
        *) echo "Refusing to remove unexpected directory: $target" >&2; return 1 ;;
    esac
}

rollback() {
    local status="$1"
    set +e
    trap - EXIT
    echo "Deployment failed; rolling back." >&2
    systemctl stop "$SERVICE_NAME"
    if ((NEW_APP_MOVED)) && [[ -d "$APP_DIR" && ! -L "$APP_DIR" ]]; then
        mv -- "$APP_DIR" "$BACKUP_DIR/failed-new"
    fi
    if ((OLD_APP_MOVED)) && [[ -d "$BACKUP_DIR/previous" && ! -L "$BACKUP_DIR/previous" ]]; then
        mv -- "$BACKUP_DIR/previous" "$APP_DIR"
    fi
    if [[ -f "$BACKUP_DIR/tlggptbot.service" ]]; then
        install -o root -g root -m 0644 "$BACKUP_DIR/tlggptbot.service" "$UNIT_PATH"
    fi
    systemctl daemon-reload
    if ((WAS_ENABLED)); then systemctl enable "$SERVICE_NAME"; else systemctl disable "$SERVICE_NAME"; fi
    systemctl start "$SERVICE_NAME"
    sleep 15
    systemctl is-active --quiet "$SERVICE_NAME" &&
        echo "Rollback succeeded." >&2 || echo "ROLLBACK FAILED; inspect $BACKUP_DIR" >&2
    exit "$status"
}

on_exit() {
    local status=$?
    if ((status != 0 && CUTOVER_STARTED)); then rollback "$status"; fi
    return "$status"
}
trap on_exit EXIT

while (($#)); do
    case "$1" in
        --staging-dir) (($# >= 2)) || fail "missing staging path"; STAGING_DIR="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done

[[ "$(id -u)" == 0 ]] || fail "deployment must run as root"
[[ "$STAGING_DIR" =~ ^/root/\.tlggptbot-deploy\.[A-Za-z0-9]+$ ]] || fail "unsafe staging path"
[[ -d "$STAGING_DIR" && ! -L "$STAGING_DIR" ]] || fail "invalid staging directory"
[[ -d "$APP_DIR" && ! -L "$APP_DIR" ]] || fail "invalid application directory"
[[ -f "$APP_DIR/bot_secrets.py" && ! -L "$APP_DIR/bot_secrets.py" ]] || fail "missing secret"
[[ -f "$UNIT_PATH" && ! -L "$UNIT_PATH" ]] || fail "invalid systemd unit"
for command_name in awk date find flock grep install journalctl readlink sha256sum \
    systemctl tee; do
    command -v "$command_name" >/dev/null || fail "missing command: $command_name"
done
for path in "$BACKUP_ROOT" "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR"; do
    [[ ! -L "$path" ]] || fail "refusing symbolic link: $path"
done

exec 9>/run/lock/tlggptbot-deploy.lock
flock -n 9 || fail "another deployment is running"
exec > >(tee -a "$STAGING_DIR/deployment.log") 2>&1

(
    cd "$STAGING_DIR"
    sha256sum -c SHA256SUMS
)

GIT_COMMIT=""
LOCK_SHA256=""
while IFS='=' read -r key value; do
    case "$key" in
        DEPLOYMENT_ID) DEPLOYMENT_ID="$value" ;;
        GIT_COMMIT) GIT_COMMIT="$value" ;;
        LOCK_SHA256) LOCK_SHA256="$value" ;;
        ""|\#*) ;;
        *) fail "unknown deployment metadata key: $key" ;;
    esac
done <"$STAGING_DIR/DEPLOYMENT_METADATA"
[[ "$DEPLOYMENT_ID" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] || fail "invalid deployment ID"
[[ "$GIT_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "invalid Git commit"
[[ "$LOCK_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "invalid lock hash"
[[ "$(sha256sum "$STAGING_DIR/uv.lock" | awk '{print $1}')" == "$LOCK_SHA256" ]] ||
    fail "lockfile hash mismatch"

UV_VERSION=""
PYTHON_VERSION=""
while IFS='=' read -r key value; do
    case "$key" in
        UV_VERSION) UV_VERSION="$value" ;;
        PYTHON_VERSION) PYTHON_VERSION="$value" ;;
        ""|\#*) ;;
        *) fail "unknown runtime version key: $key" ;;
    esac
done <"$STAGING_DIR/deploy/runtime-versions.conf"
[[ "$($UV_BIN --version | awk '{print $2}')" == "$UV_VERSION" ]] ||
    fail "uv pin drift requires an explicit runtime upgrade"
[[ "$($APP_DIR/.venv/bin/python --version)" == "Python $PYTHON_VERSION" ]] ||
    fail "Python pin drift requires an explicit runtime upgrade"

if [[ -f "$APP_DIR/.deployment-info" ]]; then
    active_commit="$(awk -F= '$1 == "GIT_COMMIT" {print $2}' "$APP_DIR/.deployment-info")"
    active_lock="$(awk -F= '$1 == "LOCK_SHA256" {print $2}' "$APP_DIR/.deployment-info")"
    if [[ "$active_commit" == "$GIT_COMMIT" && "$active_lock" == "$LOCK_SHA256" ]]; then
        systemctl is-active --quiet "$SERVICE_NAME" || fail "matching release is inactive"
        echo "Release already active; nothing to do."
        safe_remove_dir "$STAGING_DIR" "/root/.tlggptbot-deploy." ||
            echo "WARNING: could not remove staging directory: $STAGING_DIR" >&2
        exit 0
    fi
fi

systemctl is-active --quiet "$SERVICE_NAME" || fail "current service is inactive"
NEW_APP="${APP_DIR}.new.${DEPLOYMENT_ID}"
[[ ! -e "$NEW_APP" ]] || fail "staged application already exists"
mkdir -m 0700 "$NEW_APP"
for file in .python-version backend_handlers.py help.py main.py pyproject.toml \
    utils_handlers.py uv.lock white_lists.py; do
    install -o root -g root -m 0644 "$STAGING_DIR/$file" "$NEW_APP/$file"
done
install -o root -g root -m 0600 "$APP_DIR/bot_secrets.py" "$NEW_APP/bot_secrets.py"

export UV_PYTHON_INSTALL_DIR UV_CACHE_DIR
(
    cd "$NEW_APP"
    "$UV_BIN" sync --locked --no-dev --python "$PYTHON_VERSION"
    [[ "$(.venv/bin/python --version)" == "Python $PYTHON_VERSION" ]]
    .venv/bin/python -m compileall -q \
        backend_handlers.py help.py main.py utils_handlers.py white_lists.py
    .venv/bin/python -c "import main"
    "$UV_BIN" pip check --python .venv/bin/python
)

BACKUP_DIR="$BACKUP_ROOT/$DEPLOYMENT_ID"
[[ ! -e "$BACKUP_DIR" ]] || fail "release backup already exists"
[[ ! -e "$BACKUP_ROOT" || (-d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT") ]] ||
    fail "invalid release backup root"
install -d -o root -g root -m 0700 "$BACKUP_ROOT"
mkdir -m 0700 "$BACKUP_DIR"
install -o root -g root -m 0644 "$UNIT_PATH" "$BACKUP_DIR/tlggptbot.service"
systemctl status "$SERVICE_NAME" --no-pager >"$BACKUP_DIR/service-before.txt" || true
if systemctl is-enabled --quiet "$SERVICE_NAME"; then WAS_ENABLED=1; fi
printf '%s\n' "$WAS_ENABLED" >"$BACKUP_DIR/was-enabled.txt"

CUTOVER_STARTED=1
systemctl stop "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME" && fail "service did not stop"
mv -- "$APP_DIR" "$BACKUP_DIR/previous"
OLD_APP_MOVED=1
mv -- "$NEW_APP" "$APP_DIR"
NEW_APP_MOVED=1
install -o root -g root -m 0644 "$STAGING_DIR/deploy/tlggptbot.service" "$UNIT_PATH"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
health_start="$(date '+%Y-%m-%d %H:%M:%S')"
systemctl start "$SERVICE_NAME"
sleep 15

systemctl is-active --quiet "$SERVICE_NAME" || fail "new service is inactive"
main_pid="$(systemctl show --property MainPID --value "$SERVICE_NAME")"
[[ "$main_pid" =~ ^[1-9][0-9]*$ ]] || fail "new service has no main PID"
[[ "$(readlink -f "/proc/$main_pid/cwd")" == "$APP_DIR" ]] || fail "unexpected working directory"
exec_start="$(systemctl show --property ExecStart --value "$SERVICE_NAME")"
[[ "$exec_start" == *"/root/python/.venv/bin/python /root/python/main.py"* ]] ||
    fail "unexpected service command"
if journalctl -u "$SERVICE_NAME" --since "$health_start" --no-pager |
    grep -Eq 'Traceback|ModuleNotFoundError|ImportError|Start request repeated too quickly'; then
    fail "service journal contains startup errors"
fi

cat >"$APP_DIR/.deployment-info" <<MARKER
DEPLOYMENT_ID=$DEPLOYMENT_ID
GIT_COMMIT=$GIT_COMMIT
LOCK_SHA256=$LOCK_SHA256
UV_VERSION=$UV_VERSION
PYTHON_VERSION=$PYTHON_VERSION
DEPLOYED_AT=$(date --iso-8601=seconds)
MARKER
chmod 0600 "$APP_DIR/.deployment-info"
CUTOVER_STARTED=0
cp -p -- "$STAGING_DIR/deployment.log" "$BACKUP_DIR/deployment.log" ||
    echo "WARNING: could not retain the deployment log" >&2

while IFS= read -r old_backup; do
    [[ "$old_backup" == "$BACKUP_DIR" ]] && continue
    if [[ ! "$old_backup" =~ ^/root/tlggptbot-backups/releases/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]]; then
        echo "WARNING: skipped unsafe release backup path: $old_backup" >&2
        continue
    fi
    if [[ ! -d "$old_backup" || -L "$old_backup" ]]; then
        echo "WARNING: skipped invalid old release backup: $old_backup" >&2
        continue
    fi
    if ! safe_remove_dir "$old_backup" "/root/tlggptbot-backups/releases/"; then
        echo "WARNING: could not remove old release backup: $old_backup" >&2
    fi
done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -print)

"$UV_BIN" cache prune --ci || echo "WARNING: uv cache pruning failed" >&2
echo "Deployment completed: $GIT_COMMIT"
echo "Rollback backup retained: $BACKUP_DIR"
safe_remove_dir "$STAGING_DIR" "/root/.tlggptbot-deploy." ||
    echo "WARNING: could not remove staging directory: $STAGING_DIR" >&2
