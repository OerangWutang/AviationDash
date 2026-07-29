#!/usr/bin/env bash
# Restore a backup created by scripts/backup_db.sh into the docker-compose db.
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
if [[ "${CONFIRM_RESTORE:-}" != "1" ]]; then
  printf 'Refusing restore without CONFIRM_RESTORE=1; restore replaces database contents.\n' >&2
  exit 2
fi
if [[ "${CONFIRM_API_QUIESCED:-}" != "1" ]]; then
  printf 'Refusing live restore without CONFIRM_API_QUIESCED=1. Stop API traffic and confirm the maintenance window first.\n' >&2
  exit 2
fi

backup="$1"
default_db_user="atlas"
if [[ "${ATLAS_ARGUS_ENV:-dev}" == "production" ]]; then
  default_db_user="atlas_owner"
fi
db_user="${ATLAS_ARGUS_BACKUP_DB_USER:-$default_db_user}"
db_name="${ATLAS_ARGUS_BACKUP_DB_NAME:-atlas_argus}"
stage_dir="$(mktemp -d "${TMPDIR:-/tmp}/atlas-argus-live-restore.XXXXXX")"
staged_backup=""
cleanup() {
  find "$stage_dir" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
  rmdir -- "$stage_dir" 2>/dev/null || true
}
trap cleanup EXIT
staged_backup="$(atlas_argus_stage_verified_backup "$backup" "$stage_dir")"

docker compose exec -T db pg_restore --list < "$staged_backup" >/dev/null
docker compose exec -T db pg_restore \
  -U "$db_user" \
  -d "$db_name" \
  --clean \
  --if-exists \
  --no-owner \
  --exit-on-error \
  --single-transaction \
  < "$staged_backup"
printf 'restore ok: %s -> %s\n' "$backup" "$db_name"
