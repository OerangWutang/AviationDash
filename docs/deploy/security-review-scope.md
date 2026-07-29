# Security review scope

Give this scope to the independent reviewer before any real-matter deployment.
The review must assess the administrator-mediated MFA recovery path and the
deployment's two-Senior operating requirement, not just enrollment and login.

## Authentication and session lifecycle

Review:

- `server/src/atlas_argus/auth.py`
- `server/src/atlas_argus/api/routes.py`
- `server/src/atlas_argus/db/models.py`
- `src/api/client.ts`
- `src/state/store.tsx`
- `src/components/MfaPage.tsx`
- `src/components/views/AdminView.tsx`

Cover:

- Argon2 parameters and password validation;
- uniform login failures, dummy-hash timing, persistent login throttle, and
  stale-row cleanup;
- session token entropy, hash-at-rest, cookie flags, expiry, revocation, and
  deactivated-account behavior;
- forced initial password rotation and other-session revocation;
- `expiresAt` handling, periodic/focus revalidation, authenticated-state
  scrubbing, and late-response epochs across logout/login/matter switches;
- TOTP generation/verification window, enrollment, per-session verification,
  persistent MFA throttle, and sensitive-action gating;
- inability to replace an enabled factor through enrollment; and
- administrator MFA reset authorization, no-self-reset rule, target-session
  revocation, throttle clearing, factor removal, re-enrollment, and exactly-once
  account audit. Confirm password reset preserves MFA. There is no self-service
  recovery or backup code, so verify that at least two independent global Senior
  accounts and an approved identity-verification ceremony exist.

Also assess TOTP secret protection at rest. The application must recover the
secret to verify codes, and this repository has no separate envelope-encryption
key boundary.

## Authorization and data visibility

Review the distinction between:

- global `Reviewer.role`, used for account/reviewer administration; and
- per-matter `CaseMember.role`, used for evidence visibility, critical
  decisions, report approval, packet generation, and case-event attribution.

Test at minimum:

- active membership on every explicit matter route;
- cross-matter source, claim, conflict, section, packet, and membership IDs;
- non-Senior filtering of withholding-privilege sources/claims and dependent
  conflicts, decisions, sections, and audit events, including dangling
  `relatedConflictIds` and historical claim/section audit relationships;
- a global Senior account with a non-Senior matter role;
- matter membership grant/deactivate/reactivate and self-removal prevention;
- global account deactivation/reactivation/password/MFA reset and session
  effects;
- body-field identity/role smuggling; and
- MFA plus role composition on every high-risk route.

Primary files:

- `server/src/atlas_argus/services.py`
- `server/src/atlas_argus/domain/permissions.py`
- `server/src/atlas_argus/db/session.py`
- `src/domain/permissions.ts`
- `src/state/store.tsx`

## Packet generation and artifact disclosure

Review both renderers and their different operating modes:

- `server/src/atlas_argus/packet.py`
- `src/domain/packet.ts`
- packet operations in `server/src/atlas_argus/services.py`
- `src/components/views/PacketExportView.tsx`

Verify:

- privilege inheritance and production withholding;
- approved-active-revision gating;
- sanitization and HTML escaping of evidence and metadata;
- removal of confidence, evidence quality, reviewer identity, decision
  reasoning, and generator identity from production document/manifest;
- absence of generator reviewer ID in the persisted production manifest;
- packet-local opaque references and generic metadata for every withheld or
  excluded entry, including removal of original title/text/citations/internal
  impact, section/revision/claim identifiers, and revision hashes;
- removal of privileged claims from `citedClaimIds` and the evidence appendix,
  source-identity-only privilege-log entries, and generic conflict summaries
  when a counterpart is withheld;
- generator attribution filtering in artifact list/detail DTOs for a
  non-cleared requester;
- internal packet privilege clearance on retrieval;
- content/manifest/artifact byte limits before persistence;
- integrity-failure quarantine in the browser;
- packet snapshot behavior under concurrent mutations; and
- client/server renderer parity and the explicit local-mode approval difference.

