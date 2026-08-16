#!/usr/bin/env bash

set -Eeuo pipefail

readonly APP_DIR="/root/python"
readonly SERVICE_NAME="tlggptbot.service"
readonly ACTIVE_UNIT="/etc/systemd/system/tlggptbot.service"
readonly BACKUP_ROOT="/root/tlggptbot-backups"
readonly RELEASE_ROOT="/root/tlggptbot-backups/releases"
readonly LEGACY_VENV="/root/ve_tlg"
readonly SOAK_SECONDS=604800

MODE="${1:-}"
if [[ "$MODE" == --help || "$MODE" == -h ]]; then
    echo "Usage: cleanup-remote.sh (preflight | execute)"
    exit 0
fi
[[ "$MODE" == preflight || "$MODE" == execute ]] || {
    echo "Usage: cleanup-remote.sh (preflight | execute)" >&2
    exit 1
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

((BASH_VERSINFO[0] >= 4)) || fail "Bash 4 or newer is required"
EXPECTED_UNIT_HASH="${2:-}"
[[ "$EXPECTED_UNIT_HASH" =~ ^[0-9a-f]{64}$ ]] || fail "invalid expected unit hash"

safe_remove_dir() {
    local target="$1"
    local allowed_pattern="$2"
    [[ "$target" =~ $allowed_pattern ]] || fail "unsafe cleanup target: $target"
    [[ -d "$target" && ! -L "$target" ]] || fail "invalid cleanup directory: $target"
    rm -rf -- "$target"
}

[[ "$(id -u)" == 0 ]] || fail "cleanup must run as root"
for command_name in awk basename cat cut date du find flock grep journalctl rm \
    sha256sum systemctl; do
    command -v "$command_name" >/dev/null || fail "missing command: $command_name"
done
[[ -d "$APP_DIR" && ! -L "$APP_DIR" ]] || fail "invalid application directory"
[[ -f "$APP_DIR/.deployment-info" && ! -L "$APP_DIR/.deployment-info" ]] ||
    fail "one successful routine deployment is required before cleanup"
[[ -f "$ACTIVE_UNIT" && ! -L "$ACTIVE_UNIT" ]] || fail "invalid active unit"
[[ "$(sha256sum "$ACTIVE_UNIT" | awk '{print $1}')" == "$EXPECTED_UNIT_HASH" ]] ||
    fail "active unit differs from the tracked unit"
[[ "$(systemctl show --property FragmentPath --value "$SERVICE_NAME")" == "$ACTIVE_UNIT" ]] ||
    fail "service is not using the migrated /etc unit"
systemctl is-active --quiet "$SERVICE_NAME" || fail "service is inactive"

mapfile -t release_backups < <(
    find "$RELEASE_ROOT" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null
)
[[ "${#release_backups[@]}" == 1 ]] || fail "exactly one routine rollback backup is required"
release_backup="${release_backups[0]}"
[[ "$release_backup" =~ ^/root/tlggptbot-backups/releases/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] ||
    fail "invalid routine backup path"
[[ ! -L "$release_backup" ]] || fail "routine backup must not be a symbolic link"
[[ -d "$release_backup/previous" && ! -L "$release_backup/previous" ]] ||
    fail "routine rollback application is missing"

mapfile -t migration_backups < <(
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d ! -name releases -print
)
[[ "${#migration_backups[@]}" == 1 ]] || fail "exactly one migration backup is required"
migration_backup="${migration_backups[0]}"
[[ "$migration_backup" =~ ^/root/tlggptbot-backups/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] ||
    fail "invalid migration backup path"
[[ ! -L "$migration_backup" ]] || fail "migration backup must not be a symbolic link"
[[ -f "$migration_backup/unit-path.txt" && -f "$migration_backup/tlggptbot.service" ]] ||
    fail "migration backup metadata is incomplete"
[[ -d "$migration_backup/python" && ! -L "$migration_backup/python" ]] ||
    fail "legacy rollback application is missing"

migration_stamp="$(basename "$migration_backup" | cut -d- -f1)"
migration_date="${migration_stamp:0:4}-${migration_stamp:4:2}-${migration_stamp:6:2}"
migration_time="${migration_stamp:9:2}:${migration_stamp:11:2}:${migration_stamp:13:2} UTC"
migration_epoch="$(date -d "$migration_date $migration_time" +%s)" ||
    fail "invalid migration timestamp"
now_epoch="$(date +%s)"
((now_epoch - migration_epoch >= SOAK_SECONDS)) || fail "seven-day soak period is incomplete"

old_unit="$(cat "$migration_backup/unit-path.txt")"
case "$old_unit" in
    /etc/systemd/system/tlggptbot.service) old_unit="" ;;
    /lib/systemd/system/tlggptbot.service|/usr/lib/systemd/system/tlggptbot.service)
        [[ -f "$old_unit" && ! -L "$old_unit" ]] || fail "invalid legacy unit"
        [[ "$(sha256sum "$old_unit" | awk '{print $1}')" == \
            "$(sha256sum "$migration_backup/tlggptbot.service" | awk '{print $1}')" ]] ||
            fail "legacy unit changed since migration"
        ;;
    *) fail "unexpected legacy unit path" ;;
