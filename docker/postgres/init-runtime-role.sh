#!/usr/bin/env bash
# Create the non-owner API runtime role on first database initialization.
set -euo pipefail

: "${ATLAS_ARGUS_RUNTIME_DB_PASSWORD:?set ATLAS_ARGUS_RUNTIME_DB_PASSWORD}"

escaped_password="${ATLAS_ARGUS_RUNTIME_DB_PASSWORD//\'/\'\'}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_app') THEN
    CREATE ROLE atlas_app
      LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
      PASSWORD '${escaped_password}';
  ELSE
    -- Reassert every security-sensitive attribute. This protects an existing
    -- role that was accidentally or manually promoted before initialization.
    ALTER ROLE atlas_app WITH
      LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
      PASSWORD '${escaped_password}';
  END IF;
END
\$\$;
EOSQL
