#!/usr/bin/env bash
# Final launch gate: env validation plus required external sign-off records.
set -euo pipefail
cd "$(dirname "$0")/.."

failures=0
fail() {
  printf 'FAIL: %s\n' "$1" >&2
  failures=$((failures + 1))
}

scripts/validate_production_env.sh || failures=$((failures + 1))

candidate_id="${ATLAS_ARGUS_RELEASE_CANDIDATE_ID:-}"
if [[ -z "$candidate_id" ]]; then
  fail "ATLAS_ARGUS_RELEASE_CANDIDATE_ID is required"
fi

for name in \
  ATLAS_ARGUS_SECURITY_REVIEW_SIGNOFF \
  ATLAS_ARGUS_EVIDENCE_REVIEW_SIGNOFF \
  ATLAS_ARGUS_RESTORE_DRILL_RECORD
do
  if [[ -z "${!name:-}" ]]; then
    fail "$name is required"
  elif [[ "${!name}" == replace-* ]]; then
    fail "$name still contains placeholder text"
  elif [[ ! -f "${!name}" || ! -r "${!name}" ]]; then
    fail "$name does not point to a readable file: ${!name}"
  elif [[ ! -s "${!name}" ]] || ! grep -q '[^[:space:]]' "${!name}"; then
    fail "$name must point to a non-empty sign-off record: ${!name}"
  elif [[ -n "$candidate_id" ]] && ! grep -Fqx "candidate: $candidate_id" "${!name}"; then
    fail "$name is not bound to candidate $candidate_id"
  fi
done

if (( failures > 0 )); then
  printf '%s launch gate failure(s)\n' "$failures" >&2
  exit 1
fi

printf 'launch gate ok\n'
