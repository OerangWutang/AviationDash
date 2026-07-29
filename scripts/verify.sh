#!/usr/bin/env bash
# Atlas Argus — canonical full gate. One command, every check:
#   frontend: typecheck, tests, production build
#   backend:  lint, doctrine tests against real Postgres (starts docker db)
#   operations: shell syntax and deployment-helper regression tests
#
#   bash scripts/verify.sh
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n── %s ──\n' "$1"; }

step "frontend: typecheck (tsc -b, strict)"
npx tsc -b

step "frontend: tests (vitest)"
npx vitest run

step "frontend: production build (vite)"
npx vite build

step "backend: database (docker compose db, waiting for healthy)"
docker compose up -d --wait db

step "backend: lint (ruff)"
server/.venv/bin/ruff check server/src server/tests

step "backend: tests (pytest vs real Postgres)"
(cd server && .venv/bin/python -m pytest -q)

step "operations: shell syntax"
bash -n scripts/*.sh scripts/lib/*.sh docker/postgres/init-runtime-role.sh

step "operations: deployment-helper regressions"
bash scripts/test_ops.sh

printf '\nALL GATES GREEN\n'
