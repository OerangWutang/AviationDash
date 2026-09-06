# Production release gate

Use this checklist before Atlas Argus handles a real matter. Passing automation
is necessary but not sufficient; deployment controls, recovery procedures, and
independent legal/security approval remain required.

## Reviewed source and verification

- `bash scripts/verify.sh` passes against a migration-built PostgreSQL schema.
- `scripts/validate_release_package.sh` passes for the exact tracked tree or
  extracted archive being deployed.
- The source tree is clean. The production image is built from that reviewed
  commit/archive, not from a different or dirty directory.
- The dependency lockfiles and migration history are included in review.
- The npm and Python dependency audits pass. CI reports the complete production
  image vulnerability set, blocks fixable critical findings, and publishes the
  image's SPDX JSON SBOM. Unfixed high/critical base-image findings are reviewed
  and dispositioned in the release record rather than being treated as absent.
- Independent reviewers receive the final verification output and release
  identifier.

## Production image and launch

- `ATLAS_ARGUS_PRODUCTION_COMPOSE_FILE` names the reviewed production Compose
  definition and is not `docker-compose.yml`.
- `ATLAS_ARGUS_IMAGE_TAG` identifies the reviewed API image.
- Run `scripts/production_launch_check.sh` from inside the extracted reviewed
  package. It must:
  - validate production configuration and package contents;
  - run the full gate in an isolated development Compose project;
  - build a non-demo image at `ATLAS_ARGUS_IMAGE_TAG`;
  - start/wait for the production DB without recreating it;
  - force-recreate and wait for `api` from that exact tag;
  - start proxy and monitoring; and
  - run smoke only after the reviewed API is healthy.
- `ATLAS_ARGUS_RELEASE_PACKAGE` is used only for validation-only rehearsals. The
  launch script refuses to validate one directory and build another.
- Rehearsal skips are paired and require `ATLAS_ARGUS_REHEARSAL=1`:
  `ATLAS_ARGUS_SKIP_IMAGE_BUILD=1`, `ATLAS_ARGUS_SKIP_DEPLOY=1`, and
  `ATLAS_ARGUS_SKIP_SMOKE=1`. A run containing any skip prints a non-certifying
  rehearsal result and cannot print production success.
- Every security, evidence, and restore sign-off record contains an exact
  `candidate: <image-id>` line for `ATLAS_ARGUS_RELEASE_CANDIDATE_ID`.
- The launch script records the previous API image and automatically restores
  it if candidate health or deployed smoke fails.

## First production database only

- Normal production bootstrap is not used to create the first matter; it
  intentionally refuses an empty database.
- While API traffic is stopped, follow
  [greenfield production initialization](greenfield-initialization.md).
- The manifest is credential-free but protected as confidential deployment
  data; it contains matter and counsel metadata.
- `ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD` is injected only into the one-shot
  initializer. It is absent from the manifest, command line, persistent env
  file, and captured logs, and is unset afterward.
- The initializer output and root case/account audit events are captured in the
  release record.
- A second initializer invocation against the populated database is observed to
  refuse. Never empty a database to make it rerunnable.

## Authentication and authorization checks

- Production cookies are observed as `Secure`, `HttpOnly`, and `SameSite=Lax`.
- Login/session responses carry a valid absolute `expiresAt`; expiry, focus
  revalidation, sign-out, and a mid-session 401 scrub authenticated case state.
- A provisioned account cannot use case routes before forced password rotation.
- Password change revokes other sessions; reviewer deactivation invalidates
  active sessions.
- Login and MFA throttles are exercised and persist across API processes.
- MFA enrollment and per-session verification work for a production-style
  account.
- At least two separately controlled global Senior Aviation Counsel accounts
  have rotated their passwords and enrolled MFA.
- From one verified Senior session, resetting another reviewer's MFA revokes all
  target sessions, clears the target throttle, records one account-audit event,
  and requires new enrollment. Self-reset is refused.
- High-risk routes refuse a password-only session: decisions, approval, packet
  operations, integrity checks, and administration.
- Review confirms the two role planes:
  - global account role controls reviewer/account administration;
  - `caseMembership.role` controls matter evidence visibility and legal acts.
- A non-Senior matter role cannot retrieve withholding-privilege evidence or
  dependent records, regardless of global account role.
- Cross-matter object IDs are rejected and inactive membership removes matter
  access.

## Packet and integrity checks

- Authenticated packet-flow smoke passes against the deployed URL:

  ```bash
  ATLAS_ARGUS_SMOKE_PACKET_FLOW=1 \
  ATLAS_ARGUS_SMOKE_USERNAME=<senior-counsel-user> \
  ATLAS_ARGUS_SMOKE_PASSWORD=<password> \
  ATLAS_ARGUS_SMOKE_MFA_CODE=<current-code> \
  ATLAS_ARGUS_SMOKE_CASE_ID=<case-id> \
  bash scripts/smoke.sh
  ```

- `ATLAS_ARGUS_SMOKE_ALLOW_MFA_ENROLL=1` is allowed only for a local/demo
  rehearsal; never auto-enroll a real production account through smoke.
- Production packet generation excludes unapproved sections, withholds
  privileged content, and strips internal assessments, decision reasoning, reviewer
  identity, and generator identity from the document and persisted manifest.
