#!/usr/bin/env bash
# Focused behavioral regression tests for production shell automation.
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

fail() {
  printf 'ops test failed: %s\n' "$1" >&2
  exit 1
}

expect_failure() {
  if "$@" >/dev/null 2>&1; then
    fail "command unexpectedly succeeded: $*"
  fi
}

set_valid_production_env() {
  export ATLAS_ARGUS_ENV=production
  export ATLAS_ARGUS_DATABASE_URL=postgresql+psycopg://atlas_app:runtime-secret-1234@db:5432/atlas_argus
  export ATLAS_ARGUS_MIGRATION_DATABASE_URL=postgresql+psycopg://atlas_owner:migration-secret-1234@db:5432/atlas_argus
  export ATLAS_ARGUS_RUNTIME_DB_PASSWORD=runtime-secret-1234
  export ATLAS_ARGUS_MIGRATION_DB_PASSWORD=migration-secret-1234
  export ATLAS_ARGUS_METRICS_TOKEN=metrics-secret-1234
  export ATLAS_ARGUS_CORS_ORIGINS=
  export ATLAS_ARGUS_FORWARDED_ALLOW_IPS=10.0.0.10
  export ATLAS_ARGUS_BASE_URL=https://atlas.example.test
  export ATLAS_ARGUS_BACKUP_DIR="$tmp/backups"
  export ATLAS_ARGUS_BACKUP_RETENTION_DAYS=30
  export ATLAS_ARGUS_BACKUP_DB_USER=atlas_owner
  export ATLAS_ARGUS_BACKUP_OFFSITE_URI=remote:atlas-argus
  export ATLAS_ARGUS_PRODUCTION_COMPOSE_FILE=docker-compose.production.example.yml
  export ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES=262144
  export ATLAS_ARGUS_MAX_PACKET_ENTRIES=250
  export ATLAS_ARGUS_MAX_PACKET_DOCUMENT_BYTES=2097152
  export ATLAS_ARGUS_MAX_PACKET_MANIFEST_BYTES=524288
  export ATLAS_ARGUS_MAX_PACKET_ARTIFACT_BYTES=3145728
}

set_valid_production_env
scripts/validate_production_env.sh >/dev/null
grep -q 'NOSUPERUSER.*NOCREATEDB.*NOCREATEROLE.*NOREPLICATION.*NOBYPASSRLS' \
  docker/postgres/init-runtime-role.sh \
  || fail "runtime role initializer does not reassert fail-closed role attributes"
ATLAS_ARGUS_CORS_ORIGINS='https://review.example.test, https://admin.example.test:8443' \
  scripts/validate_production_env.sh >/dev/null
ATLAS_ARGUS_CORS_ORIGINS='*' expect_failure scripts/validate_production_env.sh
ATLAS_ARGUS_CORS_ORIGINS='http://unsafe.example' expect_failure scripts/validate_production_env.sh
ATLAS_ARGUS_CORS_ORIGINS='https://review.example.test:70000' expect_failure scripts/validate_production_env.sh
ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES=not-a-number expect_failure scripts/validate_production_env.sh
ATLAS_ARGUS_BACKUP_DB_USER=atlas expect_failure scripts/validate_production_env.sh
ATLAS_ARGUS_MIGRATION_DB_PASSWORD="$ATLAS_ARGUS_RUNTIME_DB_PASSWORD" \
  expect_failure scripts/validate_production_env.sh
ATLAS_ARGUS_RUNTIME_DB_PASSWORD='unsafe@runtime-secret' \
  expect_failure scripts/validate_production_env.sh
ATLAS_ARGUS_DATABASE_URL='postgresql+psycopg://atlas_app:different-runtime-secret@db:5432/atlas_argus' \
  expect_failure scripts/validate_production_env.sh

mkdir -p "$tmp/package-clean" "$tmp/package-forbidden/backups"
printf 'reviewed source\n' > "$tmp/package-clean/README.md"
printf 'not a real dump\n' > "$tmp/package-forbidden/backups/evidence.dump"
scripts/validate_release_package.sh "$tmp/package-clean" >/dev/null
expect_failure scripts/validate_release_package.sh "$tmp/package-forbidden"
mkdir -p "$tmp/extracted-release/scripts"
cp scripts/validate_release_package.sh "$tmp/extracted-release/scripts/"
printf 'reviewed source\n' > "$tmp/extracted-release/README.md"
/bin/bash "$tmp/extracted-release/scripts/validate_release_package.sh" >/dev/null
mkdir -p "$tmp/extracted-release/backups"
printf 'forbidden\n' > "$tmp/extracted-release/backups/evidence.dump"
expect_failure /bin/bash "$tmp/extracted-release/scripts/validate_release_package.sh"
expect_failure env \
  ATLAS_ARGUS_RELEASE_PACKAGE="$tmp/package-clean" \
  ATLAS_ARGUS_SKIP_IMAGE_BUILD=0 \
  /bin/bash scripts/production_launch_check.sh
