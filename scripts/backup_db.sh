#!/usr/bin/env bash
# Create a compressed custom-format Postgres backup from the docker-compose db.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib/production_compose.sh
source scripts/lib/backup_safety.sh
atlas_argus_select_compose_file
umask 077

backup_dir="$(atlas_argus_resolve_backup_dir "${ATLAS_ARGUS_BACKUP_DIR:-backups}" "$PWD")"
retention_days="$(atlas_argus_validate_retention_days "${ATLAS_ARGUS_BACKUP_RETENTION_DAYS:-30}")"
default_db_user="atlas"
if [[ "${ATLAS_ARGUS_ENV:-dev}" == "production" ]]; then
  default_db_user="atlas_owner"
fi
db_user="${ATLAS_ARGUS_BACKUP_DB_USER:-$default_db_user}"
db_name="${ATLAS_ARGUS_BACKUP_DB_NAME:-atlas_argus}"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
lockfile="${backup_dir}/.atlas_argus_backup.lock"
if [[ -L "$lockfile" || ( -e "$lockfile" && ! -f "$lockfile" ) ]]; then
  printf 'Backup lock path is not a regular file: %s\n' "$lockfile" >&2
  exit 2
fi
command -v flock >/dev/null 2>&1 || {
  printf 'flock is required to serialize database backups.\n' >&2
  exit 2
}
exec {backup_lock_fd}>"$lockfile"
chmod 600 "$lockfile"
if ! flock -n "$backup_lock_fd"; then
  printf 'Another database backup is already running for %s.\n' "$backup_dir" >&2
  exit 2
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmpfile="$(mktemp "${backup_dir}/atlas_argus_${timestamp}_XXXXXX.dump.tmp")"
outfile="${tmpfile%.tmp}"
tmphash="${outfile}.sha256.tmp"
published=0
cleanup() {
  rm -f -- "$tmpfile" "$tmphash"
  if (( published == 0 )); then
    rm -f -- "$outfile" "${outfile}.sha256"
  fi
}
trap cleanup EXIT

docker compose exec -T db pg_dump -U "$db_user" -d "$db_name" -Fc > "$tmpfile"
[[ -s "$tmpfile" ]] || {
  printf 'pg_dump produced an empty backup.\n' >&2
  exit 1
}
docker compose exec -T db pg_restore --list < "$tmpfile" >/dev/null
read -r dump_hash _ < <(sha256sum -- "$tmpfile")
printf '%s  %s\n' "$dump_hash" "$(basename -- "$outfile")" > "$tmphash"
mv -- "$tmpfile" "$outfile"
mv -- "$tmphash" "${outfile}.sha256"
chmod 600 "$outfile" "${outfile}.sha256"
(
  cd "$backup_dir"
  sha256sum -c "$(basename "${outfile}.sha256")" >/dev/null
)
published=1
# stdout is a machine-readable contract consumed by launch/cron automation:
# only the newly-created backup path belongs there. Retention logs go to stderr.
find "$backup_dir" -maxdepth 1 -type f -name 'atlas_argus_*.dump' -mtime "+${retention_days}" -print -delete >&2
find "$backup_dir" -maxdepth 1 -type f -name 'atlas_argus_*.dump.sha256' -mtime "+${retention_days}" -print -delete >&2
printf '%s\n' "$outfile"
