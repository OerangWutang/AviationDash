#!/usr/bin/env bash
# Shared path, retention, and checksum guards for database backup helpers.

atlas_argus_validate_retention_days() {
  local raw="$1"
  local value
  if [[ ! "$raw" =~ ^[0-9]{1,5}$ ]]; then
    printf 'ATLAS_ARGUS_BACKUP_RETENTION_DAYS must be an integer from 1 to 36500.\n' >&2
    return 2
  fi
  value=$((10#$raw))
  if (( value < 1 || value > 36500 )); then
    printf 'ATLAS_ARGUS_BACKUP_RETENTION_DAYS must be between 1 and 36500.\n' >&2
    return 2
  fi
  if [[ "${ATLAS_ARGUS_ENV:-dev}" == "production" ]] && (( value < 7 )); then
    printf 'ATLAS_ARGUS_BACKUP_RETENTION_DAYS must be at least 7 in production.\n' >&2
    return 2
  fi
  printf '%d\n' "$value"
}

atlas_argus_resolve_backup_dir() {
  local raw="$1"
  local project_root="$2"
  local resolved canonical_project relative
  if [[ -z "$raw" || "$raw" =~ [[:cntrl:]] ]]; then
    printf 'ATLAS_ARGUS_BACKUP_DIR must be a nonempty path without control characters.\n' >&2
    return 2
  fi
  resolved="$(realpath -m -- "$raw")" || {
    printf 'Cannot resolve ATLAS_ARGUS_BACKUP_DIR: %s\n' "$raw" >&2
    return 2
  }
  canonical_project="$(realpath -e -- "$project_root")"

  case "$resolved" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
      printf 'Refusing unsafe broad backup directory: %s\n' "$resolved" >&2
      return 2
      ;;
    /bin/*|/boot/*|/dev/*|/etc/*|/lib/*|/lib64/*|/proc/*|/run/*|/sbin/*|/sys/*|/usr/*|/var/lib/*)
      printf 'Refusing backup directory inside a system-managed tree: %s\n' "$resolved" >&2
      return 2
      ;;
  esac
  if [[ "$canonical_project" == "$resolved" || "$canonical_project" == "$resolved/"* ]]; then
    printf 'Refusing backup directory that is the project root or one of its ancestors: %s\n' "$resolved" >&2
    return 2
  fi
  relative="${resolved#/}"
  if [[ "$relative" != */*/* ]]; then
    printf 'Refusing backup directory that is not a dedicated nested path: %s\n' "$resolved" >&2
    return 2
  fi
  if [[ -e "$resolved" && ! -d "$resolved" ]]; then
    printf 'Backup path exists but is not a directory: %s\n' "$resolved" >&2
    return 2
  fi
  printf '%s\n' "$resolved"
}

atlas_argus_stage_verified_backup() {
  local source_backup="$1"
  local stage_dir="$2"
  local source_checksum="${source_backup}.sha256"
  local basename staged_backup staged_checksum
  local -a checksum_lines=()
  local checksum_line expected_hash expected_suffix actual_hash ignored

  if [[ ! -f "$source_backup" || ! -r "$source_backup" || -L "$source_backup" ]]; then
    printf 'Backup must be a readable, regular, non-symlink file: %s\n' "$source_backup" >&2
    return 2
  fi
  if [[ ! -f "$source_checksum" || ! -r "$source_checksum" || -L "$source_checksum" ]]; then
    printf 'Backup checksum must be a readable, regular, non-symlink file: %s\n' "$source_checksum" >&2
    return 2
  fi
  if [[ ! -d "$stage_dir" ]]; then
    printf 'Backup staging directory does not exist.\n' >&2
    return 2
  fi

  basename="$(basename -- "$source_backup")"
  if [[ ! "$basename" =~ ^atlas_argus_[A-Za-z0-9][A-Za-z0-9._-]*\.dump$ ]]; then
    printf 'Backup filename is not an Atlas Argus dump name: %s\n' "$basename" >&2
    return 2
  fi
  staged_backup="${stage_dir}/${basename}"
  staged_checksum="${staged_backup}.sha256"
  cp -- "$source_backup" "$staged_backup"
  cp -- "$source_checksum" "$staged_checksum"
  chmod 600 "$staged_backup" "$staged_checksum"

  mapfile -t checksum_lines < "$staged_checksum"
  if (( ${#checksum_lines[@]} != 1 )); then
    printf 'Backup checksum must contain exactly one entry.\n' >&2
    return 2
  fi
  checksum_line="${checksum_lines[0]}"
  expected_hash="${checksum_line%% *}"
  expected_suffix="${checksum_line#"$expected_hash"}"
  if [[ ! "$expected_hash" =~ ^[[:xdigit:]]{64}$ || "$expected_suffix" != "  $basename" ]]; then
    printf 'Backup checksum entry must name exactly %s.\n' "$basename" >&2
    return 2
  fi
  read -r actual_hash ignored < <(sha256sum -- "$staged_backup")
  if [[ "${actual_hash,,}" != "${expected_hash,,}" ]]; then
    printf 'Backup checksum verification failed: %s\n' "$basename" >&2
    return 2
  fi
  printf '%s\n' "$staged_backup"
}