expect_failure env \
  ATLAS_ARGUS_SKIP_IMAGE_BUILD=1 \
  ATLAS_ARGUS_SKIP_DEPLOY=0 \
  ATLAS_ARGUS_SKIP_SMOKE=1 \
  /bin/bash scripts/production_launch_check.sh
expect_failure env \
  ATLAS_ARGUS_SKIP_IMAGE_BUILD=0 \
  ATLAS_ARGUS_SKIP_DEPLOY=1 \
  ATLAS_ARGUS_SKIP_SMOKE=1 \
  /bin/bash scripts/production_launch_check.sh
expect_failure env \
  ATLAS_ARGUS_SKIP_IMAGE_BUILD=1 \
  ATLAS_ARGUS_SKIP_DEPLOY=1 \
  ATLAS_ARGUS_SKIP_SMOKE=0 \
  /bin/bash scripts/production_launch_check.sh
expect_failure env \
  ATLAS_ARGUS_PRODUCTION_COMPOSE_FILE=docker-compose.yml \
  /bin/bash scripts/production_launch_check.sh

mkdir -p "$tmp/fake-bin" "$tmp/backups"
export FAKE_DOCKER_LOG="$tmp/docker.log"
cat > "$tmp/fake-bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'project=%s compose_file=%s image_tag=%s env=%s args=%s\n' \
  "${COMPOSE_PROJECT_NAME:-}" "${COMPOSE_FILE:-}" "${ATLAS_ARGUS_IMAGE_TAG:-}" \
  "${ATLAS_ARGUS_ENV:-}" "$*" >> "$FAKE_DOCKER_LOG"
case " $* " in
  *" pg_dump "*)
    printf 'fake postgres custom dump'
    ;;
  *" pg_restore --list "*)
    cat >/dev/null
    ;;
  *" pg_restore "*)
    cat >/dev/null
    ;;
  *" psql "*)
    printf 't\n'
    ;;
  *)
    ;;
esac
EOF
chmod 700 "$tmp/fake-bin/docker"

unsafe_backup_error="$tmp/unsafe-backup.stderr"
if /bin/bash -c \
  'source scripts/lib/backup_safety.sh; atlas_argus_resolve_backup_dir "$1" "$PWD"' \
  _ / >/dev/null 2>"$unsafe_backup_error"; then
  fail "backup safety guard accepted / as a retention target"
fi
grep -q 'unsafe broad backup directory' "$unsafe_backup_error" \
  || fail "unsafe backup-directory refusal was not explicit"
ln -s / "$tmp/root-backup-link"
expect_failure /bin/bash -c \
  'source scripts/lib/backup_safety.sh; atlas_argus_resolve_backup_dir "$1" "$PWD"' \
  _ "$tmp/root-backup-link"
expect_failure env PATH="$tmp/fake-bin:$PATH" ATLAS_ARGUS_BACKUP_DIR="$tmp" \
  /bin/bash scripts/backup_db.sh
: > "$FAKE_DOCKER_LOG"
for invalid_retention in 0 -1 not-a-number 36501; do
  expect_failure env \
    PATH="$tmp/fake-bin:$PATH" \
    ATLAS_ARGUS_BACKUP_RETENTION_DAYS="$invalid_retention" \
    /bin/bash scripts/backup_db.sh
done
[[ ! -s "$FAKE_DOCKER_LOG" ]] \
  || fail "invalid direct backup settings reached the database command"

