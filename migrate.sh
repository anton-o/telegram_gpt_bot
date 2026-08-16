#!/usr/bin/env bash

set -Eeuo pipefail

readonly APP_DIR="/root/python"
readonly SERVICE_NAME="tlggptbot.service"
readonly NEW_UNIT_PATH="/etc/systemd/system/tlggptbot.service"
readonly BACKUP_ROOT="/root/tlggptbot-backups"
readonly UV_BIN="/usr/local/bin/uv"
readonly UV_PYTHON_INSTALL_DIR="/opt/uv/python"
readonly UV_CACHE_DIR="/var/cache/uv"

STAGING_DIR=""
MIGRATION_ID=""
NEW_APP=""
BACKUP_DIR=""
MIGRATION_LOG=""
CUTOVER_STARTED=0
OLD_APP_MOVED=0
NEW_APP_MOVED=0
OLD_UNIT_BACKED_UP=0
OLD_UNIT_PATH=""
WAS_ENABLED=0
UV_TEMP_DIR=""
UV_CANDIDATE=""

usage() {
    cat <<'EOF'
Usage: migrate.sh --staging-dir PATH --migration-id ID

Runs the staged one-time uv migration. This script must run as root.
EOF
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

safe_remove_temp_dir() {
    local target="$1"
    local expected_prefix="$2"
    [[ -n "$target" ]] || return 0
    case "$target" in
        "$expected_prefix"*)
            [[ -d "$target" && ! -L "$target" ]] && rm -rf -- "$target"
            ;;
        *)
            echo "Refusing to remove unexpected path: $target" >&2
            ;;
    esac
}

remove_uv_candidate() {
    if [[ -n "$UV_CANDIDATE" && "$UV_CANDIDATE" == /usr/local/bin/.uv-migrate-* ]]; then
        [[ ! -L "$UV_CANDIDATE" ]] && rm -f -- "$UV_CANDIDATE"
    fi
}

rollback() {
    local original_status="$1"
    set +e
    trap - EXIT
    echo "Migration failed; starting automatic rollback." >&2

    systemctl stop "$SERVICE_NAME"

    if ((NEW_APP_MOVED)) && [[ -d "$APP_DIR" && ! -L "$APP_DIR" ]]; then
        if [[ ! -e "$BACKUP_DIR/failed-new" ]]; then
            mv -- "$APP_DIR" "$BACKUP_DIR/failed-new"
        else
            echo "Cannot preserve failed new app: destination already exists." >&2
        fi
    fi

    if ((OLD_APP_MOVED)) && [[ -d "$BACKUP_DIR/python" && ! -L "$BACKUP_DIR/python" ]]; then
        if [[ ! -e "$APP_DIR" ]]; then
            mv -- "$BACKUP_DIR/python" "$APP_DIR"
        else
            echo "Cannot restore old app: $APP_DIR already exists." >&2
        fi
    fi

    if ((OLD_UNIT_BACKED_UP)) && [[ -f "$BACKUP_DIR/tlggptbot.service" ]]; then
        if [[ "$OLD_UNIT_PATH" == "$NEW_UNIT_PATH" ]]; then
            install -o root -g root -m 0644 \
                "$BACKUP_DIR/tlggptbot.service" "$NEW_UNIT_PATH"
        else
            rm -f -- "$NEW_UNIT_PATH"
        fi
    fi

    systemctl daemon-reload
    if ((WAS_ENABLED)); then
        systemctl enable "$SERVICE_NAME"
    else
        systemctl disable "$SERVICE_NAME"
    fi
    systemctl start "$SERVICE_NAME"
    sleep 15

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "Rollback succeeded; the legacy service is active." >&2
    else
        echo "ROLLBACK FAILED. Manual recovery:" >&2
        echo "  systemctl stop $SERVICE_NAME" >&2
        echo "  mv $BACKUP_DIR/python $APP_DIR" >&2
        if [[ "$OLD_UNIT_PATH" == "$NEW_UNIT_PATH" ]]; then
            echo "  install -m 0644 $BACKUP_DIR/tlggptbot.service $NEW_UNIT_PATH" >&2
        else
            echo "  rm -f $NEW_UNIT_PATH  # restores the unit from $OLD_UNIT_PATH" >&2
        fi
        echo "  systemctl daemon-reload && systemctl start $SERVICE_NAME" >&2
    fi

    [[ -n "$MIGRATION_LOG" && -f "$MIGRATION_LOG" ]] &&
        cp -p -- "$MIGRATION_LOG" "$BACKUP_DIR/migration.log"
    safe_remove_temp_dir "$UV_TEMP_DIR" "/root/.uv-install."
    remove_uv_candidate
    exit "$original_status"
}

