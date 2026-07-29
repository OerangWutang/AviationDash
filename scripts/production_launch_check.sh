#!/usr/bin/env bash
# End-to-end production launch check.
#
# Defaults are strict. Explicit ATLAS_ARGUS_SKIP_* knobs exist for local
# rehearsal only; do not use them for a real production launch.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib/production_compose.sh

image_tag="${ATLAS_ARGUS_IMAGE_TAG:-atlas-argus:production}"
verify_project=""

step() {
  printf '\n== %s ==\n' "$1"
}

maybe_skip() {
  local flag="$1"
  [[ "${!flag:-0}" == "1" ]]
}

cleanup_verify_stack() {
  if [[ -n "$verify_project" ]]; then
    env -u COMPOSE_PROFILES \
      COMPOSE_FILE=docker-compose.yml \
      COMPOSE_PROJECT_NAME="$verify_project" \
      docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    verify_project=""
  fi
}
trap cleanup_verify_stack EXIT

step "validate production environment"
scripts/validate_production_env.sh
atlas_argus_select_compose_file
production_compose_file="$COMPOSE_FILE"
export ATLAS_ARGUS_IMAGE_TAG="$image_tag"
# Backup and restore helpers use Docker Compose directly. Export the reviewed
# production definition so those later operations cannot fall back to the
# development database after the isolated verification block completes.
export COMPOSE_FILE="$production_compose_file"

skip_image_build=0
skip_deploy=0
maybe_skip ATLAS_ARGUS_SKIP_IMAGE_BUILD && skip_image_build=1
maybe_skip ATLAS_ARGUS_SKIP_DEPLOY && skip_deploy=1
if [[ "$skip_image_build" != "$skip_deploy" ]]; then
  printf '%s\n' \
    'ATLAS_ARGUS_SKIP_IMAGE_BUILD and ATLAS_ARGUS_SKIP_DEPLOY must be set together.' \
    'Skipping only the build could deploy an unreviewed tag; skipping only deployment would leave smoke checks on an unrelated service.' >&2
  exit 2
fi
if (( skip_deploy == 1 )) && ! maybe_skip ATLAS_ARGUS_SKIP_SMOKE; then
  printf '%s\n' \
    'ATLAS_ARGUS_SKIP_SMOKE=1 is required when deployment is skipped.' \
    'A smoke check is only release evidence after this script deploys the reviewed image.' >&2
  exit 2
fi

step "validate release package contents"
release_package="${ATLAS_ARGUS_RELEASE_PACKAGE:-}"
scripts/validate_release_package.sh "$release_package"
if [[ -n "$release_package" ]] && ! maybe_skip ATLAS_ARGUS_SKIP_IMAGE_BUILD; then
  printf '%s\n' \
    'Refusing to validate one release package but build the image from the current directory.' \
    'Run this script from the extracted reviewed package with ATLAS_ARGUS_RELEASE_PACKAGE unset.' >&2
  exit 2
fi

if maybe_skip ATLAS_ARGUS_SKIP_VERIFY; then
  step "skip full verify gate"
else
  step "run full verify gate in an isolated development database"
  verify_project="atlas-argus-verify-$$-${RANDOM}"
  env \
    -u ATLAS_ARGUS_DATABASE_URL \
    -u ATLAS_ARGUS_MIGRATION_DATABASE_URL \
    -u ATLAS_ARGUS_TEST_DATABASE_URL \
    -u ATLAS_ARGUS_RUNTIME_DB_PASSWORD \
    -u ATLAS_ARGUS_MIGRATION_DB_PASSWORD \
    -u ATLAS_ARGUS_DB_PASSWORD \
    -u ATLAS_ARGUS_SEED_PASSWORD \
    -u ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD \
    -u ATLAS_ARGUS_METRICS_TOKEN \
    -u ATLAS_ARGUS_CORS_ORIGINS \
    -u ATLAS_ARGUS_COOKIE_SECURE \
    -u ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES \
    -u ATLAS_ARGUS_MAX_PACKET_ENTRIES \
    -u ATLAS_ARGUS_MAX_PACKET_DOCUMENT_BYTES \
    -u ATLAS_ARGUS_MAX_PACKET_MANIFEST_BYTES \
    -u ATLAS_ARGUS_MAX_PACKET_ARTIFACT_BYTES \
    -u COMPOSE_PROFILES \
    ATLAS_ARGUS_ENV=dev \
    COMPOSE_FILE=docker-compose.yml \
    COMPOSE_PROJECT_NAME="$verify_project" \
    bash scripts/verify.sh
  cleanup_verify_stack
fi

if (( skip_image_build == 1 )); then
  step "skip production image build"
else
  step "build non-demo production image"
  docker build --build-arg DEMO_LOGINS= -t "$image_tag" .
fi

if (( skip_deploy == 1 )); then
  step "skip production deployment"
else
  step "deploy reviewed production image"
  # Start the database without recreating it, then force only the API onto the
  # image tag built above. Bring up the remaining services after the API health
  # check succeeds so the following smoke test is tied to this deployment.
  docker compose up -d --no-build --wait db
  docker compose up -d --no-build --no-deps --force-recreate --wait api
  docker compose up -d --no-build --no-deps --wait proxy prometheus
fi

if maybe_skip ATLAS_ARGUS_SKIP_SMOKE; then
  step "skip deployed smoke check"
else
  step "smoke deployed service"
  bash scripts/smoke.sh
fi

step "create verified database backup"
backup="$(bash scripts/backup_db.sh)"
printf 'backup: %s\n' "$backup"

if maybe_skip ATLAS_ARGUS_SKIP_OFFSITE_BACKUP; then
  step "skip off-host backup copy"
else
  step "copy backup off-host"
  bash scripts/backup_offsite.sh "$backup"
fi

step "run non-destructive restore drill"
if [[ -n "${ATLAS_ARGUS_RESTORE_DRILL_RECORD:-}" ]]; then
  mkdir -p "$(dirname "$ATLAS_ARGUS_RESTORE_DRILL_RECORD")"
  bash scripts/restore_drill.sh "$backup" | tee "$ATLAS_ARGUS_RESTORE_DRILL_RECORD"
else
  bash scripts/restore_drill.sh "$backup"
fi

step "final launch gate"
scripts/launch_gate.sh

printf '\nproduction launch check ok\n'