old_dump="$tmp/backups/atlas_argus_20000101T000000Z.dump"
old_hash="${old_dump}.sha256"
printf 'expired\n' > "$old_dump"
printf 'expired checksum\n' > "$old_hash"
touch -d '40 days ago' "$old_dump" "$old_hash"
mkdir -p "$tmp/backups/nested"
nested_old_dump="$tmp/backups/nested/atlas_argus_19990101T000000Z.dump"
nested_old_hash="${nested_old_dump}.sha256"
printf 'nested expired\n' > "$nested_old_dump"
printf 'nested expired checksum\n' > "$nested_old_hash"
touch -d '40 days ago' "$nested_old_dump" "$nested_old_hash"
backup_stderr="$tmp/backup.stderr"
: > "$FAKE_DOCKER_LOG"
backup_path="$(PATH="$tmp/fake-bin:$PATH" ATLAS_ARGUS_BACKUP_RETENTION_DAYS=30 bash scripts/backup_db.sh 2>"$backup_stderr")"
[[ "$backup_path" != *$'\n'* ]] || fail "backup stdout contained more than one line"
[[ -f "$backup_path" && -f "${backup_path}.sha256" ]] || fail "backup artifacts were not created"
grep -q 'atlas_argus_20000101T000000Z.dump' "$backup_stderr" || fail "retention deletion was not logged to stderr"
[[ -f "$nested_old_dump" && -f "$nested_old_hash" ]] \
  || fail "backup retention traversed below its configured directory"
grep -q 'compose_file=docker-compose.production.example.yml.*pg_dump -U atlas_owner' "$FAKE_DOCKER_LOG" \
  || fail "direct production backup did not select the production compose file"

symlink_lock_dir="$tmp/symlink-lock/backups"
mkdir -p "$symlink_lock_dir"
lock_victim="$tmp/lock-victim"
printf 'do not truncate\n' > "$lock_victim"
ln -s "$lock_victim" "$symlink_lock_dir/.atlas_argus_backup.lock"
expect_failure env PATH="$tmp/fake-bin:$PATH" ATLAS_ARGUS_BACKUP_DIR="$symlink_lock_dir" \
  /bin/bash scripts/backup_db.sh
grep -qx 'do not truncate' "$lock_victim" \
  || fail "backup helper followed and truncated a symlink lock target"

exec 8>>"$tmp/backups/.atlas_argus_backup.lock"
flock -n 8 || fail "ops test could not acquire the backup lock"
if env PATH="$tmp/fake-bin:$PATH" /bin/bash scripts/backup_db.sh 8>&- >/dev/null 2>&1; then
  fail "concurrent backup did not fail on the directory lock"
fi
flock -u 8
exec 8>&-

cat > "$tmp/fake-bin/date" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "-u +%Y%m%dT%H%M%SZ" ]]; then
  printf '20260718T120000Z\n'
  exit 0
fi
exec /usr/bin/date "$@"
EOF
chmod 700 "$tmp/fake-bin/date"
same_second_a="$(PATH="$tmp/fake-bin:$PATH" bash scripts/backup_db.sh)"
same_second_b="$(PATH="$tmp/fake-bin:$PATH" bash scripts/backup_db.sh)"
[[ "$same_second_a" != "$same_second_b" ]] \
  || fail "same-second backups collided on one output path"
[[ -f "$same_second_a" && -f "$same_second_b" ]] \
  || fail "same-second unique backup artifacts were not both retained"
mv "$tmp/fake-bin/date" "$tmp/fake-date-used"

for helper in backup_db.sh backup_offsite.sh restore_db.sh restore_drill.sh; do
  expect_failure env \
    PATH="$tmp/fake-bin:$PATH" \
    ATLAS_ARGUS_PRODUCTION_COMPOSE_FILE=docker-compose.yml \
    /bin/bash "scripts/$helper" "$backup_path"
done

expect_failure env PATH="$tmp/fake-bin:$PATH" CONFIRM_RESTORE=1 bash scripts/restore_db.sh "$backup_path"

bad_checksum_backup="$tmp/backups/atlas_argus_bad_checksum.dump"
cp "$backup_path" "$bad_checksum_backup"
read -r bad_checksum_hash _ < <(sha256sum -- "$bad_checksum_backup")
printf '%s  different_backup.dump\n' "$bad_checksum_hash" > "${bad_checksum_backup}.sha256"
expect_failure env PATH="$tmp/fake-bin:$PATH" CONFIRM_RESTORE=1 CONFIRM_API_QUIESCED=1 \
  bash scripts/restore_db.sh "$bad_checksum_backup"
expect_failure env PATH="$tmp/fake-bin:$PATH" bash scripts/restore_drill.sh "$bad_checksum_backup"

