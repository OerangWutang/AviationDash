#!/usr/bin/env bash
# Restore a backup into a throwaway database and verify core tables.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib/production_compose.sh
source scripts/lib/backup_safety.sh
atlas_argus_select_compose_file
umask 077

if [[ $# -ne 1 ]]; then
  printf 'usage: %s <backup.dump>\n' "$0" >&2
  exit 2
fi

backup="$1"
default_db_user="atlas"
if [[ "${ATLAS_ARGUS_ENV:-dev}" == "production" ]]; then
  default_db_user="atlas_owner"
fi
db_user="${ATLAS_ARGUS_BACKUP_DB_USER:-$default_db_user}"
stage_dir="$(mktemp -d "${TMPDIR:-/tmp}/atlas-argus-restore-drill.XXXXXX")"
staged_backup=""
drill_db="atlas_argus_drill_$(date -u +%Y%m%d%H%M%S)_${BASHPID}_${RANDOM}"
drill_created=0
cleanup() {
  if (( drill_created == 1 )); then
    docker compose exec -T db dropdb -U "$db_user" --if-exists "$drill_db" >/dev/null 2>&1 || true
  fi
  find "$stage_dir" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
  rmdir -- "$stage_dir" 2>/dev/null || true
}
trap cleanup EXIT
staged_backup="$(atlas_argus_stage_verified_backup "$backup" "$stage_dir")"

docker compose exec -T db createdb -U "$db_user" "$drill_db"
drill_created=1
docker compose exec -T db pg_restore \
  -U "$db_user" \
  -d "$drill_db" \
  --no-owner \
  --exit-on-error \
  --single-transaction \
  < "$staged_backup"
docker compose exec -T db psql -U "$db_user" -d "$drill_db" -v ON_ERROR_STOP=1 -tAc "SELECT count(*) > 0 FROM case_file" | grep -qx 't'
docker compose exec -T db psql -U "$db_user" -d "$drill_db" -v ON_ERROR_STOP=1 -tAc "SELECT count(*) > 0 FROM reviewer" | grep -qx 't'
docker compose exec -T db psql -U "$db_user" -d "$drill_db" -v ON_ERROR_STOP=1 -tAc "SELECT count(*) > 0 FROM audit_event" | grep -qx 't'
printf 'restore drill ok: %s -> %s\n' "$backup" "$drill_db"
