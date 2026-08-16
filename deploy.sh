#!/usr/bin/env bash

set -Eeuo pipefail

readonly APP_DIR="/root/python"
readonly SERVICE_NAME="tlggptbot.service"

CONFIG_FILE="./deploy_config.cfg"
PREFLIGHT_ONLY=0
LOCAL_BUNDLE=""

usage() {
    cat <<'EOF'
Usage: ./deploy.sh [--config PATH] [--preflight]

Deploys the checked-out origin/main commit through a staged server cutover.
--preflight validates local and remote state without uploading or changing it.
EOF
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

cleanup_bundle() {
    if [[ -n "$LOCAL_BUNDLE" ]]; then
        case "$LOCAL_BUNDLE" in
            "${TMPDIR:-/tmp}"/tlggptbot-release.*)
                [[ -d "$LOCAL_BUNDLE" && ! -L "$LOCAL_BUNDLE" ]] &&
                    rm -rf -- "$LOCAL_BUNDLE"
                ;;
        esac
    fi
}

trap cleanup_bundle EXIT

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
        *) fail "unknown argument: $1" ;;
    esac
done

[[ -f "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]] ||
    fail "configuration must be a regular file: $CONFIG_FILE"

REMOTE_HOST=""
REMOTE_USER=""
SSH_KEY_PATH=""
REMOTE_APP_DIR=""
CONFIG_SERVICE=""

set_config_value() {
    local key="$1"
    local value="$2"
    [[ -n "$value" ]] || fail "empty configuration value: $key"
    case "$key" in
        REMOTE_HOST) [[ -z "$REMOTE_HOST" ]] || fail "duplicate configuration key: $key"; REMOTE_HOST="$value" ;;
        REMOTE_USER) [[ -z "$REMOTE_USER" ]] || fail "duplicate configuration key: $key"; REMOTE_USER="$value" ;;
        SSH_KEY_PATH) [[ -z "$SSH_KEY_PATH" ]] || fail "duplicate configuration key: $key"; SSH_KEY_PATH="$value" ;;
        REMOTE_APP_DIR) [[ -z "$REMOTE_APP_DIR" ]] || fail "duplicate configuration key: $key"; REMOTE_APP_DIR="$value" ;;
        SERVICE_NAME) [[ -z "$CONFIG_SERVICE" ]] || fail "duplicate configuration key: $key"; CONFIG_SERVICE="$value" ;;
        *) fail "unknown configuration key: $key" ;;
    esac
}

while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || fail "invalid configuration line: $line"
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Z_]+$ ]] || fail "invalid configuration key: $key"
    set_config_value "$key" "$value"
done <"$CONFIG_FILE"