multi_checksum_backup="$tmp/backups/atlas_argus_multi_checksum.dump"
cp "$backup_path" "$multi_checksum_backup"
read -r multi_checksum_hash _ < <(sha256sum -- "$multi_checksum_backup")
printf '%s  %s\n%s  %s\n' \
  "$multi_checksum_hash" "$(basename "$multi_checksum_backup")" \
  "$multi_checksum_hash" "$(basename "$multi_checksum_backup")" \
  > "${multi_checksum_backup}.sha256"
expect_failure env PATH="$tmp/fake-bin:$PATH" CONFIRM_RESTORE=1 CONFIRM_API_QUIESCED=1 \
  bash scripts/restore_db.sh "$multi_checksum_backup"
expect_failure env PATH="$tmp/fake-bin:$PATH" bash scripts/restore_drill.sh "$multi_checksum_backup"

ln -s "$backup_path" "$tmp/backups/atlas_argus_symlink.dump"
ln -s "${backup_path}.sha256" "$tmp/backups/atlas_argus_symlink.dump.sha256"
expect_failure env PATH="$tmp/fake-bin:$PATH" CONFIRM_RESTORE=1 CONFIRM_API_QUIESCED=1 \
  bash scripts/restore_db.sh "$tmp/backups/atlas_argus_symlink.dump"

: > "$FAKE_DOCKER_LOG"
PATH="$tmp/fake-bin:$PATH" CONFIRM_RESTORE=1 CONFIRM_API_QUIESCED=1 \
  bash scripts/restore_db.sh "$backup_path" >/dev/null
grep -q -- '--exit-on-error' "$FAKE_DOCKER_LOG" || fail "restore did not request fail-fast behavior"
grep -q -- '--single-transaction' "$FAKE_DOCKER_LOG" || fail "restore was not transactional"
grep -q 'compose_file=docker-compose.production.example.yml.*pg_restore' "$FAKE_DOCKER_LOG" \
  || fail "direct production restore did not select the production compose file"

: > "$FAKE_DOCKER_LOG"
PATH="$tmp/fake-bin:$PATH" bash scripts/restore_drill.sh "$backup_path" >/dev/null
grep -q 'compose_file=docker-compose.production.example.yml.*createdb -U atlas_owner' "$FAKE_DOCKER_LOG" \
  || fail "production restore drill did not select the production compose file"

export FAKE_RCLONE_LOG="$tmp/rclone.log"
cat > "$tmp/fake-bin/rclone" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_RCLONE_LOG"
EOF
chmod 700 "$tmp/fake-bin/rclone"
: > "$FAKE_DOCKER_LOG"
PATH="$tmp/fake-bin:$PATH" ATLAS_ARGUS_BACKUP_OFFSITE_ENCRYPTED=1 \
  bash scripts/backup_offsite.sh "$backup_path" >/dev/null
grep -q 'compose_file=docker-compose.production.example.yml.*pg_restore --list' "$FAKE_DOCKER_LOG" \
  || fail "production offsite validation did not select the production compose file"
grep -q "copyto .*/$(basename "$backup_path") remote:atlas-argus/$(basename "$backup_path")" "$FAKE_RCLONE_LOG" \
  || fail "offsite backup did not copy the verified dump"
: > "$FAKE_RCLONE_LOG"
expect_failure env PATH="$tmp/fake-bin:$PATH" ATLAS_ARGUS_BACKUP_OFFSITE_ENCRYPTED=1 \
  bash scripts/backup_offsite.sh "$bad_checksum_backup"
expect_failure env PATH="$tmp/fake-bin:$PATH" ATLAS_ARGUS_BACKUP_OFFSITE_ENCRYPTED=1 \
  bash scripts/backup_offsite.sh "$multi_checksum_backup"
[[ ! -s "$FAKE_RCLONE_LOG" ]] \
  || fail "offsite helper copied a backup with an invalid checksum sidecar"

export FAKE_CURL_LOG="$tmp/curl.log"
cat > "$tmp/fake-bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_CURL_LOG"
for arg in "$@"; do
  if [[ "$arg" == @* ]]; then
    cat "${arg#@}" >> "$FAKE_CURL_LOG"
  fi
done
printf '%s\n' \
  '{"status":"ok"}' \
  'atlas_argus_http_requests_total 1' \
  'x-content-type-options: nosniff' \
  'x-frame-options: DENY' \
  'content-type: text/html'