Treat counsel-authored section text as a separate disclosure boundary. The
renderer removes structured withholding-source/claim fields but cannot infer
whether approved prose manually repeats privileged substance; review the human
approval and production-QA procedure accordingly.

Do not equate server-side redaction with an inability to fabricate arbitrary
client files. The security property is that a fabricated file lacks the stored
server artifact, audit event, and integrity-chain provenance.

## Database and integrity controls

Review:

- all Alembic migrations;
- `server/src/atlas_argus/db/migrate.py`;
- `server/src/atlas_argus/db/guards.py`;
- `server/src/atlas_argus/integrity.py`;
- `docker/postgres/init-runtime-role.sh`; and
- integrity operations in `server/src/atlas_argus/services.py`.

Cover:

- runtime/schema-owner separation and role-attribute validation;
- grants, default privileges, function execution, and append-table revocations;
- RLS policies and transaction-local reviewer context;
- the fact that RLS does not contain a stolen runtime credential able to set
  that context;
- append-only triggers for case audit, account audit, review decisions, report
  revisions, and packet artifacts;
- case/account/decision/revision/packet chain ordering and concurrency locks;
- canonicalization and recomputation by verification endpoints;
- optimistic `expectedVersion` checks and stale-write behavior; and
- the limitation that unkeyed co-located hashes are neither signatures nor
  WORM protection against a schema owner.

## Production initialization and configuration

Review:

- `server/src/atlas_argus/db/bootstrap.py`
- `server/src/atlas_argus/db/initialize_production.py`
- `server/src/atlas_argus/config.py`
- `server/src/atlas_argus/api/app.py`
- `Dockerfile`
- `docker-compose.production.example.yml`
- `.env.production.example`

Verify:

- production never loads or resets demo seed data;
- normal production bootstrap refuses an empty application database;
- the one-shot initializer requires production mode and the owner URL, validates
  a strict credential-free manifest, reads the password only from
  `ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD`, serializes concurrent attempts, locks
  and rechecks every application table, and refuses populated state;
- initialization atomically creates the first matter, account, Senior
  membership, and root case/account audit events;
- initial password rotation and MFA-unenrolled state;
- runtime grant validation before API start and migration-secret removal from
  the Uvicorn environment;
- production CORS default-empty behavior and exact-HTTPS validation;
- secure cookies, metrics token, bounded limits, and forwarded-header trust;
- CSP (including the exact packet-style hash), API `no-store`, frame,
  content-type, referrer, and permissions headers; and
- non-root/read-only/capability-dropped container behavior.

## Release, backup, and restore automation

Review:

- `scripts/validate_release_package.sh`
- `scripts/production_launch_check.sh`
- `scripts/lib/production_compose.sh`
- `scripts/backup_db.sh`
- `scripts/backup_offsite.sh`
- `scripts/restore_drill.sh`
- `scripts/restore_db.sh`
- `scripts/smoke.sh`

Cover clean-package provenance, reviewed image-tag binding, deployment-before-
smoke ordering, rehearsal skip combinations, production Compose selection,
backup stdout contract, file modes/checksums/retention, encrypted offsite
acknowledgement, restore transactionality, API quiescence, and cleanup paths.

## Out of scope unless explicitly added

- Accuracy of legal conclusions in sample or real matter content.
- Cloud/provider controls outside this repository, including network ACLs,
  managed database encryption, secret manager, immutable storage, and SIEM.
- Penetration testing against third-party infrastructure not owned and
  authorized by the deployer.
- Legal sufficiency of HTML packets, privilege logs, hashes, or signatures.

## Evidence to provide

- Latest `bash scripts/verify.sh` output.
- Latest `bash scripts/test_ops.sh` output.
- Release-package validation result and reviewed source identifier.
- Production launch output showing the built tag, forced API recreation, health
  wait, and post-deploy smoke.
- Authenticated packet-flow smoke output.
- Production environment validation output.
- Greenfield initializer record for a first deployment.
- Case and account integrity-verification results.
- Backup/offsite copy and restore-drill records.
- Current [production readiness](../production-readiness.md) and
  [release gate](release-gate.md).
