# Full codebase review — 2026-07-18

## Executive result

Atlas Argus received a repository-wide correctness, security, evidentiary,
frontend, database, deployment, recovery, test, and documentation review.
No P0 issue was found. Three P1 issue groups and the actionable P2/P3 defects
described below were fixed in the reviewed working tree and covered by
regressions where practical.

The resulting code and production image pass the automated checks listed in
this report. That is not a production approval: independent application-
security and evidence-handling/legal review, deployment-specific controls, and
an evidenced restore drill remain mandatory release gates.

## Review prompt used

> Review Atlas Argus as a production-critical, multi-matter legal evidence
> system. Inspect every tracked source, configuration, migration, test, script,
> and document. Evaluate domain correctness and invariants; authentication,
> session, MFA, authorization, privilege, and tenant isolation; concurrency and
> transaction behavior; evidentiary integrity and auditability; production
> packet disclosure and XSS; frontend state races and accessibility; schema and
> migration safety; deployment, backup, restore, CI, observability, and supply-
> chain posture; and test/documentation accuracy. Rank findings P0–P3 by
> exploitability, data-loss potential, and legal-disclosure impact. Reproduce
> findings where possible, implement safe in-scope fixes, add regression tests,
> and verify against real PostgreSQL, the production frontend build, operations
> tests, a container build, and dependency advisory scans. Clearly separate
> fixed defects from residual operational or external-review risks. Do not
> describe application hash chains as WORM storage, external signatures, or
> trusted timestamps, and do not treat automated redaction as a substitute for
> legal review.

## Severity model

- **P0:** active compromise, catastrophic corruption, or a release-stopping
  defect with no safe containment.
- **P1:** credible unauthorized legal disclosure, destructive production
  operation, or a broken release/evidence boundary.
- **P2:** material defense-in-depth, integrity, availability, or correctness
  defect that needs remediation before high-assurance use.
- **P3:** hardening, maintainability, usability, or coverage weakness with lower
  immediate impact.

## P1 findings — fixed

### 1. Production packet confidentiality boundary

Non-included packet entries could retain internal source/claim metadata and
generator attribution in production artifacts. Related bootstrap and audit
views could also expose dangling identifiers or historical relationships for
records the current reviewer was not cleared to see.

The server and local renderers now use packet-local opaque references for
withheld/excluded entries and omit original title, text, internal assessment,
section/revision/hash identifiers, and privileged claim identifiers. Production
documents and persisted manifests omit generator identity. Non-cleared members
receive no generator attribution when reopening a production artifact, hidden
relationships are filtered, and history-aware audit filtering suppresses
indirect privilege leaks. Internal packets retain their stricter clearance
requirement.

Primary implementation: [packet.py](../server/src/atlas_argus/packet.py),
[packet.ts](../src/domain/packet.ts), [services.py](../server/src/atlas_argus/services.py),
and [routes.py](../server/src/atlas_argus/api/routes.py).

### 2. Production backup/restore target and file safety

Operational helpers could inherit a development Compose target, while backup
path, retention, same-second naming, checksum, and copy behavior did not provide
a sufficiently narrow destructive boundary for production evidence data.

Every backup, offsite, restore, and drill helper now selects the explicit
production Compose file and refuses the development definition. Shared safety
logic rejects root, broad/system/ancestor paths and symlinks; validates
retention; serializes backup creation; publishes atomically with unique names;
uses exact checksum sidecars; limits pruning depth; and stages private verified
copies before restore or offsite transfer.

Primary implementation: [backup_safety.sh](../scripts/lib/backup_safety.sh),
[backup_db.sh](../scripts/backup_db.sh), [restore_db.sh](../scripts/restore_db.sh),
and [test_ops.sh](../scripts/test_ops.sh).

### 3. Reviewed-image deployment provenance

The prior launch flow could validate/build one state yet run smoke tests against
an already-running API image. That could create false release evidence.

The production Compose API now uses an explicit reviewed image tag. The launch
check builds that tag, force-recreates and waits for that exact API image, and
only then runs smoke. Build/deploy/smoke rehearsal skips are paired so a skipped
deployment cannot be presented as production smoke evidence.

Primary implementation:
[production_launch_check.sh](../scripts/production_launch_check.sh),
[docker-compose.production.example.yml](../docker-compose.production.example.yml),
and [Dockerfile](../Dockerfile).

## P2 findings — fixed

- **Authentication throttling and recovery:** login and MFA throttles are now
  persistent PostgreSQL state with separate namespaces and row-locked updates.
  Enabled MFA cannot be silently replaced. An MFA-verified global Senior can
  perform an audited reset for another reviewer; self-reset is forbidden and
  target sessions are revoked.
- **Session containment:** the client consumes absolute server expiry, scrubs
  authenticated state on expiry/401/identity mismatch, revalidates periodically
  and on browser lifecycle events, and uses epochs so late requests cannot
  repopulate state after logout or matter/reviewer changes.
- **Role-plane and matter isolation:** global reviewer roles govern account
  administration; active per-matter membership governs evidence visibility and
  legal actions. Cross-matter identifiers are rejected and RLS remains a second
  enforcement layer.