on_exit() {
    local status=$?
    if ((status != 0 && CUTOVER_STARTED)); then
        rollback "$status"
    fi
    safe_remove_temp_dir "$UV_TEMP_DIR" "/root/.uv-install."
    remove_uv_candidate
    return "$status"
}

trap on_exit EXIT

while (($#)); do
    case "$1" in
        --staging-dir)
            (($# >= 2)) || fail "--staging-dir requires a path"
            STAGING_DIR="$2"
            shift 2
            ;;
        --migration-id)
            (($# >= 2)) || fail "--migration-id requires a value"
            MIGRATION_ID="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "unknown argument: $1"
            ;;
    esac
done

[[ "$(id -u)" == "0" ]] || fail "migration must run as root"
[[ "$STAGING_DIR" =~ ^/root/\.tlggptbot-migrate\.[A-Za-z0-9]+$ ]] ||
    fail "unsafe staging directory"
[[ -d "$STAGING_DIR" && ! -L "$STAGING_DIR" ]] || fail "invalid staging directory"
[[ "$MIGRATION_ID" =~ ^[A-Za-z0-9._-]+$ ]] || fail "invalid migration ID"
[[ -d "$APP_DIR" && ! -L "$APP_DIR" ]] || fail "invalid application directory"
[[ -f "$APP_DIR/bot_secrets.py" && ! -L "$APP_DIR/bot_secrets.py" ]] ||
    fail "missing regular bot_secrets.py"
[[ -d /root/ve_tlg && ! -L /root/ve_tlg ]] || fail "missing legacy rollback venv"
for stable_path in "$BACKUP_ROOT" /opt/uv "$UV_PYTHON_INSTALL_DIR" \
    "$UV_CACHE_DIR" "$NEW_UNIT_PATH"; do
    [[ ! -L "$stable_path" ]] || fail "refusing symbolic link: $stable_path"
done
[[ ! -e "$UV_BIN" || (-f "$UV_BIN" && ! -L "$UV_BIN") ]] ||
    fail "uv executable path is not a regular file"

OLD_UNIT_PATH="$(systemctl show --property FragmentPath --value "$SERVICE_NAME")"
case "$OLD_UNIT_PATH" in
    /etc/systemd/system/tlggptbot.service|/lib/systemd/system/tlggptbot.service|/usr/lib/systemd/system/tlggptbot.service) ;;
    *) fail "unexpected systemd unit path: $OLD_UNIT_PATH" ;;
esac
[[ -f "$OLD_UNIT_PATH" && ! -L "$OLD_UNIT_PATH" ]] ||
    fail "missing regular systemd unit"

for command_name in curl tar sha256sum systemctl install flock mktemp readlink \
    journalctl grep tee; do
    command -v "$command_name" >/dev/null || fail "missing command: $command_name"
done

exec 9>/run/lock/tlggptbot-migrate.lock
flock -n 9 || fail "another migration is already running"

MIGRATION_LOG="$STAGING_DIR/migration.log"
touch "$MIGRATION_LOG"
chmod 0600 "$MIGRATION_LOG"
exec > >(tee -a "$MIGRATION_LOG") 2>&1

echo "Starting migration $MIGRATION_ID"
(
    cd "$STAGING_DIR"
    sha256sum -c SHA256SUMS
)

GIT_COMMIT=""
EXPECTED_LOCK_HASH=""
while IFS='=' read -r key value; do
    case "$key" in
        GIT_COMMIT) GIT_COMMIT="$value" ;;
        LOCK_SHA256) EXPECTED_LOCK_HASH="$value" ;;
        ""|\#*) ;;
        *) fail "unknown migration metadata key: $key" ;;
    esac
