#!/usr/bin/env bash
# Copy append-only integrity checkpoints to the encrypted off-host destination.
set -euo pipefail
cd "$(dirname "$0")/.."

source_dir="${ATLAS_ARGUS_INTEGRITY_ANCHOR_DIR:-}"
destination="${ATLAS_ARGUS_BACKUP_OFFSITE_URI:-}"
if [[ -z "$source_dir" || ! -d "$source_dir" ]]; then
  printf 'ATLAS_ARGUS_INTEGRITY_ANCHOR_DIR must name an existing directory.\n' >&2
  exit 2
fi
if [[ -z "$destination" ]]; then
  printf 'ATLAS_ARGUS_BACKUP_OFFSITE_URI is required.\n' >&2
  exit 2
fi
if [[ "${ATLAS_ARGUS_BACKUP_OFFSITE_ENCRYPTED:-}" != "1" ]]; then
  printf 'Refusing checkpoint copy without encrypted offsite storage.\n' >&2
  exit 2
fi
if ! command -v rclone >/dev/null 2>&1; then
  printf 'rclone is required for off-host checkpoint copy.\n' >&2
  exit 2
fi
if find "$source_dir" -type l -print -quit | grep -q .; then
  printf 'Integrity checkpoint storage must not contain symbolic links.\n' >&2
  exit 2
fi

rclone copy --immutable "$source_dir" "${destination%/}/integrity-anchors"
printf 'integrity checkpoints copied to %s/integrity-anchors\n' "${destination%/}"