- **Concurrency:** sorted row locking, required expected versions, advisory
  chain locks, and conflict handling prevent stale decisions, duplicate report
  references, divergent audit heads, and internally inconsistent packet
  snapshots. Stale writes return `409` and the client reloads current state.
- **Evidence integrity:** case/account audit, review decision, report revision,
  and packet sequences are append-only for the runtime role and protected by
  hash-chain verification. Artifact verification recomputes content and lineage
  relationships instead of trusting stored hashes.
- **Greenfield production initialization:** sample seeding is prohibited in
  production. A one-shot, production-only initializer accepts a credential-free
  manifest, reads the first password only from a transient environment value,
  verifies owner/runtime boundaries and an empty database, and atomically
  creates root records and audit events.
- **Production configuration:** secure cookies, fail-closed CORS, exact origin
  checks, constant-time metrics-token comparison, no-store API responses,
  bounded request/artifact sizes, scoped proxy trust, and distinct strong
  migration/runtime credentials are validated.
- **Database privileges:** production bootstrap rejects a runtime role that is
  missing, identical to the owner, superuser, `BYPASSRLS`, or owner of an
  application table; immutable tables deny runtime update/delete.
- **Container boundary:** the built API runs as uid 999 (`app`) with a read-only
  root filesystem, dropped capabilities, `no-new-privileges`, and a constrained
  temporary filesystem in production Compose.

Primary implementation areas: [auth.py](../server/src/atlas_argus/auth.py),
[store.tsx](../src/state/store.tsx), [integrity.py](../server/src/atlas_argus/integrity.py),
[initialize_production.py](../server/src/atlas_argus/db/initialize_production.py),
and [production-readiness.md](production-readiness.md).

## P3 findings — fixed or documented

- Packet list/detail requests, case loads, mutations, and administrator audit
  views now have independent latest-request/error state rather than allowing
  stale responses to overwrite current UI state.
- Integrity-failed artifacts are quarantined from open/download/print actions.
- Progress and form controls received semantic labels/status behavior.
- API requests have a client-side timeout and abort handling.
- Documentation now matches multi-matter behavior, the two role planes,
  session/MFA recovery, packet disclosure semantics, initialization, release
  provenance, and the actual limits of unkeyed hashes.
- CI and the local verification script include shell syntax/operations tests,
  production-environment validation, frontend checks, backend checks, and image
  construction.

## Verification evidence

All commands completed successfully in the reviewed working tree:

| Area | Result |
| --- | --- |
| Backend integration/regression suite | `172 passed`; one Starlette/httpx deprecation warning |
| Frontend tests | `108 passed` across 10 test files |
| Frontend typecheck and production build | passed; 339.64 kB JS, 98.34 kB gzip |
| Python lint and bytecode compilation | passed |
| Shell syntax and operations regression suite | passed |
| Production Compose/environment fixtures | passed; reviewed image tag resolved |
| Container build | passed; image `atlas-argus:review`; configured user `app` |
| JavaScript dependency advisories | 0 known vulnerabilities, including dev dependencies |
| Locked Python dependency advisories | 0 known vulnerabilities |
| Installed Python dependency compatibility | all 34 packages compatible |
| Patch hygiene | `git diff --check` passed |

The backend suite used the real PostgreSQL test service, including migration
upgrade/backfill, transaction locking, RLS, append-only triggers, and concurrent
write regressions. Advisory scans are point-in-time evidence, not a guarantee
against future disclosures.

## Residual risks and release conditions

These are intentionally not represented as fixed:

1. **Independent approval is outstanding.** Complete the application-security
   and evidence-handling/legal reviews, record both sign-offs, and perform an
   evidenced restore drill before a production decision.
2. **Hash chains are co-located and unkeyed.** They detect accidental/runtime-
   role tampering but are not external signatures, trusted timestamps, or WORM
   storage. A schema owner can rewrite data and recompute them.
3. **TOTP secrets lack application-level envelope encryption.** Their
   confidentiality depends on database, storage, backup, and access controls.
4. **RLS is defense in depth.** Possession of the runtime database credential
   permits setting the transaction-local reviewer context; credential
   protection and SQL-injection prevention remain critical.
5. **Approved prose remains a human disclosure boundary.** Structured
   redaction cannot detect privileged substance manually repeated in approved
   report text.
6. **MFA recovery requires two administrators.** At least two independently
   controlled global Seniors and an approved identity-verification ceremony are
   required; there are no backup codes or self-service recovery.
7. **Deployment controls are operator-owned.** Off-host encryption and key
   rotation, legal-hold retention, alerting, incident response, recurring
   restore drills, TLS, and network exposure must be implemented and evidenced
   in the target environment.
8. **Two packet renderers remain.** TypeScript local/demo and Python server
   implementations must remain synchronized; automated tests reduce but do not
   eliminate doctrine drift.
9. **Output and workflow gaps remain.** Export is self-contained HTML/browser
   print-to-PDF, and normal post-initialization matter create/import/search is
   not implemented.

The operational go/no-go checklist is
[release-gate.md](deploy/release-gate.md). The detailed current control record
and limitations are in [production-readiness.md](production-readiness.md).

## Working-tree note

The review was performed in the existing working tree. Changes are intentionally
left uncommitted for owner inspection; no unrelated work was reset or deleted.