done <"$STAGING_DIR/MIGRATION_METADATA"
[[ "$GIT_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "invalid Git commit metadata"
[[ "$EXPECTED_LOCK_HASH" =~ ^[0-9a-f]{64}$ ]] || fail "invalid lock hash metadata"
actual_lock_hash="$(sha256sum "$STAGING_DIR/uv.lock" | awk '{print $1}')"
[[ "$actual_lock_hash" == "$EXPECTED_LOCK_HASH" ]] || fail "uv.lock hash mismatch"

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
[[ "$UV_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "invalid uv version"
[[ "$PYTHON_VERSION" =~ ^3\.11\.[0-9]+$ ]] || fail "invalid Python version"

if [[ -f "$APP_DIR/.uv-migration-complete" ]]; then
    active_commit="$(awk -F= '$1 == "GIT_COMMIT" {print $2}' "$APP_DIR/.uv-migration-complete")"
    active_lock="$(awk -F= '$1 == "LOCK_SHA256" {print $2}' "$APP_DIR/.uv-migration-complete")"
    if [[ "$active_commit" == "$GIT_COMMIT" && "$active_lock" == "$EXPECTED_LOCK_HASH" ]]; then
        systemctl is-active --quiet "$SERVICE_NAME" ||
            fail "matching migration marker exists but service is inactive"
        echo "Migration is already active; nothing to do."
        exit 0
    fi
fi

for disk_path in /root /opt /var; do
    available_kb="$(df -Pk "$disk_path" | awk 'NR == 2 {print $4}')"
    [[ "$available_kb" =~ ^[0-9]+$ && "$available_kb" -ge 2097152 ]] ||
        fail "at least 2 GiB free space is required on $disk_path"
done
systemctl is-active --quiet "$SERVICE_NAME" || fail "legacy service must be active"

case "$(uname -m)" in
    x86_64) uv_target="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) uv_target="aarch64-unknown-linux-gnu" ;;
    *) fail "unsupported server architecture: $(uname -m)" ;;
esac

current_uv_version=""
if [[ -x "$UV_BIN" ]]; then
    current_uv_version="$($UV_BIN --version | awk '{print $2}')"
fi
if [[ "$current_uv_version" != "$UV_VERSION" ]]; then
    UV_TEMP_DIR="$(mktemp -d /root/.uv-install.XXXXXXXX)"
    [[ -d "$UV_TEMP_DIR" && ! -L "$UV_TEMP_DIR" ]] || fail "invalid uv temp directory"
    uv_archive="uv-${uv_target}.tar.gz"
    uv_url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${uv_archive}"
    curl --fail --location --silent --show-error "$uv_url" \
        --output "$UV_TEMP_DIR/$uv_archive"
    curl --fail --location --silent --show-error "${uv_url}.sha256" \
        --output "$UV_TEMP_DIR/${uv_archive}.sha256"
    (
        cd "$UV_TEMP_DIR"
        sha256sum -c "${uv_archive}.sha256"
        tar -xzf "$uv_archive"
    )
    [[ -x "$UV_TEMP_DIR/uv-${uv_target}/uv" ]] || fail "uv binary missing from archive"
    UV_CANDIDATE="/usr/local/bin/.uv-migrate-${MIGRATION_ID}"
    [[ ! -e "$UV_CANDIDATE" ]] || fail "uv install candidate already exists"
    install -o root -g root -m 0755 \
        "$UV_TEMP_DIR/uv-${uv_target}/uv" "$UV_CANDIDATE"
    [[ "$($UV_CANDIDATE --version | awk '{print $2}')" == "$UV_VERSION" ]] ||
        fail "downloaded uv version does not match pin"
    mv -f -- "$UV_CANDIDATE" "$UV_BIN"
    UV_CANDIDATE=""
fi
[[ "$($UV_BIN --version | awk '{print $2}')" == "$UV_VERSION" ]] ||
    fail "installed uv version does not match pin"

install -d -o root -g root -m 0755 "$UV_PYTHON_INSTALL_DIR"
install -d -o root -g root -m 0755 "$UV_CACHE_DIR"
export UV_PYTHON_INSTALL_DIR UV_CACHE_DIR
"$UV_BIN" python install --no-bin "$PYTHON_VERSION"

NEW_APP="${APP_DIR}.new.${MIGRATION_ID}"
[[ ! -e "$NEW_APP" ]] || fail "staged application already exists: $NEW_APP"
mkdir -m 0700 "$NEW_APP"

runtime_files=(
    .python-version
    backend_handlers.py
    help.py
    main.py
    pyproject.toml
    utils_handlers.py
    uv.lock
    white_lists.py
)
for runtime_file in "${runtime_files[@]}"; do
    install -o root -g root -m 0644 \
        "$STAGING_DIR/$runtime_file" "$NEW_APP/$runtime_file"
done
install -o root -g root -m 0600 "$APP_DIR/bot_secrets.py" "$NEW_APP/bot_secrets.py"

(
    cd "$NEW_APP"
    "$UV_BIN" sync --locked --no-dev --python "$PYTHON_VERSION"
    [[ "$(.venv/bin/python --version)" == "Python $PYTHON_VERSION" ]]
    .venv/bin/python -m compileall -q \
        backend_handlers.py help.py main.py utils_handlers.py white_lists.py
    .venv/bin/python -c "import main"
    "$UV_BIN" pip check --python .venv/bin/python
)

BACKUP_DIR="$BACKUP_ROOT/$MIGRATION_ID"
[[ ! -e "$BACKUP_DIR" ]] || fail "backup already exists: $BACKUP_DIR"
[[ ! -e "$BACKUP_ROOT" || (-d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT") ]] ||
    fail "invalid backup root"
install -d -o root -g root -m 0700 "$BACKUP_ROOT"
mkdir -m 0700 "$BACKUP_DIR"
systemctl status "$SERVICE_NAME" --no-pager >"$BACKUP_DIR/service-before.txt" || true
install -o root -g root -m 0644 "$OLD_UNIT_PATH" "$BACKUP_DIR/tlggptbot.service"
OLD_UNIT_BACKED_UP=1
printf '%s\n' "$OLD_UNIT_PATH" >"$BACKUP_DIR/unit-path.txt"
sha256sum "$APP_DIR/bot_secrets.py" >"$BACKUP_DIR/bot-secrets.sha256"
if systemctl is-enabled --quiet "$SERVICE_NAME"; then
    WAS_ENABLED=1
fi
printf '%s\n' "$WAS_ENABLED" >"$BACKUP_DIR/was-enabled.txt"

CUTOVER_STARTED=1
systemctl stop "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME" && fail "legacy service did not stop"

mv -- "$APP_DIR" "$BACKUP_DIR/python"
OLD_APP_MOVED=1
mv -- "$NEW_APP" "$APP_DIR"
NEW_APP_MOVED=1
install -o root -g root -m 0644 \
    "$STAGING_DIR/deploy/tlggptbot.service" "$NEW_UNIT_PATH"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
health_start="$(date --iso-8601=seconds)"
systemctl start "$SERVICE_NAME"
sleep 15

systemctl is-active --quiet "$SERVICE_NAME" || fail "new service is inactive"
main_pid="$(systemctl show --property MainPID --value "$SERVICE_NAME")"
[[ "$main_pid" =~ ^[1-9][0-9]*$ ]] || fail "new service has no main PID"
exec_start="$(systemctl show --property ExecStart --value "$SERVICE_NAME")"
[[ "$exec_start" == *"/root/python/.venv/bin/python /root/python/main.py"* ]] ||
    fail "systemd is not using the uv project environment"
[[ "$(readlink -f "/proc/$main_pid/cwd")" == "$APP_DIR" ]] ||
    fail "new service has an unexpected working directory"
if journalctl -u "$SERVICE_NAME" --since "$health_start" --no-pager |
    grep -Eq 'Traceback|ModuleNotFoundError|ImportError|Start request repeated too quickly'; then
    fail "new service journal contains startup errors"
fi

cat >"$APP_DIR/.uv-migration-complete" <<MARKER
MIGRATION_ID=$MIGRATION_ID
GIT_COMMIT=$GIT_COMMIT
LOCK_SHA256=$EXPECTED_LOCK_HASH
UV_VERSION=$UV_VERSION
PYTHON_VERSION=$PYTHON_VERSION
COMPLETED_AT=$(date --iso-8601=seconds)
MARKER
chmod 0600 "$APP_DIR/.uv-migration-complete"
cp -p -- "$MIGRATION_LOG" "$BACKUP_DIR/migration.log"

CUTOVER_STARTED=0
safe_remove_temp_dir "$UV_TEMP_DIR" "/root/.uv-install."
UV_TEMP_DIR=""
echo "Migration completed successfully."
echo "Active commit: $GIT_COMMIT"
echo "Backup retained at: $BACKUP_DIR"
echo "Legacy environment retained at: /root/ve_tlg"

safe_remove_temp_dir "$STAGING_DIR" "/root/.tlggptbot-migrate."
