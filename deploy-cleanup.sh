#!/usr/bin/env bash

set -Eeuo pipefail

CONFIG_FILE="./deploy_config.cfg"

usage() {
    cat <<'EOF'
Usage: ./deploy-cleanup.sh [--config PATH] --preflight

Runs the deployment preflight, including the read-only migration cleanup audit.
Eligible cleanup executes automatically after the next successful deployment.
EOF
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

PREFLIGHT=0
while (($#)); do
    case "$1" in
        --config)
            (($# >= 2)) || fail "--config requires a path"
            CONFIG_FILE="$2"
            shift 2
            ;;
        --preflight)
            PREFLIGHT=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *) fail "unknown argument: $1" ;;
    esac
done
((PREFLIGHT)) || fail "only --preflight is supported"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/deploy.sh" --config "$CONFIG_FILE" --preflight
