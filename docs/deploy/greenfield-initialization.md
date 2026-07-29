# Greenfield production initialization

Use this one-shot workflow only for a new, empty production database while the
API is stopped. The initializer migrates the schema with the migration-owner
connection, verifies that every application table is empty, creates the first
matter and Senior Aviation Counsel account in one transaction, writes the root
account and case audit-chain events, and grants the runtime role its privileges.

## 1. Prepare the credential-free manifest

The manifest contains no credentials, but matter, docket, location, and counsel
identity can still be confidential. Protect it as deployment data. The image
runs as UID/GID 999, so a bind-mounted copy must be readable by that identity
(for example, owner `999:999` and mode `0400`); a root-only `0400` file will not
work.

```json
{
  "schemaVersion": 1,
  "matter": {
    "id": "case-production-001",
    "name": "North Sea Aviation Matter",
    "aircraft": "Airbus A320-200",
    "accidentDate": "2025-04-19",
    "location": "North Sea",
    "matterType": "Accident litigation",
    "status": "Active — evidence intake",
    "docketRef": "MATTER-2025-001"
  },
  "initialSeniorCounsel": {
    "id": "rev-production-admin",
    "name": "Alex Morgan",
    "username": "amorgan",
    "role": "Senior Aviation Counsel"
  }
}
```

Do not put a password in this file. Unknown fields, duplicate JSON keys,
non-Senior roles, malformed IDs, and future or invalid accident dates are
rejected before the database is touched.

## 2. Build the image and start only Postgres

Do not start the normal API command yet: its production bootstrap intentionally
refuses an empty database. From the reviewed release directory, load the normal
production Compose variables and build the non-demo image, then start only the
database:

```bash
ATLAS_COMPOSE_FILE=docker-compose.production.example.yml
ATLAS_COMPOSE_ENV=/run/secrets/atlas-argus-production.env

docker compose --env-file "$ATLAS_COMPOSE_ENV" -f "$ATLAS_COMPOSE_FILE" build api
docker compose --env-file "$ATLAS_COMPOSE_ENV" -f "$ATLAS_COMPOSE_FILE" \
  up -d --wait db
```

Copy the manifest to a path readable by the container's UID 999 without making
it writable by the container:

```bash
install -o 999 -g 999 -m 0400 initial-matter.json \
  /run/secrets/atlas-initial-matter.json
```

## 3. Run the initializer once

Inject the initial password through the process environment from the deployment
secret manager. For an interactive rehearsal, `read -rs` avoids shell history
and terminal output. `docker compose run` overrides the image's normal API
command, while the read-only bind mount makes the manifest available to UID 999:

```bash
read -rsp 'Initial Senior Counsel password: ' ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD
export ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD
echo

docker compose --env-file "$ATLAS_COMPOSE_ENV" -f "$ATLAS_COMPOSE_FILE" \
  run --rm --no-deps \
  -e ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD \
  -v /run/secrets/atlas-initial-matter.json:/run/secrets/atlas-initial-matter.json:ro \
  api python -m atlas_argus.db.initialize_production \
  /run/secrets/atlas-initial-matter.json

unset ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD
```

The password has no CLI option, is forbidden in the manifest, and is never
printed. The account is created with forced password rotation and without an
MFA secret. The operator must deliver the password through the approved secret
channel; the Senior Aviation Counsel must rotate it and enroll MFA immediately.

## 4. Start and verify

Start the remaining services normally. Bootstrap now sees the initialized
matter, reapplies migrations/grants idempotently, and removes the migration URL
from the API process environment before starting Uvicorn:

```bash
docker compose --env-file "$ATLAS_COMPOSE_ENV" -f "$ATLAS_COMPOSE_FILE" \
  up -d --wait
```

Confirm the administrator can sign in, complete forced rotation and MFA
enrollment, and see the initialized matter. Before final launch sign-off, use
reviewer administration to provision a separately controlled second global
Senior Aviation Counsel account; have that reviewer rotate the temporary
password and enroll MFA as well.

Recovery is administrator-mediated: an MFA-verified global Senior can reset
another reviewer's enabled factor, but cannot reset their own. The reset revokes
the target's sessions, clears the target MFA throttle, records account audit,
and requires fresh enrollment. There are no self-service recovery or backup
codes. Exercise this workflow on a test account, approve the identity-
verification ceremony, and never operate with only one enrolled global Senior.

Then run the authenticated smoke test and capture the initialization, recovery,
and review evidence in the release record.

Never rerun the initializer. A second invocation, or any invocation against a
database containing application data, refuses without creating records. Use
normal migrations and administration workflows after initialization; do not
empty or reset a production database to make this command run again.
