#!/usr/bin/env bash
# Fail-fast validation for production deployment environment variables.
set -euo pipefail

failures=0

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  failures=$((failures + 1))
}

require_set() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    fail "$name is required"
  fi
}

validate_bounded_integer() {
  local name="$1"
  local minimum="$2"
  local maximum="$3"
  local raw="${!name:-}"
  if [[ ! "$raw" =~ ^[0-9]{1,10}$ ]]; then
    fail "$name must be an integer between $minimum and $maximum"
    return
  fi
  if (( 10#$raw < minimum || 10#$raw > maximum )); then
    fail "$name must be between $minimum and $maximum"
  fi
}

validate_compose_secret() {
  local name="$1"
  local raw="${!name:-}"
  if (( ${#raw} < 16 )); then
    fail "$name must contain at least 16 characters"
  fi
  # Compose uses each database password both as the literal PostgreSQL role
  # password and inside a connection URL. Restrict deployment secrets to the
  # URI-unreserved alphabet so those two interpretations cannot diverge.
  if [[ ! "$raw" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    fail "$name must use only URI-unreserved characters (A-Z, a-z, 0-9, . _ ~ -)"
  fi
}

validate_cors_origins() {
  local raw="${ATLAS_ARGUS_CORS_ORIGINS:-}"
  local origin host_port port
  local -a origins=()
  [[ -z "$raw" ]] && return
  if [[ "$raw" == ,* || "$raw" == *, || "$raw" == *,,* ]]; then
    fail "ATLAS_ARGUS_CORS_ORIGINS must not contain empty origins"
    return
  fi
  IFS=',' read -r -a origins <<< "$raw"
  for origin in "${origins[@]}"; do
    origin="${origin#"${origin%%[![:space:]]*}"}"
    origin="${origin%"${origin##*[![:space:]]}"}"
    if [[ "$origin" == "*" || "$origin" == "null" ]]; then
      fail "ATLAS_ARGUS_CORS_ORIGINS must not contain wildcard or null origins"
      continue
    fi
    if [[ ! "$origin" =~ ^https://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$ ]]; then
      fail "ATLAS_ARGUS_CORS_ORIGINS entries must be exact HTTPS origins without paths: $origin"
      continue
    fi
    host_port="${origin#https://}"
    if [[ "$host_port" == *:* ]]; then
      port="${host_port##*:}"
      if (( 10#$port < 1 || 10#$port > 65535 )); then
        fail "ATLAS_ARGUS_CORS_ORIGINS contains an invalid port: $origin"
      fi
    fi
  done
}

require_set ATLAS_ARGUS_ENV
require_set ATLAS_ARGUS_DATABASE_URL
require_set ATLAS_ARGUS_MIGRATION_DATABASE_URL
require_set ATLAS_ARGUS_RUNTIME_DB_PASSWORD
require_set ATLAS_ARGUS_MIGRATION_DB_PASSWORD
require_set ATLAS_ARGUS_METRICS_TOKEN
require_set ATLAS_ARGUS_FORWARDED_ALLOW_IPS
require_set ATLAS_ARGUS_BASE_URL
require_set ATLAS_ARGUS_BACKUP_DIR
require_set ATLAS_ARGUS_BACKUP_RETENTION_DAYS
require_set ATLAS_ARGUS_BACKUP_DB_USER
require_set ATLAS_ARGUS_BACKUP_OFFSITE_URI
require_set ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES
require_set ATLAS_ARGUS_MAX_PACKET_ENTRIES
require_set ATLAS_ARGUS_MAX_PACKET_DOCUMENT_BYTES
require_set ATLAS_ARGUS_MAX_PACKET_MANIFEST_BYTES
require_set ATLAS_ARGUS_MAX_PACKET_ARTIFACT_BYTES

[[ "${ATLAS_ARGUS_ENV:-}" == "production" ]] || fail "ATLAS_ARGUS_ENV must be production"
[[ "${ATLAS_ARGUS_DATABASE_URL:-}" != "${ATLAS_ARGUS_MIGRATION_DATABASE_URL:-}" ]] || fail "ATLAS_ARGUS_DATABASE_URL and ATLAS_ARGUS_MIGRATION_DATABASE_URL must be distinct"
runtime_url_prefix="postgresql+psycopg://atlas_app:${ATLAS_ARGUS_RUNTIME_DB_PASSWORD:-}@"
migration_url_prefix="postgresql+psycopg://atlas_owner:${ATLAS_ARGUS_MIGRATION_DB_PASSWORD:-}@"
[[ "${ATLAS_ARGUS_DATABASE_URL:-}" == "$runtime_url_prefix"* ]] || fail "ATLAS_ARGUS_DATABASE_URL must use atlas_app and the configured runtime password"
[[ "${ATLAS_ARGUS_MIGRATION_DATABASE_URL:-}" == "$migration_url_prefix"* ]] || fail "ATLAS_ARGUS_MIGRATION_DATABASE_URL must use atlas_owner and the configured migration password"
[[ "${ATLAS_ARGUS_RUNTIME_DB_PASSWORD:-}" != "atlas" ]] || fail "ATLAS_ARGUS_RUNTIME_DB_PASSWORD must not be atlas"
[[ "${ATLAS_ARGUS_MIGRATION_DB_PASSWORD:-}" != "atlas" ]] || fail "ATLAS_ARGUS_MIGRATION_DB_PASSWORD must not be atlas"
[[ "${ATLAS_ARGUS_RUNTIME_DB_PASSWORD:-}" != "${ATLAS_ARGUS_MIGRATION_DB_PASSWORD:-}" ]] || fail "runtime and migration database passwords must be distinct"
[[ "${ATLAS_ARGUS_DATABASE_URL:-}" != *"replace-"* ]] || fail "ATLAS_ARGUS_DATABASE_URL still contains placeholder text"
[[ "${ATLAS_ARGUS_MIGRATION_DATABASE_URL:-}" != *"replace-"* ]] || fail "ATLAS_ARGUS_MIGRATION_DATABASE_URL still contains placeholder text"
[[ "${ATLAS_ARGUS_RUNTIME_DB_PASSWORD:-}" != replace-* ]] || fail "ATLAS_ARGUS_RUNTIME_DB_PASSWORD still contains placeholder text"
[[ "${ATLAS_ARGUS_MIGRATION_DB_PASSWORD:-}" != replace-* ]] || fail "ATLAS_ARGUS_MIGRATION_DB_PASSWORD still contains placeholder text"
[[ "${ATLAS_ARGUS_METRICS_TOKEN:-}" != replace-* ]] || fail "ATLAS_ARGUS_METRICS_TOKEN still contains placeholder text"
[[ "${ATLAS_ARGUS_FORWARDED_ALLOW_IPS:-}" != replace-* ]] || fail "ATLAS_ARGUS_FORWARDED_ALLOW_IPS still contains placeholder text"
[[ "${ATLAS_ARGUS_FORWARDED_ALLOW_IPS:-}" != "*" ]] || fail "ATLAS_ARGUS_FORWARDED_ALLOW_IPS must not be *"
[[ "${ATLAS_ARGUS_FORWARDED_ALLOW_IPS:-}" != "0.0.0.0/0" ]] || fail "ATLAS_ARGUS_FORWARDED_ALLOW_IPS must not trust every IPv4 address"
[[ "${ATLAS_ARGUS_FORWARDED_ALLOW_IPS:-}" != "::/0" ]] || fail "ATLAS_ARGUS_FORWARDED_ALLOW_IPS must not trust every IPv6 address"
[[ "${ATLAS_ARGUS_BASE_URL:-}" == https://* ]] || fail "ATLAS_ARGUS_BASE_URL must use https://"
[[ "${ATLAS_ARGUS_BASE_URL:-}" != *replace-* ]] || fail "ATLAS_ARGUS_BASE_URL still contains placeholder text"
[[ "${ATLAS_ARGUS_BACKUP_OFFSITE_URI:-}" != replace-* ]] || fail "ATLAS_ARGUS_BACKUP_OFFSITE_URI still contains placeholder text"
[[ "${ATLAS_ARGUS_BACKUP_OFFSITE_URI:-}" == *:* ]] || fail "ATLAS_ARGUS_BACKUP_OFFSITE_URI should be an rclone destination such as remote:path"
[[ "${ATLAS_ARGUS_BACKUP_DB_USER:-}" == "atlas_owner" ]] || fail "ATLAS_ARGUS_BACKUP_DB_USER must be atlas_owner in production"
validate_compose_secret ATLAS_ARGUS_RUNTIME_DB_PASSWORD
validate_compose_secret ATLAS_ARGUS_MIGRATION_DB_PASSWORD
validate_compose_secret ATLAS_ARGUS_METRICS_TOKEN
backup_retention_days="${ATLAS_ARGUS_BACKUP_RETENTION_DAYS:-0}"
[[ "$backup_retention_days" =~ ^[0-9]+$ ]] || fail "ATLAS_ARGUS_BACKUP_RETENTION_DAYS must be numeric"
if [[ "$backup_retention_days" =~ ^[0-9]+$ ]] && (( backup_retention_days < 7 )); then
  fail "ATLAS_ARGUS_BACKUP_RETENTION_DAYS should be at least 7"
fi
validate_cors_origins
validate_bounded_integer ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES 1024 1073741824
validate_bounded_integer ATLAS_ARGUS_MAX_PACKET_ENTRIES 1 1000000
validate_bounded_integer ATLAS_ARGUS_MAX_PACKET_DOCUMENT_BYTES 4096 1073741824
validate_bounded_integer ATLAS_ARGUS_MAX_PACKET_MANIFEST_BYTES 4096 1073741824
validate_bounded_integer ATLAS_ARGUS_MAX_PACKET_ARTIFACT_BYTES 4096 1073741824

if (( failures > 0 )); then
  printf '%s production environment validation failure(s)\n' "$failures" >&2
  exit 1
fi

printf 'production env ok\n'