[[ "$REMOTE_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || fail "invalid REMOTE_HOST"
[[ "$REMOTE_USER" == root ]] || fail "REMOTE_USER must be root"
[[ "$REMOTE_APP_DIR" == "$APP_DIR" ]] || fail "REMOTE_APP_DIR must be $APP_DIR"
[[ "$CONFIG_SERVICE" == "$SERVICE_NAME" ]] ||
    fail "SERVICE_NAME must be $SERVICE_NAME"
case "$SSH_KEY_PATH" in
    "~/"*) SSH_KEY_PATH="${HOME:?}/${SSH_KEY_PATH#\~/}" ;;
esac
[[ "$SSH_KEY_PATH" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "invalid SSH_KEY_PATH"
[[ -f "$SSH_KEY_PATH" && ! -L "$SSH_KEY_PATH" ]] || fail "invalid SSH key"

readonly SSH_OPTIONS=(-i "$SSH_KEY_PATH" -o BatchMode=yes)
readonly REMOTE_TARGET="${REMOTE_USER}@${REMOTE_HOST}"

for command_name in git make rsync ssh uv; do
    command -v "$command_name" >/dev/null || fail "missing command: $command_name"
done

if command -v sha256sum >/dev/null; then
    checksum_command="sha256sum"
elif command -v shasum >/dev/null; then
    checksum_command="shasum -a 256"
else
    fail "sha256sum or shasum is required"
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR"

git fetch --quiet origin main
make check
[[ -z "$(git status --porcelain)" ]] || fail "working tree must be clean"
commit_id="$(git rev-parse --verify HEAD)"
origin_main="$(git rev-parse --verify FETCH_HEAD)"
[[ "$commit_id" == "$origin_main" ]] || fail "HEAD must exactly match origin/main"
[[ "$commit_id" =~ ^[0-9a-f]{40}$ ]] || fail "invalid Git commit"

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
[[ "$UV_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "invalid uv pin"
[[ "$PYTHON_VERSION" =~ ^3\.11\.[0-9]+$ ]] || fail "invalid Python pin"

required_files=(
    .python-version
    backend_handlers.py
    deploy-remote.sh
    help.py
    main.py
    pyproject.toml
    state_store.py
    utils_handlers.py
    uv.lock
    white_lists.py
    deploy/runtime-versions.conf
    deploy/tlggptbot.service
)
for required_file in "${required_files[@]}"; do
    [[ -f "$required_file" && ! -L "$required_file" ]] ||
        fail "missing release file: $required_file"
done

remote_preflight() {
    ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" bash -s -- \
        "$UV_VERSION" "$PYTHON_VERSION" <<'REMOTE'
set -Eeuo pipefail
expected_uv="$1"
expected_python="$2"
app_dir="/root/python"
service="tlggptbot.service"

[[ "$(id -u)" == 0 ]] || { echo "remote user must be root" >&2; exit 1; }
[[ -d "$app_dir" && ! -L "$app_dir" ]] || { echo "invalid app directory" >&2; exit 1; }
[[ -f "$app_dir/bot_secrets.py" && ! -L "$app_dir/bot_secrets.py" ]] || {
    echo "missing server secret" >&2; exit 1;
}
[[ -f /etc/systemd/system/tlggptbot.service && ! -L /etc/systemd/system/tlggptbot.service ]] || {
    echo "the active unit must be the migrated /etc unit" >&2; exit 1;
}
[[ "$(systemctl show --property FragmentPath --value "$service")" == "/etc/systemd/system/tlggptbot.service" ]] || {
    echo "unexpected active systemd unit" >&2; exit 1;
}
systemctl is-active --quiet "$service" || { echo "service is inactive" >&2; exit 1; }
[[ -x /usr/local/bin/uv && ! -L /usr/local/bin/uv ]] || { echo "invalid uv binary" >&2; exit 1; }
[[ "$(/usr/local/bin/uv --version | awk '{print $2}')" == "$expected_uv" ]] || {
    echo "uv pin drift requires an explicit runtime upgrade" >&2; exit 1;
}
[[ "$($app_dir/.venv/bin/python --version)" == "Python $expected_python" ]] || {
    echo "Python pin drift requires an explicit runtime upgrade" >&2; exit 1;
}
marker="$app_dir/.deployment-info"
[[ -f "$marker" && ! -L "$marker" ]] || { echo "missing deployment marker" >&2; exit 1; }
marker_uv="$(awk -F= '$1 == "UV_VERSION" {print $2}' "$marker")"
marker_python="$(awk -F= '$1 == "PYTHON_VERSION" {print $2}' "$marker")"
[[ "$marker_uv" == "$expected_uv" && "$marker_python" == "$expected_python" ]] || {
    echo "runtime marker pin drift requires an explicit upgrade" >&2; exit 1;
}
for path in /root/tlggptbot-backups /root/tlggptbot-backups/releases; do
    [[ ! -L "$path" ]] || { echo "refusing symbolic link: $path" >&2; exit 1; }
done
state_dir=/var/lib/tlggptbot
state_file="$state_dir/user-state.json"
[[ ! -L "$state_dir" ]] || { echo "invalid state directory" >&2; exit 1; }
[[ ! -e "$state_dir" || -d "$state_dir" ]] || {
    echo "invalid state directory" >&2; exit 1;
}
[[ ! -L "$state_file" ]] || { echo "invalid state file" >&2; exit 1; }
[[ ! -e "$state_file" || -f "$state_file" ]] || {
    echo "invalid state file" >&2; exit 1;
}
for disk_path in /root /var; do
    available_kb="$(df -Pk "$disk_path" | awk 'NR == 2 {print $4}')"
    [[ "$available_kb" =~ ^[0-9]+$ && "$available_kb" -ge 1048576 ]] || {
        echo "at least 1 GiB free space is required on $disk_path" >&2; exit 1;
    }
done
echo "Routine deployment preflight passed."
REMOTE
}

remote_preflight
if ((PREFLIGHT_ONLY)); then
    echo "Preflight passed; no server changes were made."
    exit 0
fi

deployment_id="$(date -u +%Y%m%dT%H%M%SZ)-${commit_id:0:12}"
LOCAL_BUNDLE="$(mktemp -d "${TMPDIR:-/tmp}/tlggptbot-release.XXXXXXXX")"
[[ -d "$LOCAL_BUNDLE" && ! -L "$LOCAL_BUNDLE" ]] || fail "invalid local bundle"
mkdir -p "$LOCAL_BUNDLE/deploy"
for required_file in "${required_files[@]}"; do
    install -m 0644 "$required_file" "$LOCAL_BUNDLE/$required_file"
done
chmod 0755 "$LOCAL_BUNDLE/deploy-remote.sh"
lock_hash="$($checksum_command uv.lock | awk '{print $1}')"
printf 'DEPLOYMENT_ID=%s\nGIT_COMMIT=%s\nLOCK_SHA256=%s\n' \
    "$deployment_id" "$commit_id" "$lock_hash" >"$LOCAL_BUNDLE/DEPLOYMENT_METADATA"
(
    cd "$LOCAL_BUNDLE"
    find . -type f ! -name SHA256SUMS -print0 |
        LC_ALL=C sort -z |
        xargs -0 $checksum_command >SHA256SUMS
)

remote_staging="$(ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" \
    'mktemp -d /root/.tlggptbot-deploy.XXXXXXXX')"
[[ "$remote_staging" =~ ^/root/\.tlggptbot-deploy\.[A-Za-z0-9]+$ ]] ||
    fail "unsafe remote staging path"
rsync -rlpt -e "ssh -i $SSH_KEY_PATH -o BatchMode=yes" -- \
    "$LOCAL_BUNDLE/" "${REMOTE_TARGET}:${remote_staging}/"

if ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" bash \
    "$remote_staging/deploy-remote.sh" --staging-dir "$remote_staging"; then
    echo "Deployment $deployment_id completed successfully."
else
    status=$?
    echo "Deployment failed with status $status; diagnostics: $remote_staging" >&2
    exit "$status"
fi
