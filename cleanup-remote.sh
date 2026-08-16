#!/usr/bin/env bash

set -Eeuo pipefail

readonly APP_DIR="/root/python"
readonly SERVICE_NAME="tlggptbot.service"
readonly ACTIVE_UNIT="/etc/systemd/system/tlggptbot.service"
readonly BACKUP_ROOT="/root/tlggptbot-backups"
readonly RELEASE_ROOT="/root/tlggptbot-backups/releases"
readonly LEGACY_VENV="/root/ve_tlg"
readonly IN_PROGRESS_STATE="$BACKUP_ROOT/.migration-cleanup-in-progress"
readonly COMPLETE_STATE="$BACKUP_ROOT/.migration-cleanup-complete"
readonly CLEANUP_FAILURE_STATUS=20

MODE="${1:-}"
EXPECTED_UNIT_HASH="${2:-}"

usage() {
    echo "Usage: cleanup-remote.sh (audit | auto) [expected-unit-sha256]"
}

if [[ "$MODE" == --help || "$MODE" == -h ]]; then
    usage
    exit 0
fi
[[ "$MODE" == audit || "$MODE" == auto ]] || {
    usage >&2
    exit 1
}

fail() {
    echo "ERROR: $*" >&2
    if [[ "$MODE" == auto ]]; then exit "$CLEANUP_FAILURE_STATUS"; fi
    exit 1
}

on_error() {
    local status=$?
    trap - ERR
    if [[ "$MODE" == auto && "$status" != "$CLEANUP_FAILURE_STATUS" ]]; then
        echo "ERROR: automatic migration cleanup failed with status $status" >&2
        exit "$CLEANUP_FAILURE_STATUS"
    fi
    exit "$status"
}
trap on_error ERR

((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 3))) ||
    fail "Bash 4.3 or newer is required"
if [[ "$MODE" == auto ]]; then
    [[ "$EXPECTED_UNIT_HASH" =~ ^[0-9a-f]{64}$ ]] || fail "invalid expected unit hash"
else
    [[ -z "$EXPECTED_UNIT_HASH" ]] || fail "audit does not accept a unit hash"
fi

for command_name in awk cat chmod date find flock grep journalctl mv readlink rm \
    sha256sum stat systemctl; do
    command -v "$command_name" >/dev/null || fail "missing command: $command_name"
done
[[ "$(id -u)" == 0 ]] || fail "cleanup must run as root"
for path in "$APP_DIR" "$BACKUP_ROOT"; do
    [[ -d "$path" && ! -L "$path" ]] || fail "invalid directory: $path"
done
[[ ! -L "$RELEASE_ROOT" && (! -e "$RELEASE_ROOT" || -d "$RELEASE_ROOT") ]] ||
    fail "invalid release backup root"
[[ -f "$ACTIVE_UNIT" && ! -L "$ACTIVE_UNIT" ]] || fail "invalid active unit"
[[ "$(systemctl show --property FragmentPath --value "$SERVICE_NAME")" == "$ACTIVE_UNIT" ]] ||
    fail "service is not using the migrated /etc unit"
systemctl is-active --quiet "$SERVICE_NAME" || fail "service is inactive"

validate_state_file() {
    local state_file="$1"
    [[ -f "$state_file" && ! -L "$state_file" ]] || fail "invalid cleanup state: $state_file"
    [[ "$(stat -c '%U:%G:%a' "$state_file")" == root:root:600 ]] ||
        fail "unsafe cleanup state permissions: $state_file"
}

[[ ! -L "$COMPLETE_STATE" ]] || fail "completion marker must not be a symbolic link"
if [[ -e "$COMPLETE_STATE" ]]; then
    validate_state_file "$COMPLETE_STATE"
    grep -Eq '^STATUS=complete$' "$COMPLETE_STATE" || fail "invalid completion marker"
    echo "Migration cleanup already completed."
    exit 0
fi
[[ ! -L "$IN_PROGRESS_STATE" ]] || fail "cleanup state must not be a symbolic link"