- Withheld/excluded manifest entries use packet-local opaque references and
  generic metadata, expose no original title/text/citations/internal impact or
  internal section/revision/claim IDs or revision hashes, and do not put privileged
  claims in `citedClaimIds` or the evidence appendix. The privilege log contains
  only approved source-identity fields.
- Counsel reviews approved section prose for manually repeated privileged
  substance; structured redaction cannot classify the semantics of authored
  text.
- A non-cleared active member can reopen a redacted production artifact but
  receives no generator ID/name/role in artifact metadata.
- A non-cleared member cannot reopen an internal packet.
- An integrity failure disables open/download/print in the UI.
- Case integrity verification covers case audit, decisions, section revisions,
  packet sequence, extraction records, stored PDF bytes, and the latest external
  checkpoint. Global account-audit verification also passes.
- A fresh checkpoint is created with
  `python -m atlas_argus.db.create_integrity_anchor`, copied to independently
  controlled WORM storage, and then observed as `matched` by verification.

## Production configuration

- `scripts/validate_production_env.sh` passes with `ATLAS_ARGUS_ENV=production`.
- `ATLAS_ARGUS_MIGRATION_DB_PASSWORD` and
  `ATLAS_ARGUS_RUNTIME_DB_PASSWORD` are generated, distinct, at least 16
  URI-unreserved characters, and not defaults. `openssl rand -hex 32` produces
  a compatible value.
- `ATLAS_ARGUS_DATABASE_URL` uses the non-owner `atlas_app` role.
- `ATLAS_ARGUS_MIGRATION_DATABASE_URL` uses the `atlas_owner` schema owner and
  is absent from the final Uvicorn process environment.
- The runtime role is not superuser, does not bypass RLS, and owns no application
  table.
- `ATLAS_ARGUS_METRICS_TOKEN` is generated and configured as the monitoring
  bearer credential.
- Packaged same-origin production leaves `ATLAS_ARGUS_CORS_ORIGINS` empty. Any
  approved cross-origin entry is an exact HTTPS origin.
- `ATLAS_ARGUS_FORWARDED_ALLOW_IPS` trusts only the reverse proxy address/CIDR.
- Request and packet artifact limits are explicitly set and match proxy limits.
- `ATLAS_ARGUS_API_MEMORY_LIMIT_BYTES` matches the Compose `mem_limit`; preflight
  proves admitted ingestion children and request/render overhead fit it.
- The integrity checkpoint key is at least 32 bytes with mode `0600`, and the
  checkpoint directory/key are mounted read-only into the API container.
- TLS terminates before API traffic; direct public access to the API container is
  blocked by deployment network policy.
- `/api/metrics` rejects an unauthenticated request and is successfully scraped
  with its token.
- JSON request logs are collected centrally; API responses are observed with
  `Cache-Control: no-store`, CSP, frame, content-type, referrer, and permissions
  headers.

## Backup and recovery operations

- `ATLAS_ARGUS_BACKUP_DB_USER=atlas_owner` for complete production dumps.
- Backup, offsite validation, restore, and restore-drill helpers select
  `ATLAS_ARGUS_PRODUCTION_COMPOSE_FILE` themselves and refuse the development
  Compose file.
- `scripts/backup_db.sh` runs on schedule and produces a mode-`0600` dump plus a
  valid `.sha256` sidecar.
- The dump and sidecar are copied off-host with
  `scripts/backup_offsite.sh` and
  `ATLAS_ARGUS_BACKUP_OFFSITE_ENCRYPTED=1` to an independently encrypted/rclone
  crypt destination.
- Retention, legal hold, encryption-key ownership/rotation, and job alerts are
  approved.
- A non-destructive `scripts/restore_drill.sh` succeeds and its output is stored
  at `ATLAS_ARGUS_RESTORE_DRILL_RECORD`.
- A live restore is performed only during a maintenance window with API traffic
  stopped and both `CONFIRM_API_QUIESCED=1` and `CONFIRM_RESTORE=1`.

## Required review sign-offs

- Independent application security review using
  [security-review-scope.md](security-review-scope.md).
- Evidence-handling/legal review using
  [evidence-handling-review.md](evidence-handling-review.md).
- Incident-response owner assigned.
- Reviewer provisioning, matter membership, and privilege-clearance process
  approved by Senior Aviation Counsel.
- Backup retention and legal-hold policy approved.
- Review artifacts are non-empty, current, and linked from the launch record:
  - `ATLAS_ARGUS_SECURITY_REVIEW_SIGNOFF`
  - `ATLAS_ARGUS_EVIDENCE_REVIEW_SIGNOFF`
  - `ATLAS_ARGUS_RESTORE_DRILL_RECORD`
- `scripts/launch_gate.sh` passes only after those records exist.

## Release decision and residual risks

Do not approve production use unless the two-Senior MFA requirement above is
met and the organization has approved how an administrator verifies a target's
identity before resetting the factor. Atlas Argus has no self-service recovery
or backup codes; self-reset is forbidden, and password reset deliberately
preserves MFA. Record that administrator-mediated dependency in the operating
and incident-response procedures.

Other accepted residual risks must be recorded explicitly:

- RLS is defense in depth and does not contain a stolen runtime DB credential.
- Hash chains are unkeyed and co-located with the database.
- Local and server modes have separate packet renderers and different approval
  semantics.
- Server mode supports controlled matter creation and switching, but not bulk
  matter import or full-text matter search.
- Production export uses the stored, server-rendered, hash-stamped PDF.
  Browser printing remains a non-authoritative preview path.