esac

[[ -d "$LEGACY_VENV" && ! -L "$LEGACY_VENV" ]] || fail "invalid legacy environment"
mapfile -t residual_dirs < <(
    find /root -mindepth 1 -maxdepth 1 -type d \
        \( -name '.tlggptbot-migrate.*' -o -name '.uv-install.*' -o \
        -name 'python.new.*' \) -print
)
for residual in "${residual_dirs[@]}"; do
    [[ ! -L "$residual" ]] || fail "residual path is a symbolic link: $residual"
    [[ "$residual" =~ ^/root/(\.tlggptbot-migrate\.[A-Za-z0-9]+|\.uv-install\.[A-Za-z0-9]+|python\.new\.[A-Za-z0-9._-]+)$ ]] ||
        fail "unsafe residual path: $residual"
done

estimated_kb="$(du -sk "$LEGACY_VENV" "$migration_backup" "${residual_dirs[@]}" 2>/dev/null |
    awk '{total += $1} END {print total + 0}')"

echo "Cleanup is eligible."
echo "Legacy environment: $LEGACY_VENV"
echo "Migration backup: $migration_backup"
[[ -n "$old_unit" ]] && echo "Shadowed legacy unit: $old_unit"
for residual in "${residual_dirs[@]}"; do echo "Residual staging directory: $residual"; done
echo "Retained routine rollback: $release_backup"
echo "Estimated space to reclaim: ${estimated_kb} KiB"

[[ "$MODE" == execute ]] || exit 0
exec 9>/run/lock/tlggptbot-cleanup.lock
flock -n 9 || fail "another cleanup is running"
exec 8>/run/lock/tlggptbot-deploy.lock
flock -n 8 || fail "a deployment is running"
exec 7>/run/lock/tlggptbot-migrate.lock
flock -n 7 || fail "a migration is running"
health_start="$(date --iso-8601=seconds)"

if [[ -n "$old_unit" ]]; then
    rm -f -- "$old_unit"
    systemctl daemon-reload
    [[ "$(systemctl show --property FragmentPath --value "$SERVICE_NAME")" == "$ACTIVE_UNIT" ]] ||
        fail "active unit changed after legacy unit removal"
fi

safe_remove_dir "$LEGACY_VENV" '^/root/ve_tlg$'
safe_remove_dir "$migration_backup" \
    '^/root/tlggptbot-backups/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$'
for residual in "${residual_dirs[@]}"; do
    safe_remove_dir "$residual" \
        '^/root/(\.tlggptbot-migrate\.[A-Za-z0-9]+|\.uv-install\.[A-Za-z0-9]+|python\.new\.[A-Za-z0-9._-]+)$'
done

UV_CACHE_DIR=/var/cache/uv /usr/local/bin/uv cache prune
systemctl is-active --quiet "$SERVICE_NAME" || fail "service became inactive during cleanup"
if journalctl -u "$SERVICE_NAME" --since "$health_start" --no-pager |
    grep -Eq 'Traceback|Start request repeated too quickly'; then
    fail "service journal contains errors after cleanup"
fi
cat >"$APP_DIR/.migration-cleanup-complete" <<REPORT
CLEANED_AT=$(date --iso-8601=seconds)
REMOVED_LEGACY_VENV=$LEGACY_VENV
REMOVED_MIGRATION_BACKUP=$migration_backup
REMOVED_LEGACY_UNIT=${old_unit:-none}
RETAINED_RELEASE_BACKUP=$release_backup
REPORT
chmod 0600 "$APP_DIR/.migration-cleanup-complete"
echo "Cleanup completed; service remains active."
