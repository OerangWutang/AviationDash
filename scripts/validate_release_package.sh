#!/usr/bin/env bash
# Reject release/review packages that contain dependency trees, backups,
# caches, build output, env files, or likely secret material.
set -euo pipefail
cd "$(dirname "$0")/.."

target="${1:-}"

forbidden_re='(^|/)(\.git|node_modules|\.venv|\.pytest_cache|\.ruff_cache|__pycache__|dist|build|backups)(/|$)|(^|/)\.env($|[./])|(^|/).*\.(dump|sql|sqlite|db|pem|key|p12|pfx|kdbx)$|(^|/)(id_rsa|id_ed25519|known_hosts|\.pgpass)$'

list_entries() {
  if [[ -z "$target" ]]; then
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      dirty="$(git status --porcelain --untracked-files=all -- .)"
      if [[ -n "$dirty" ]]; then
        printf 'release package validation failed; source tree is not clean:\n%s\n' "$dirty" >&2
        return 1
      fi
      git ls-files
    else
      # A reviewed release archive intentionally has no .git directory. In
      # that environment the extracted tree itself is the build context and
      # must be enumerated rather than rejected for lacking Git metadata.
      find . \( -type f -o -type d -o -type l \) | sed 's#^\./##'
    fi
  elif [[ -d "$target" ]]; then
    (cd "$target" && find . \( -type f -o -type d -o -type l \)) | sed 's#^\./##'
  elif [[ -f "$target" ]]; then
    case "$target" in
      *.zip)
        unzip -Z1 "$target"
        ;;
      *.tar|*.tar.gz|*.tgz)
        tar -tf "$target"
        ;;
      *)
        printf 'unsupported package type: %s\n' "$target" >&2
        exit 2
        ;;
    esac
  else
    printf 'release package target not found: %s\n' "$target" >&2
    exit 2
  fi
}

entries="$(list_entries)"
violations="$(printf '%s\n' "$entries" | grep -E "$forbidden_re" | grep -Ev '(^|/)\.env([./][^/]+)?\.example$' || true)"
if [[ -n "$violations" ]]; then
  printf 'release package validation failed; forbidden paths found:\n%s\n' "$violations" >&2
  exit 1
fi

printf 'release package validation ok'
if [[ -n "$target" ]]; then
  printf ': %s' "$target"
fi
printf '\n'
