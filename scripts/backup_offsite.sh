#!/usr/bin/env bash
# Copy a verified local backup to off-host storage via rclone.
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
if [[ -z "${ATLAS_ARGUS_BACKUP_OFFSITE_URI:-}" ]]; then
  printf 'ATLAS_ARGUS_BACKUP_OFFSITE_URI is required (example: remote:bucket/path).\n' >&2
  exit 2
fi
if [[ "${ATLAS_ARGUS_BACKUP_OFFSITE_ENCRYPTED:-}" != "1" ]]; then
  printf 'Refusing offsite copy without ATLAS_ARGUS_BACKUP_OFFSITE_ENCRYPTED=1. Use rclone crypt or an independently encrypted destination.\n' >&2
  exit 2
fi
if ! command -v rclone >/dev/null 2>&1; then
  printf 'rclone is required for off-host backup copy.\n' >&2
  exit 2
fi

backup="$1"
stage_dir="$(mktemp -d "${TMPDIR:-/tmp}/atlas-argus-offsite.XXXXXX")"
staged_backup=""
cleanup() {
  find "$stage_dir" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
  rmdir -- "$stage_dir" 2>/dev/null || true
}
trap cleanup EXIT
staged_backup="$(atlas_argus_stage_verified_backup "$backup" "$stage_dir")"

docker compose exec -T db pg_restore --list < "$staged_backup" >/dev/null
rclone copyto "$staged_backup" "${ATLAS_ARGUS_BACKUP_OFFSITE_URI%/}/$(basename "$backup")"
rclone copyto "${staged_backup}.sha256" "${ATLAS_ARGUS_BACKUP_OFFSITE_URI%/}/$(basename "${backup}.sha256")"
printf 'offsite backup copied: %s -> %s/%s\n' "$backup" "${ATLAS_ARGUS_BACKUP_OFFSITE_URI%/}" "$(basename "$backup")"
