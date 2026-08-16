#!/usr/bin/env bash

set -Eeuo pipefail

CONFIG_FILE="./deploy_config.cfg"
MODE=""

usage() {
    cat <<'EOF'
Usage: ./deploy-cleanup.sh [--config PATH] (--preflight | --execute)

Audits or removes migration-only server rollback assets after the soak gate.
EOF
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

while (($#)); do
    case "$1" in
        --config) (($# >= 2)) || fail "--config requires a path"; CONFIG_FILE="$2"; shift 2 ;;
        --preflight) [[ -z "$MODE" ]] || fail "choose one mode"; MODE="preflight"; shift ;;
        --execute) [[ -z "$MODE" ]] || fail "choose one mode"; MODE="execute"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done
[[ -n "$MODE" ]] || fail "--preflight or --execute is required"
[[ -f "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]] || fail "invalid configuration file"

REMOTE_HOST=""
REMOTE_USER=""
SSH_KEY_PATH=""
REMOTE_APP_DIR=""
CONFIG_SERVICE=""
while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || fail "invalid configuration line: $line"
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
        REMOTE_HOST) [[ -z "$REMOTE_HOST" ]] || fail "duplicate configuration key: $key"; REMOTE_HOST="$value" ;;
        REMOTE_USER) [[ -z "$REMOTE_USER" ]] || fail "duplicate configuration key: $key"; REMOTE_USER="$value" ;;
        SSH_KEY_PATH) [[ -z "$SSH_KEY_PATH" ]] || fail "duplicate configuration key: $key"; SSH_KEY_PATH="$value" ;;
        REMOTE_APP_DIR) [[ -z "$REMOTE_APP_DIR" ]] || fail "duplicate configuration key: $key"; REMOTE_APP_DIR="$value" ;;
        SERVICE_NAME) [[ -z "$CONFIG_SERVICE" ]] || fail "duplicate configuration key: $key"; CONFIG_SERVICE="$value" ;;
        *) fail "unknown configuration key: $key" ;;
    esac
done <"$CONFIG_FILE"

[[ "$REMOTE_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || fail "invalid REMOTE_HOST"
[[ "$REMOTE_USER" == root ]] || fail "REMOTE_USER must be root"
[[ "$REMOTE_APP_DIR" == /root/python ]] || fail "unexpected REMOTE_APP_DIR"
[[ "$CONFIG_SERVICE" == tlggptbot.service ]] || fail "unexpected SERVICE_NAME"
case "$SSH_KEY_PATH" in
    "~/"*) SSH_KEY_PATH="${HOME:?}/${SSH_KEY_PATH#\~/}" ;;
esac
[[ "$SSH_KEY_PATH" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "invalid SSH_KEY_PATH"
[[ -f "$SSH_KEY_PATH" && ! -L "$SSH_KEY_PATH" ]] || fail "invalid SSH key"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR"
[[ -f cleanup-remote.sh && ! -L cleanup-remote.sh ]] || fail "missing cleanup worker"

./deploy.sh --config "$CONFIG_FILE" --preflight

if command -v sha256sum >/dev/null; then
    unit_hash="$(sha256sum deploy/tlggptbot.service | awk '{print $1}')"
else
    unit_hash="$(shasum -a 256 deploy/tlggptbot.service | awk '{print $1}')"
fi
ssh -i "$SSH_KEY_PATH" -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" \
    bash -s -- "$MODE" "$unit_hash" <cleanup-remote.sh
