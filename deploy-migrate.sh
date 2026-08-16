#!/usr/bin/env bash

set -Eeuo pipefail

readonly EXPECTED_APP_DIR="/root/python"
readonly EXPECTED_SERVICE="tlggptbot.service"
readonly DEFAULT_CONFIG="./deploy_config.cfg"

CONFIG_FILE="$DEFAULT_CONFIG"
PREFLIGHT_ONLY=0
LOCAL_BUNDLE=""
REMOTE_STAGING=""
REMOTE_TARGET=""

usage() {
    cat <<'EOF'
Usage: ./deploy-migrate.sh [--config PATH] [--preflight]

Uploads and runs the one-time uv migration on the configured server.
--preflight performs validation without uploading or changing server state.
EOF
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

cleanup_local_bundle() {
    if [[ -n "$LOCAL_BUNDLE" ]]; then
        case "$LOCAL_BUNDLE" in
            "${TMPDIR:-/tmp}"/tlggptbot-deploy.*)
                [[ -d "$LOCAL_BUNDLE" && ! -L "$LOCAL_BUNDLE" ]] &&
                    rm -rf -- "$LOCAL_BUNDLE"
                ;;
        esac
    fi
}

trap cleanup_local_bundle EXIT

while (($#)); do
    case "$1" in
        --config)
            (($# >= 2)) || fail "--config requires a path"
            CONFIG_FILE="$2"
            shift 2
            ;;
        --preflight)
            PREFLIGHT_ONLY=1
            shift
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

[[ -f "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]] ||
    fail "configuration must be a regular file: $CONFIG_FILE"

REMOTE_HOST=""
REMOTE_USER=""
SSH_KEY_PATH=""
REMOTE_APP_DIR=""
SERVICE_NAME=""

set_config_value() {
    local key="$1"
    local value="$2"
    [[ -n "$value" ]] || fail "empty configuration value: $key"

    case "$key" in
        REMOTE_HOST)
            [[ -z "$REMOTE_HOST" ]] || fail "duplicate configuration key: $key"
            REMOTE_HOST="$value"
            ;;
        REMOTE_USER)
            [[ -z "$REMOTE_USER" ]] || fail "duplicate configuration key: $key"
            REMOTE_USER="$value"
            ;;
        SSH_KEY_PATH)
            [[ -z "$SSH_KEY_PATH" ]] || fail "duplicate configuration key: $key"
            SSH_KEY_PATH="$value"
            ;;
        REMOTE_APP_DIR)
            [[ -z "$REMOTE_APP_DIR" ]] || fail "duplicate configuration key: $key"
            REMOTE_APP_DIR="$value"
            ;;
        SERVICE_NAME)
            [[ -z "$SERVICE_NAME" ]] || fail "duplicate configuration key: $key"
            SERVICE_NAME="$value"
            ;;
        *)
            fail "unknown configuration key: $key"
            ;;
    esac
}

while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || fail "invalid configuration line: $line"
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Z_]+$ ]] || fail "invalid configuration key: $key"
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] ||
        fail "invalid newline in configuration value: $key"
    set_config_value "$key" "$value"
done <"$CONFIG_FILE"