EOF
chmod 700 "$tmp/fake-bin/curl"
PATH="$tmp/fake-bin:$PATH" ATLAS_ARGUS_SMOKE_PACKET_FLOW=0 bash scripts/smoke.sh >/dev/null
grep -q 'Authorization: Bearer metrics-secret-1234' "$FAKE_CURL_LOG" || fail "smoke did not authenticate the metrics request"

security="$tmp/security.md"
evidence="$tmp/evidence.md"
restore_record="$tmp/restore.md"
: > "$security"
printf '   \n' > "$evidence"
printf 'restore drill passed\n' > "$restore_record"
export ATLAS_ARGUS_SECURITY_REVIEW_SIGNOFF="$security"
export ATLAS_ARGUS_EVIDENCE_REVIEW_SIGNOFF="$evidence"
export ATLAS_ARGUS_RESTORE_DRILL_RECORD="$restore_record"
expect_failure scripts/launch_gate.sh
printf 'security review approved\n' > "$security"
printf 'evidence review approved\n' > "$evidence"
scripts/launch_gate.sh >/dev/null

export FAKE_VERIFY_ENV="$tmp/verify.env"
cat > "$tmp/fake-bin/bash" <<'EOF'
#!/bin/bash
set -euo pipefail
if [[ "${1:-}" == "scripts/verify.sh" ]]; then
  env | sort > "$FAKE_VERIFY_ENV"
  exit 0
fi
exec /bin/bash "$@"
EOF
chmod 700 "$tmp/fake-bin/bash"
: > "$FAKE_DOCKER_LOG"
PATH="$tmp/fake-bin:$PATH" \
  ATLAS_ARGUS_RELEASE_PACKAGE="$tmp/package-clean" \
  ATLAS_ARGUS_SKIP_IMAGE_BUILD=1 \
  ATLAS_ARGUS_SKIP_DEPLOY=1 \
  ATLAS_ARGUS_SKIP_SMOKE=1 \
  ATLAS_ARGUS_SKIP_OFFSITE_BACKUP=1 \
  /bin/bash scripts/production_launch_check.sh >/dev/null
grep -q '^ATLAS_ARGUS_ENV=dev$' "$FAKE_VERIFY_ENV" || fail "verify did not force development mode"
if grep -q '^ATLAS_ARGUS_DATABASE_URL=' "$FAKE_VERIFY_ENV"; then
  fail "verify inherited the production runtime database URL"
fi
if grep -q '^ATLAS_ARGUS_MIGRATION_DATABASE_URL=' "$FAKE_VERIFY_ENV"; then
  fail "verify inherited the production migration database URL"
fi
grep -q 'project=atlas-argus-verify-' "$FAKE_DOCKER_LOG" || fail "verify cleanup did not use an isolated Compose project"
grep -q 'compose_file=docker-compose.production.example.yml.*pg_dump -U atlas_owner' "$FAKE_DOCKER_LOG" \
  || fail "production backup did not use the production compose file and atlas_owner"

cat > "$tmp/fake-bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  rev-parse|status)
    exit 0
    ;;
  ls-files)
    printf 'README.md\n'
    ;;
  *)
    exit 1
    ;;
esac
EOF
chmod 700 "$tmp/fake-bin/git"
: > "$FAKE_DOCKER_LOG"
: > "$FAKE_CURL_LOG"
env -u ATLAS_ARGUS_RELEASE_PACKAGE \
  PATH="$tmp/fake-bin:$PATH" \
  ATLAS_ARGUS_IMAGE_TAG=atlas-argus:reviewed-test \
  ATLAS_ARGUS_SKIP_VERIFY=1 \
  ATLAS_ARGUS_SKIP_OFFSITE_BACKUP=1 \
  /bin/bash scripts/production_launch_check.sh >/dev/null
grep -q 'args=build --build-arg DEMO_LOGINS= -t atlas-argus:reviewed-test .' "$FAKE_DOCKER_LOG" \
  || fail "production launch did not build the reviewed image tag"
grep -q 'compose_file=docker-compose.production.example.yml image_tag=atlas-argus:reviewed-test.*args=compose up -d --no-build --no-deps --force-recreate --wait api' "$FAKE_DOCKER_LOG" \
  || fail "production launch did not force-recreate and wait for the reviewed API image"
grep -q 'api/health' "$FAKE_CURL_LOG" \
  || fail "production launch did not smoke the service after deployment"

printf 'ops shell tests ok\n'