validate_migration_backup() {
    local migration_backup="$1"
    [[ "$migration_backup" =~ ^/root/tlggptbot-backups/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] ||
        fail "invalid migration backup path"
    [[ -d "$migration_backup" && ! -L "$migration_backup" ]] ||
        fail "invalid migration backup"
    [[ -f "$migration_backup/unit-path.txt" && ! -L "$migration_backup/unit-path.txt" ]] ||
        fail "migration unit metadata is missing"
    [[ -f "$migration_backup/tlggptbot.service" && ! -L "$migration_backup/tlggptbot.service" ]] ||
        fail "migration unit backup is missing"
    [[ -d "$migration_backup/python" && ! -L "$migration_backup/python" ]] ||
        fail "legacy rollback application is missing"
}

validate_residual_path() {
    local residual="$1"
    [[ "$residual" =~ ^/root/(\.tlggptbot-migrate\.[A-Za-z0-9]+|\.uv-install\.[A-Za-z0-9]+|python\.new\.[A-Za-z0-9._-]+)$ ]] ||
        fail "unsafe residual path: $residual"
    if [[ -e "$residual" || -L "$residual" ]]; then
        [[ -d "$residual" && ! -L "$residual" ]] || fail "invalid residual path: $residual"
    fi
}

validate_release_backup() {
    local expected_id="$1"
    local release_backup="$RELEASE_ROOT/$expected_id"
    local -a release_backups=()
    mapfile -t release_backups < <(
        find "$RELEASE_ROOT" -mindepth 1 -maxdepth 1 -print 2>/dev/null
    )
    [[ "${#release_backups[@]}" == 1 ]] ||
        fail "exactly one routine rollback backup is required"
    [[ "${release_backups[0]}" == "$release_backup" ]] || fail "unexpected routine backup"
    [[ -d "$release_backup/previous" && ! -L "$release_backup/previous" ]] ||
        fail "routine rollback application is missing"
    echo "$release_backup"
}

audit_release_backups() {
    local -a release_entries=()
    mapfile -t release_entries < <(
        find "$RELEASE_ROOT" -mindepth 1 -maxdepth 1 -print 2>/dev/null
    )
    ((${#release_entries[@]} <= 1)) || fail "ambiguous routine rollback backups"
    if ((${#release_entries[@]} == 1)); then
        [[ "${release_entries[0]}" =~ ^/root/tlggptbot-backups/releases/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] ||
            fail "invalid routine rollback path"
        [[ -d "${release_entries[0]}/previous" && ! -L "${release_entries[0]}" &&
            ! -L "${release_entries[0]}/previous" ]] || fail "invalid routine rollback backup"
    fi
}

discover_cleanup_targets() {
    local -n migration_backup_ref="$1"
    local -n old_unit_ref="$2"
    local -n old_unit_hash_ref="$3"
    local -n residual_dirs_ref="$4"
    local -a migration_backups=()

    mapfile -t migration_backups < <(
        find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d ! -name releases -print
    )
    [[ "${#migration_backups[@]}" == 1 ]] || fail "exactly one migration backup is required"
    migration_backup_ref="${migration_backups[0]}"
    validate_migration_backup "$migration_backup_ref"

    [[ -d "$LEGACY_VENV" && ! -L "$LEGACY_VENV" ]] || fail "invalid legacy environment"
    old_unit_ref="$(cat "$migration_backup_ref/unit-path.txt")"
    case "$old_unit_ref" in
        /etc/systemd/system/tlggptbot.service)
            old_unit_ref="none"
            old_unit_hash_ref="none"
            ;;
        /lib/systemd/system/tlggptbot.service|/usr/lib/systemd/system/tlggptbot.service)
            [[ -f "$old_unit_ref" && ! -L "$old_unit_ref" ]] || fail "invalid legacy unit"
            old_unit_hash_ref="$(sha256sum "$old_unit_ref" | awk '{print $1}')"
            [[ "$old_unit_hash_ref" == \
                "$(sha256sum "$migration_backup_ref/tlggptbot.service" | awk '{print $1}')" ]] ||
                fail "legacy unit changed since migration"
            ;;
        *) fail "unexpected legacy unit path" ;;
    esac

    mapfile -t residual_dirs_ref < <(
        find /root -mindepth 1 -maxdepth 1 \
            \( -name '.tlggptbot-migrate.*' -o -name '.uv-install.*' -o \
            -name 'python.new.*' \) -print
    )
    for residual in "${residual_dirs_ref[@]}"; do validate_residual_path "$residual"; done
}

load_in_progress_state() {
    local -n started_id_ref="$1"
    local -n started_commit_ref="$2"
    local -n migration_backup_ref="$3"
    local -n old_unit_ref="$4"
    local -n old_unit_hash_ref="$5"
    local -n residual_dirs_ref="$6"
    local version=""

    validate_state_file "$IN_PROGRESS_STATE"
    while IFS='=' read -r key value; do
        case "$key" in
            VERSION) [[ -z "$version" ]] || fail "duplicate state key: $key"; version="$value" ;;
            STARTED_BY_DEPLOYMENT_ID) [[ -z "$started_id_ref" ]] || fail "duplicate state key: $key"; started_id_ref="$value" ;;
            STARTED_BY_GIT_COMMIT) [[ -z "$started_commit_ref" ]] || fail "duplicate state key: $key"; started_commit_ref="$value" ;;
            MIGRATION_BACKUP) [[ -z "$migration_backup_ref" ]] || fail "duplicate state key: $key"; migration_backup_ref="$value" ;;
            LEGACY_UNIT) [[ -z "$old_unit_ref" ]] || fail "duplicate state key: $key"; old_unit_ref="$value" ;;
            LEGACY_UNIT_SHA256) [[ -z "$old_unit_hash_ref" ]] || fail "duplicate state key: $key"; old_unit_hash_ref="$value" ;;
            RESIDUAL_DIR) residual_dirs_ref+=("$value") ;;
            "") ;;
            *) fail "unknown cleanup state key: $key" ;;
        esac
    done <"$IN_PROGRESS_STATE"

    [[ "$version" == 1 ]] || fail "unsupported cleanup state version"
    [[ "$started_id_ref" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] ||
        fail "invalid cleanup deployment ID"
    [[ "$started_commit_ref" =~ ^[0-9a-f]{40}$ ]] || fail "invalid cleanup Git commit"
    [[ "$migration_backup_ref" =~ ^/root/tlggptbot-backups/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] ||
        fail "invalid cleanup migration backup"
    [[ "$old_unit_ref" == none || "$old_unit_ref" == /lib/systemd/system/tlggptbot.service ||
        "$old_unit_ref" == /usr/lib/systemd/system/tlggptbot.service ]] || fail "invalid cleanup unit"
    if [[ "$old_unit_ref" == none ]]; then
        [[ "$old_unit_hash_ref" == none ]] || fail "invalid cleanup unit hash"
    else
        [[ "$old_unit_hash_ref" =~ ^[0-9a-f]{64}$ ]] || fail "invalid cleanup unit hash"
    fi
    for residual in "${residual_dirs_ref[@]}"; do validate_residual_path "$residual"; done
}

validate_remaining_recorded_targets() {
    local migration_backup="$1"
    local old_unit="$2"
    local old_unit_hash="$3"
    shift 3

    if [[ -e "$migration_backup" || -L "$migration_backup" ]]; then
        validate_migration_backup "$migration_backup"
    fi
    if [[ -e "$LEGACY_VENV" || -L "$LEGACY_VENV" ]]; then
        [[ -d "$LEGACY_VENV" && ! -L "$LEGACY_VENV" ]] ||
            fail "invalid recorded legacy environment"
    fi
    if [[ "$old_unit" != none && (-e "$old_unit" || -L "$old_unit") ]]; then
        [[ -f "$old_unit" && ! -L "$old_unit" ]] || fail "invalid recorded legacy unit"
        [[ "$(sha256sum "$old_unit" | awk '{print $1}')" == "$old_unit_hash" ]] ||
            fail "recorded legacy unit changed"
    fi
    for residual in "$@"; do validate_residual_path "$residual"; done
}

write_in_progress_state() {
    local deployment_id="$1"
    local git_commit="$2"
    local migration_backup="$3"
    local old_unit="$4"
    local old_unit_hash="$5"
    shift 5
    local state_tmp="${IN_PROGRESS_STATE}.$$"
    [[ ! -e "$state_tmp" && ! -L "$state_tmp" ]] ||
        fail "cleanup state temporary file already exists"
    umask 077
    {
        printf 'VERSION=1\n'
        printf 'STARTED_BY_DEPLOYMENT_ID=%s\n' "$deployment_id"
        printf 'STARTED_BY_GIT_COMMIT=%s\n' "$git_commit"
        printf 'MIGRATION_BACKUP=%s\n' "$migration_backup"
        printf 'LEGACY_UNIT=%s\n' "$old_unit"
        printf 'LEGACY_UNIT_SHA256=%s\n' "$old_unit_hash"
        for residual in "$@"; do printf 'RESIDUAL_DIR=%s\n' "$residual"; done
    } >"$state_tmp"
    chmod 0600 "$state_tmp"
    mv -- "$state_tmp" "$IN_PROGRESS_STATE"
}

safe_remove_recorded_dir() {
    local target="$1"
    local allowed_pattern="$2"
    [[ "$target" =~ $allowed_pattern ]] || fail "unsafe recorded cleanup target: $target"
    if [[ -e "$target" || -L "$target" ]]; then
        [[ -d "$target" && ! -L "$target" ]] ||
            fail "invalid recorded cleanup target: $target"
        rm -rf -- "$target"
    fi
}

started_id=""
started_commit=""
migration_backup=""
old_unit=""
old_unit_hash=""
residual_dirs=()

if [[ -e "$IN_PROGRESS_STATE" ]]; then
    load_in_progress_state started_id started_commit migration_backup old_unit \
        old_unit_hash residual_dirs
    validate_remaining_recorded_targets "$migration_backup" "$old_unit" \
        "$old_unit_hash" "${residual_dirs[@]}"
    echo "Recoverable migration cleanup is already in progress."
else
    discover_cleanup_targets migration_backup old_unit old_unit_hash residual_dirs
    echo "Migration cleanup targets are valid."
fi
echo "Legacy environment: $LEGACY_VENV"
echo "Migration backup: $migration_backup"
[[ "$old_unit" != none ]] && echo "Shadowed legacy unit: $old_unit"
for residual in "${residual_dirs[@]}"; do echo "Residual staging directory: $residual"; done
audit_release_backups

if [[ "$MODE" == audit ]]; then
    echo "Cleanup will run automatically after a successful routine deployment."
    exit 0
fi

[[ -e /proc/$$/fd/9 ]] || fail "automatic cleanup requires the deployment lock"
[[ "$(readlink -f /proc/$$/fd/9)" == /run/lock/tlggptbot-deploy.lock ]] ||
    fail "automatic cleanup does not hold the deployment lock"
[[ "$(sha256sum "$ACTIVE_UNIT" | awk '{print $1}')" == "$EXPECTED_UNIT_HASH" ]] ||
    fail "active unit differs from the deployed unit"
[[ -f "$APP_DIR/.deployment-info" && ! -L "$APP_DIR/.deployment-info" ]] ||
    fail "successful routine deployment marker is missing"
deployment_id="$(awk -F= '$1 == "DEPLOYMENT_ID" {print $2}' "$APP_DIR/.deployment-info")"
git_commit="$(awk -F= '$1 == "GIT_COMMIT" {print $2}' "$APP_DIR/.deployment-info")"
[[ "$deployment_id" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] ||
    fail "invalid active deployment ID"
[[ "$git_commit" =~ ^[0-9a-f]{40}$ ]] || fail "invalid active Git commit"
release_backup="$(validate_release_backup "$deployment_id")"

exec 8>/run/lock/tlggptbot-cleanup.lock
flock -n 8 || fail "another cleanup is running"
exec 7>/run/lock/tlggptbot-migrate.lock
flock -n 7 || fail "a migration is running"

if [[ ! -e "$IN_PROGRESS_STATE" ]]; then
    write_in_progress_state "$deployment_id" "$git_commit" "$migration_backup" \
        "$old_unit" "$old_unit_hash" "${residual_dirs[@]}"
    started_id="$deployment_id"
    started_commit="$git_commit"
fi

health_start="$(date --iso-8601=seconds)"
if [[ "$old_unit" != none && (-e "$old_unit" || -L "$old_unit") ]]; then
    [[ -f "$old_unit" && ! -L "$old_unit" ]] || fail "invalid recorded legacy unit"
    [[ "$(sha256sum "$old_unit" | awk '{print $1}')" == "$old_unit_hash" ]] ||
        fail "recorded legacy unit changed"
    rm -f -- "$old_unit"
    systemctl daemon-reload
    [[ "$(systemctl show --property FragmentPath --value "$SERVICE_NAME")" == "$ACTIVE_UNIT" ]] ||
        fail "active unit changed after legacy unit removal"
fi

safe_remove_recorded_dir "$LEGACY_VENV" '^/root/ve_tlg$'
safe_remove_recorded_dir "$migration_backup" \
    '^/root/tlggptbot-backups/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$'
for residual in "${residual_dirs[@]}"; do
    safe_remove_recorded_dir "$residual" \
        '^/root/(\.tlggptbot-migrate\.[A-Za-z0-9]+|\.uv-install\.[A-Za-z0-9]+|python\.new\.[A-Za-z0-9._-]+)$'
done

UV_CACHE_DIR=/var/cache/uv /usr/local/bin/uv cache prune ||
    echo "WARNING: uv cache pruning failed" >&2
systemctl is-active --quiet "$SERVICE_NAME" || fail "service became inactive during cleanup"
if journalctl -u "$SERVICE_NAME" --since "$health_start" --no-pager |
    grep -Eq 'Traceback|Start request repeated too quickly'; then
    fail "service journal contains errors after cleanup"
fi

complete_tmp="${COMPLETE_STATE}.$$"
[[ ! -e "$complete_tmp" && ! -L "$complete_tmp" ]] ||
    fail "completion marker temporary file already exists"
umask 077
cat >"$complete_tmp" <<REPORT
STATUS=complete
STARTED_BY_DEPLOYMENT_ID=$started_id
STARTED_BY_GIT_COMMIT=$started_commit
COMPLETED_BY_DEPLOYMENT_ID=$deployment_id
COMPLETED_BY_GIT_COMMIT=$git_commit
COMPLETED_AT=$(date --iso-8601=seconds)
REMOVED_LEGACY_VENV=$LEGACY_VENV
REMOVED_MIGRATION_BACKUP=$migration_backup
REMOVED_LEGACY_UNIT=$old_unit
RETAINED_RELEASE_BACKUP=$release_backup
REPORT
chmod 0600 "$complete_tmp"
mv -- "$complete_tmp" "$COMPLETE_STATE"
rm -f -- "$IN_PROGRESS_STATE"
echo "Migration cleanup completed; service remains active."