[[ "$REMOTE_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || fail "invalid REMOTE_HOST"
[[ "$REMOTE_USER" == "root" ]] || fail "REMOTE_USER must be root"
[[ "$REMOTE_APP_DIR" == "$EXPECTED_APP_DIR" ]] ||
    fail "REMOTE_APP_DIR must be $EXPECTED_APP_DIR"
[[ "$SERVICE_NAME" == "$EXPECTED_SERVICE" ]] ||
    fail "SERVICE_NAME must be $EXPECTED_SERVICE"

case "$SSH_KEY_PATH" in
    "~/"*) SSH_KEY_PATH="${HOME:?}/${SSH_KEY_PATH#\~/}" ;;
esac
[[ "$SSH_KEY_PATH" == /* ]] || fail "SSH_KEY_PATH must be absolute or start with ~/"
[[ "$SSH_KEY_PATH" =~ ^/[A-Za-z0-9._/-]+$ ]] ||
    fail "SSH_KEY_PATH contains unsupported characters"
[[ -f "$SSH_KEY_PATH" && ! -L "$SSH_KEY_PATH" ]] ||
    fail "SSH key must be a regular file: $SSH_KEY_PATH"

REMOTE_TARGET="${REMOTE_USER}@${REMOTE_HOST}"
readonly SSH_OPTIONS=(-i "$SSH_KEY_PATH" -o BatchMode=yes)

for command_name in ssh rsync uv make git; do
    command -v "$command_name" >/dev/null || fail "missing command: $command_name"
done

checksum_command=""
if command -v sha256sum >/dev/null; then
    checksum_command="sha256sum"
elif command -v shasum >/dev/null; then
    checksum_command="shasum -a 256"
else
    fail "sha256sum or shasum is required"
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR"

[[ -f deploy/runtime-versions.conf ]] || fail "missing runtime version config"
UV_VERSION=""
PYTHON_VERSION=""
while IFS='=' read -r key value; do
    case "$key" in
        UV_VERSION) UV_VERSION="$value" ;;
        PYTHON_VERSION) PYTHON_VERSION="$value" ;;
        ""|\#*) ;;
        *) fail "unknown runtime version key: $key" ;;
    esac
done <deploy/runtime-versions.conf

[[ "$UV_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "invalid UV_VERSION"
[[ "$PYTHON_VERSION" =~ ^3\.11\.[0-9]+$ ]] || fail "invalid PYTHON_VERSION"
actual_uv_version="$(uv --version | awk '{print $2}')"
[[ "$actual_uv_version" == "$UV_VERSION" ]] ||
    fail "expected uv $UV_VERSION, found $actual_uv_version"

required_files=(
    .python-version
    backend_handlers.py
    help.py
    main.py
    migrate.sh
    pyproject.toml
    utils_handlers.py
    uv.lock
    white_lists.py
    deploy/runtime-versions.conf
    deploy/tlggptbot.service
)
for required_file in "${required_files[@]}"; do
    [[ -f "$required_file" && ! -L "$required_file" ]] ||
        fail "missing regular migration file: $required_file"
done

uv lock --check
make check
[[ -z "$(git status --porcelain)" ]] ||
    fail "working tree must be clean so migration metadata matches its payload"

remote_preflight() {
    ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" bash -s -- \
        "$REMOTE_APP_DIR" "$SERVICE_NAME" <<'REMOTE'
set -Eeuo pipefail
app_dir="$1"
service_name="$2"

[[ "$(id -u)" == "0" ]] || { echo "remote user must be root" >&2; exit 1; }
[[ "$app_dir" == "/root/python" ]] || { echo "unexpected app directory" >&2; exit 1; }
[[ "$service_name" == "tlggptbot.service" ]] || { echo "unexpected service" >&2; exit 1; }
[[ -d "$app_dir" && ! -L "$app_dir" ]] || { echo "invalid app directory" >&2; exit 1; }
[[ -f "$app_dir/bot_secrets.py" && ! -L "$app_dir/bot_secrets.py" ]] || {
    echo "missing regular bot_secrets.py" >&2
    exit 1
}
[[ -d /root/ve_tlg && ! -L /root/ve_tlg ]] || {
    echo "legacy /root/ve_tlg is required for rollback" >&2
    exit 1
}
for path in /root/tlggptbot-backups /opt/uv /opt/uv/python /var/cache/uv /etc/systemd/system/tlggptbot.service; do
    [[ ! -L "$path" ]] || { echo "refusing symbolic link: $path" >&2; exit 1; }
done
unit_path="$(systemctl show --property FragmentPath --value "$service_name")"
case "$unit_path" in
    /etc/systemd/system/tlggptbot.service|/lib/systemd/system/tlggptbot.service|/usr/lib/systemd/system/tlggptbot.service) ;;
    *) echo "unexpected systemd unit path: $unit_path" >&2; exit 1 ;;
esac
[[ -f "$unit_path" && ! -L "$unit_path" ]] || { echo "invalid systemd unit" >&2; exit 1; }
for command_name in curl tar sha256sum systemctl install flock mktemp readlink \
    journalctl grep tee; do
    command -v "$command_name" >/dev/null || {
        echo "missing remote command: $command_name" >&2
        exit 1
    }
done
systemctl is-active --quiet "$service_name" || {
    echo "legacy service must be active before migration" >&2
    exit 1
}
for disk_path in /root /opt /var; do
    available_kb="$(df -Pk "$disk_path" | awk 'NR == 2 {print $4}')"
    [[ "$available_kb" =~ ^[0-9]+$ && "$available_kb" -ge 2097152 ]] || {
        echo "at least 2 GiB free space is required on $disk_path" >&2
        exit 1
    }
done
echo "Remote preflight passed."
REMOTE
}

remote_preflight
if ((PREFLIGHT_ONLY)); then
    echo "Migration preflight passed; no server changes were made."
    exit 0
fi

commit_id="$(git rev-parse --verify HEAD)"
[[ "$commit_id" =~ ^[0-9a-f]{40}$ ]] || fail "could not determine Git commit"
migration_id="$(date -u +%Y%m%dT%H%M%SZ)-${commit_id:0:12}"
[[ "$migration_id" =~ ^[A-Za-z0-9._-]+$ ]] || fail "invalid migration ID"

LOCAL_BUNDLE="$(mktemp -d "${TMPDIR:-/tmp}/tlggptbot-deploy.XXXXXXXX")"
[[ -d "$LOCAL_BUNDLE" && ! -L "$LOCAL_BUNDLE" ]] || fail "invalid local bundle"
mkdir -p "$LOCAL_BUNDLE/deploy"

for required_file in "${required_files[@]}"; do
    install -m 0644 "$required_file" "$LOCAL_BUNDLE/$required_file"
done
chmod 0755 "$LOCAL_BUNDLE/migrate.sh"
lock_hash="$($checksum_command uv.lock | awk '{print $1}')"
printf 'GIT_COMMIT=%s\nLOCK_SHA256=%s\n' "$commit_id" "$lock_hash" \
    >"$LOCAL_BUNDLE/MIGRATION_METADATA"
(
    cd "$LOCAL_BUNDLE"
    find . -type f ! -name SHA256SUMS -print0 |
        LC_ALL=C sort -z |
        xargs -0 $checksum_command >SHA256SUMS
)

REMOTE_STAGING="$(
    ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" \
        'mktemp -d /root/.tlggptbot-migrate.XXXXXXXX'
)"
[[ "$REMOTE_STAGING" =~ ^/root/\.tlggptbot-migrate\.[A-Za-z0-9]+$ ]] ||
    fail "server returned an unsafe staging path"

rsync -rlpt -e "ssh -i $SSH_KEY_PATH -o BatchMode=yes" -- \
    "$LOCAL_BUNDLE/" "${REMOTE_TARGET}:${REMOTE_STAGING}/"

if ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" bash \
    "$REMOTE_STAGING/migrate.sh" \
    --staging-dir "$REMOTE_STAGING" \
    --migration-id "$migration_id"; then
    echo "Migration $migration_id completed successfully."
else
    status=$?
    echo "Migration failed with status $status." >&2
    echo "Remote diagnostics retained at: $REMOTE_STAGING" >&2
    exit "$status"
fi
