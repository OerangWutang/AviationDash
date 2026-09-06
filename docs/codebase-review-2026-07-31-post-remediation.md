# Post-remediation repository review - 2026-07-31

This report continues the finding sequence in
`codebase-review-2026-07-30-follow-up.md`. It reviews the repository after the
F01-F29 remediation work and records new or residual findings as F30-F42.
No application code was changed as part of this review.

## 1. Executive summary

The repository is substantially healthier than it was before F01-F29. The
backend and frontend have broad automated coverage, integrity records are more
complete, production configuration is validated aggressively, and release,
backup, and packet workflows now have explicit controls. However, this review
found four high-severity confirmed defects and nine medium/low findings.

The most serious risks are:

- the external integrity checkpoint accepts a replaced chain whenever its row
  count has increased;
- unauthenticated request bodies are fully buffered before authentication or
  workload admission, allowing aggregate memory exhaustion;
- production packet retrieval exposes and verifies hidden internal packet-chain
  state for users who are not cleared to see internal work product;
- a release remains live when later backup, restore, or final-signoff gates fail.

The system should not be treated as production-ready for privileged legal work
until F30-F34 are resolved. F35-F41 are stabilization requirements because they
affect workload isolation, credential consistency, migration reproducibility,
recovery assurance, and operational monitoring.

Review coverage included backend routes/services/authentication, persistence and
migrations, packet generation/retrieval, integrity chains and external anchors,
frontend local persistence, CI, production Compose/Caddy examples, release and
restore scripts, and relevant tests. This review did not verify a real WORM
anchor store, production proxy behavior, a live GitHub Actions run, production
data volumes, cloud IAM, independent legal evidentiary sufficiency, or a restore
against an actual production backup.

## 2. Critical and high-severity findings

### F30 - External checkpoints do not prove that an advanced chain retains the signed prefix

- **Classification:** Confirmed defect
- **Severity:** High
- **Confidence:** High
- **Category:** Data integrity, tamper evidence
- **Affected:** `server/src/atlas_argus/integrity_anchors.py:20-47`,
  `server/src/atlas_argus/integrity_anchors.py:113-190`,
  `server/tests/test_integrity_anchors.py:36-58`
- **Problem:** The signed checkpoint stores only each chain's current count and
  root. During verification, any current count greater than the signed count is
  classified as `advanced`; the signed root is not compared with the root at the
  signed ordinal and no prefix proof is performed.
- **Trigger:** Delete or rewrite records covered by an anchor, recompute the
  mutable database chain, and append enough replacement rows that the current
  count exceeds the anchored count.
- **Expected:** The verifier proves that the exact signed chain is a prefix of the
  current chain and rejects replacement, deletion, or reordering within it.
- **Actual:** The verifier returns `ok: true` and `status: advanced` based solely
  on the larger count. A direct reproduction anchored count 2/root A and then
  supplied count 3/root B; it was accepted.
- **Impact:** A database owner or attacker with database write access can rewrite
  anchored audit, packet, section, decision, or extraction history without the
  external checkpoint detecting it, defeating the principal purpose of F29.
- **Root cause:** A terminal root is not a prefix-verification data structure, and
  the verifier assumes monotonic count implies append-only history.
- **Recommended fix:** For linear chains, recompute and compare the root at the
  anchored ordinal before accepting later rows. For aggregate section and
  extraction roots, store a signed member/tip manifest or use a versioned Merkle
  or incremental accumulator that supports prefix proofs. Treat advancement as a
  failure until the proof succeeds. Prefer asymmetric signatures so the API can
  verify with a public key while only the external writer holds the private key.
- **Tests required:** Replace an anchored prefix and append rows; replace one
  aggregate member and increase aggregate count; reorder records; verify a valid
  append; verify an exact match; verify rollback and malformed proofs.
- **Related:** F38. The current HMAC key is mounted into the application runtime,
  so host or process compromise can also forge checkpoints.

### F31 - Anonymous request buffering can exhaust aggregate process memory

- **Classification:** Confirmed defect
- **Severity:** High
- **Confidence:** High
- **Category:** Security, availability, resource management
- **Affected:** `server/src/atlas_argus/api/app.py:49-130`,
  `server/src/atlas_argus/api/routes.py:379-393`,
  `server/src/atlas_argus/config.py:224-233`,
  `server/src/atlas_argus/config.py:533-545`,
  `docs/deploy/Caddyfile.example:14-17`,
  `docker-compose.production.example.yml:35`,
  `server/tests/test_limits.py:72-81`,
  `server/tests/test_limits.py:108-149`
- **Problem:** The global body-limit middleware reads and retains every request
  body before route authentication, Pydantic decoding, or the extraction
  admission semaphore. Configuration budgets one largest request plus admitted
  extraction children, but it does not cap concurrent anonymous body buffers.
- **Trigger:** Open many concurrent unauthenticated slow or full-size POSTs to a
  large-body endpoint such as source upload. Each connection can retain roughly
  the configured JSON/base64 envelope independently.
- **Expected:** Authentication and global workload admission bound aggregate
  memory before a large body is accepted, and the reverse proxy bounds abusive
  connection rates and read times.
- **Actual:** Every request may buffer tens of megabytes. The example Caddy
  configuration has no connection/rate/read-body control, while the production
  container has a 1.5 GiB memory limit.
- **Impact:** A remote unauthenticated actor can cause OOM termination, restart
  loops, and loss of service. Legitimate uploads can produce the same failure
  during bursts.
- **Root cause:** Limits are enforced per request, while the memory model assumes
  globally admitted requests; authentication and admission occur too late.
- **Recommended fix:** Authenticate from headers before consuming large bodies,
  acquire an application-wide body-buffer slot, stream uploads into bounded
  temporary storage, and reject excess work before buffering. Add proxy request
  rate, concurrent connection, body-size, and read-time controls.
- **Tests required:** Concurrent anonymous uploads must stay within a measured
  memory/concurrency bound; rejected requests must stop reading promptly; slow
  clients must release slots on timeout/disconnect; authenticated admitted
  uploads must continue to work.
- **Related:** F36. Both show that configured per-operation limits do not bound
  aggregate work.

### F32 - Production packet readers receive hidden internal chain state and trigger privileged verification

- **Classification:** Confirmed defect
- **Severity:** High
- **Confidence:** High
- **Category:** Authorization, privilege confidentiality, denial of service
- **Affected:** `server/src/atlas_argus/services.py:1446-1493`,
  `server/src/atlas_argus/services.py:1591-1636`,
  `server/src/atlas_argus/services.py:1700-1734`,
  `server/tests/test_packet_retrieval.py:141-159`
- **Problem:** A user allowed to retrieve a production packet is not allowed to
  retrieve internal packets, but packet detail returns full-chain metadata and
  issues. List, detail, and download also verify the entire packet chain,
  including hidden internal packet PDFs.
- **Trigger:** Generate at least one internal packet followed by a production
  packet, then retrieve or list the production packet as a reviewer without
  internal-packet clearance. Corrupting the hidden packet makes the leak more
  explicit because status/issues can expose its existence and identifier.
- **Expected:** A production-only reader sees the requested production artifact
  and its self-integrity result, with no information or work derived from hidden
  privileged artifacts.
- **Actual:** Detail can expose total count, root, checked count, and issue data
  for the complete chain. List suppresses some fields but still returns a status
  derived from hidden rows and performs their expensive verification.
- **Impact:** Confidential internal work-product existence and integrity state can
  leak horizontally. An authorized low-privilege user can repeatedly force the
  server to read and hash privileged PDFs they cannot access.
- **Root cause:** Artifact authorization is applied to the target row, while
  integrity verification and serialization operate on the unfiltered case-wide
  chain.
- **Recommended fix:** Return target-artifact self-integrity only to
  production-only readers. Restrict full-chain verification and issue details to
  cleared users/owner operations. Do not label a complete-chain status as
  `authorized-visible`; either compute a genuinely authorized subset proof or
  omit it.
- **Tests required:** Production-only detail/list/download after internal packet
  creation; hidden packet corruption; assertions that no hidden identifier,
  count, root, status, or PDF query is observable; cleared-user control cases.
- **Related:** F35 amplifies the availability impact of this authorization error.

### F34 - Post-deployment certification failures leave the candidate release live

- **Classification:** Confirmed defect
- **Severity:** High
- **Confidence:** High
- **Category:** Deployment safety, rollback, operational integrity
- **Affected:** `scripts/production_launch_check.sh:155-221`
- **Problem:** The script arms rollback before deploying the candidate, but
  clears `rollback_needed` immediately after smoke and anchor checks. Backup,
  offsite-copy, restore-drill, and final candidate-bound signoff gates execute
  after rollback has been disabled.
- **Trigger:** The candidate passes smoke and anchor checks, then backup creation,
  encrypted offsite copy, restore drill, or final launch-gate validation fails.
- **Expected:** Any mandatory certification failure restores the previously
  serving image, or the candidate remains isolated until every gate succeeds.
- **Actual:** The script exits nonzero but the candidate remains serving. The
  proxy is also started before final signoffs are validated.
- **Impact:** Operators can receive a failed launch result while an unapproved or
  unrecoverable release is still handling production traffic.
- **Root cause:** Rollback scope covers only early technical checks rather than
  the complete release transaction.
- **Recommended fix:** Keep rollback armed until all required gates succeed.
  Prefer pre-validating candidate-bound signoffs and performing backup/restore
  certification before traffic cutover, or use a staged blue/green promotion.
- **Tests required:** Mock failure at every command after deployment and assert
  that the old image is restored and proxy routing is unchanged; test success;
  test rollback failure reporting.
- **Related:** F33 and F41 weaken the release controls surrounding this path.

## 3. Medium and low-severity findings

### Delivery and operations

#### F33 - The checked-in GitHub Actions backend job cannot pass its own production fixtures

- **Classification:** Confirmed defect
- **Severity:** Medium
- **Confidence:** High
- **Category:** CI/CD, configuration drift
- **Affected:** `.github/workflows/ci.yml:77-125`,
  `scripts/validate_production_env.sh:89-92`,
  `scripts/validate_production_env.sh:121`,
  `scripts/launch_gate.sh:14-34`
- **Problem:** The workflow's production environment omits the required encrypted
  offsite-backup flag and integrity anchor directory/key. Its launch fixture also
  omits the release candidate ID and the candidate binding required in each
  signoff file.
- **Trigger:** Run the checked-in backend GitHub Actions job.
- **Expected:** The repository's canonical CI fixture satisfies current
  production validation and launch-gate contracts.
- **Actual:** Local execution of the exact environment produced six validation
  failures. The launch-gate fixture is independently guaranteed to fail its
  candidate-binding checks.
- **Impact:** Pull requests cannot receive a green canonical backend check;
  maintainers may bypass CI or lose trust in production validation.
- **Root cause:** Validation contracts changed without updating the duplicated CI
  fixture.
- **Recommended fix:** Create temporary anchor storage and a mode-0600 key in CI,
  set encrypted offsite backup, set a candidate ID, and bind all signoff records
  to it. Centralize the fixture in a reusable script and run `actionlint`.
- **Tests required:** Execute the production validator and launch gate from the
  same reusable fixture locally and in GitHub Actions.
- **Related:** F37 is another instance of executable historical/configuration
  drift not covered by a repository-wide invariant test.

#### F38 - No recurring external checkpoint writer or freshness alert is shipped

- **Classification:** Missing test or safeguard
- **Severity:** Medium
- **Confidence:** High
- **Category:** Integrity operations, observability
- **Affected:** `docker-compose.production.example.yml:27-30`,
  `server/src/atlas_argus/db/create_integrity_anchor.py:16-39`,
  `scripts/production_launch_check.sh:183-187`,
  `docs/deploy/evidence-handling-review.md:51-73`
- **Problem:** Compose comments describe a separate scheduled owner process, but
  the repository contains only a launch-time/manual all-matter command. There is
  no scheduler unit, freshness policy, or stale/missing-anchor alert.
- **Trigger:** Create a matter after deployment or mutate an existing matter
  after the launch-time anchor without an operator manually rerunning the command.
- **Expected:** Every matter receives checkpoints within a documented maximum
  age, and missing/stale/advanced checkpoints alert operators.
- **Actual:** New matters remain `missing` and changed matters remain `advanced`
  indefinitely. The unbounded all-matter CLI performs the work synchronously.
- **Impact:** The tamper-detection window is undefined and can silently extend for
  the lifetime of a deployment.
- **Root cause:** The external trust boundary is documented but its required
  control plane is not implemented or packaged.
- **Recommended fix:** Ship a systemd timer, cron, or orchestrator job example;
  define checkpoint cadence/SLO and alerting; copy checkpoints to immutable
  offsite storage; make checkpointing incremental and observable.
- **Tests required:** Scheduler integration test, new-matter checkpoint test,
  freshness alert test, partial-run recovery, and concurrent writer test.
- **Related:** F30 must be fixed before recurring checkpoints can provide the
  claimed assurance.

#### F41 - The restore drill can pass a logically corrupt or incompatible backup

- **Classification:** Missing test or safeguard
- **Severity:** Medium
- **Confidence:** High
- **Category:** Disaster recovery, data integrity
- **Affected:** `scripts/restore_drill.sh:35-47`
- **Problem:** Restore certification checks only that `case_file`, `reviewer`, and
  `audit_event` contain rows after `pg_restore`.
- **Trigger:** Restore a dump with those three tables populated but with an old
  migration revision, broken grants/RLS, missing packet bytes, corrupt integrity
  chains, or inaccessible related records.
- **Expected:** A drill proves the restored database is schema-compatible,
  internally consistent, accessible under runtime roles, and usable by the app.
- **Actual:** Three nonzero row counts are sufficient for success.
- **Impact:** A backup can be certified and attached to release evidence despite
  being unusable or incomplete during an incident.
- **Root cause:** The drill validates presence, not recoverability or integrity.
- **Recommended fix:** Verify Alembic head/compatibility, boot the app against the
  restored database, test runtime grants and RLS, run owner-mode case integrity
  verification, verify representative packet bytes, and sample critical
  relationships.
- **Tests required:** Deliberately stale schema, removed grants, corrupt chain,
  missing packet blob, and successful full restore cases.
- **Related:** F34 makes a weak restore record part of the release promotion path.

### Performance and concurrency

#### F35 - Paginated packet retrieval rehashes the full unbounded packet history

- **Classification:** Confirmed defect
- **Severity:** Medium
- **Confidence:** High
- **Category:** Performance, scalability, availability
- **Affected:** `server/src/atlas_argus/services.py:1044-1099`,
  `server/src/atlas_argus/services.py:1496-1518`,
  `server/src/atlas_argus/services.py:1591-1636`,
  `server/src/atlas_argus/services.py:1700-1734`,
  `server/src/atlas_argus/config.py:177-200`
- **Problem:** Every packet list/detail/download verifies every historical packet
  and performs a separate PDF fetch/hash per row. Pagination applies only after
  this work.
- **Trigger:** Repeatedly list one page in a matter containing many large packet
  PDFs. At default limits, 1,000 packets of up to 20 MB imply up to roughly 20 GB
  read and hashed per request plus approximately 1,000 PDF queries.
- **Expected:** Request cost is proportional to the requested page or artifact;
  complete forensic verification is an explicit bounded/background operation.
- **Actual:** Cost is proportional to total case history and artifact bytes.
- **Impact:** Latency, database I/O, CPU, and memory pressure grow without relation
  to page size and can be abused by any authorized reader.
- **Root cause:** Verification is recomputed synchronously from stored blobs for
  every read and is coupled to response serialization.
- **Recommended fix:** Store immutable verification receipts/status, verify only
  the requested artifact on detail/download, batch metadata reads, and run full
  chain verification as an owner-only paginated/background job.
- **Tests required:** Query-count and bytes-read assertions across growing packet
  histories; page-size proportionality; cached receipt invalidation; explicit
  forensic verification behavior.
- **Related:** F32 causes this work to include hidden privileged artifacts.

#### F36 - Packet entry limits are enforced only after unbounded expansion under a case-wide lock

- **Classification:** Highly likely defect
- **Severity:** Medium
- **Confidence:** High
- **Category:** Performance, concurrency, transaction scope
- **Affected:** `server/src/atlas_argus/services.py:3612-3756`,
  `server/src/atlas_argus/packet.py:130-235`
- **Problem:** Packet generation acquires the case audit lock, selects and expands
  all sections with repeated related-record queries, then checks the configured
  entry limit. It renders the PDF while the database transaction and case-wide
  lock remain held.
- **Trigger:** Generate a packet for a large matter, especially one over the entry
  limit or with a slow renderer, while another user tries to mutate the matter.
- **Expected:** A bounded preflight rejects oversized work before expansion;
  related data is batch loaded; CPU-heavy rendering does not monopolize a
  database connection and mutation lock.
- **Actual:** Over-limit matters still incur the expensive build, and concurrent
  case mutations block for the full build/render duration.
- **Impact:** Long lock waits, connection-pool exhaustion, avoidable N+1 queries,
  and high latency under production-sized matters.
- **Root cause:** Validation and snapshot boundaries are placed after expansion,
  and rendering is inside the integrity transaction.
- **Recommended fix:** Query `limit + 1` before expansion, batch preload dependent
  rows, capture a coherent versioned snapshot quickly, render outside the lock,
  then recheck snapshot/version before atomically recording the artifact. A
  background job is preferable for large packets.
- **Tests required:** Over-limit case performs bounded queries and never invokes
  the renderer; query-count scaling; concurrent mutation during a blocked render;
  stale-snapshot rejection; crash/retry behavior.
- **Related:** F31 demonstrates the same gap between per-item limits and actual
  aggregate resource use.

### Authentication and state integrity

#### F39 - Self-service and administrative credential mutations do not share a concurrency boundary

- **Classification:** Highly likely defect
- **Severity:** Medium
- **Confidence:** High
- **Category:** Authentication, concurrency, state transitions
- **Affected:** `server/src/atlas_argus/services.py:118-126`,
  `server/src/atlas_argus/services.py:2302-2357`,
  `server/src/atlas_argus/auth.py:431-508`,
  `server/src/atlas_argus/api/routes.py:261-278`
- **Problem:** Admin password/MFA resets use a reviewer security advisory lock,
  while self-service password changes and MFA enrollment/enablement do not use
  that lock or a row version.
- **Trigger:** A user changes a password while an owner resets it, or enables MFA
  while an owner resets MFA, in concurrent transactions.
- **Expected:** One defined transition wins atomically; forced-password-change,
  factor state, and session revocation remain coherent.
- **Actual:** Last commit wins individual row fields. The user can receive success
  for credentials that immediately lose, or overwrite the admin reset's required
  next state. Session revocation does not make the credential state deterministic.
- **Impact:** Confusing account recovery, unexpected credential validity, and a
  possible bypass of an administrator's forced reset intent.
- **Root cause:** The lock is scoped by API path rather than by the credential
  aggregate/state machine.
- **Recommended fix:** Use the same reviewer security lock and row reload/version
  check for every password and MFA mutation. Define conflict semantics and commit
  state plus session invalidation as one transition.
- **Tests required:** Barrier-controlled concurrent self-change/admin-reset and
  MFA-enable/admin-reset tests, including both commit orders and session results.
- **Related:** Treat future credential operations as one explicit state machine,
  not independent field updates.

#### F40 - Local snapshots accept impossible domain states and can report a failed reset as successful

- **Classification:** Confirmed defect
- **Severity:** Low
- **Confidence:** High
- **Category:** Frontend validation, persistence, error handling
- **Affected:** `src/state/persistence.ts:76-210`,
  `src/state/persistence.ts:286-293`,
  `src/state/store.tsx:1877-1881`,
  `src/state/persistence.test.ts`
- **Problem:** Snapshot validation checks primitive shapes and some uniqueness but
  not enum membership, confidence ranges, positive versions, parseable dates,
  cross-record references, case ownership, or active-reviewer validity. Storage
  removal errors are swallowed while reset success is dispatched.
- **Trigger:** Load a syntactically valid snapshot containing `confidence: 999`, an
  unknown status, or a conflict referencing a missing claim; alternatively make
  `localStorage.removeItem` throw during reset.
- **Expected:** Impossible snapshots are rejected/quarantined, and reset reports
  failure if durable state was not removed.
- **Actual:** The malformed snapshot is accepted into typed application state. A
  failed removal still appears successful and the stale snapshot returns on
  reload.
- **Impact:** Local/demo users can see inconsistent calculations or broken views;
  failed reset can unexpectedly resurrect sensitive local state.
- **Root cause:** Handwritten shape checks have drifted from domain invariants and
  persistence APIs do not propagate errors.
- **Recommended fix:** Use a runtime schema generated from/shared with domain
  contracts plus a cross-record validation pass. Return a failure from
  `clearSnapshot` and dispatch reset success only after confirmed removal.
- **Tests required:** Every enum/range/date boundary, dangling references,
  cross-case records, missing reviewer, storage read/write/remove exceptions, and
  stale-state reload after failed reset.
- **Related:** The server remains authoritative in connected mode, limiting this
  finding primarily to local persistence.

### Migrations and maintainability

#### F37 - Historical migrations still import mutable runtime modules

- **Classification:** Maintainability issue
- **Severity:** Medium
- **Confidence:** High
- **Category:** Migration reproducibility, architecture
- **Affected:** `server/alembic/versions/0001_baseline.py:15`,
  `0004_reviewer_roles.py:12`, `0005_attorney_review.py:13`,
  `0007_evidence_integrity.py:13`, `0012_extraction_integrity_chain.py:14-19`,
  `0015_external_auth_identity.py:23`, `0016_reviewer_status.py:19-20`,
  `server/tests/test_migration_reproducibility.py:7-13`
- **Problem:** Multiple immutable historical revisions import current runtime
  guards or integrity functions. Only revision 0017 is protected by the existing
  reproducibility test.
- **Trigger:** Refactor a runtime guard constant, canonicalization rule, or hash
  helper, then initialize a fresh database or upgrade one that has not yet
  crossed the affected revision.
- **Expected:** A migration revision executes the same logic for the lifetime of
  the repository.
- **Actual:** Historical behavior can change with current application code,
  producing different backfill hashes, failures, or drift between old and fresh
  installations.
- **Impact:** Non-reproducible schema/data state and risky rolling upgrades.
- **Root cause:** Runtime code was reused instead of freezing revision-local
  behavior.
- **Recommended fix:** Inline/freeze migration-specific SQL, constants, and hash
  algorithms or import versioned migration-only helpers. Add a repository test
  forbidding `atlas_argus.*` imports from every historical revision.
- **Tests required:** Fresh install and incremental upgrade paths; invariant scan
  over all migrations; fixed expected hashes for backfill revisions.
- **Related:** Review adjacent-version compatibility before rolling deployment.

#### F42 - Production readiness documentation describes the superseded integrity design

- **Classification:** Maintainability issue
- **Severity:** Low
- **Confidence:** High
- **Category:** Documentation, operational consistency
- **Affected:** `docs/production-readiness.md:58-60`,
  `docs/production-readiness.md:124-126`,
  `docs/deploy/evidence-handling-review.md:51-73`
- **Problem:** The readiness document says chains are only database-local and
  recommends adding external signing, while the evidence document and runtime
  claim an implemented external HMAC checkpoint design.
- **Trigger:** An operator or reviewer uses the readiness document to assess the
  deployed control set.
- **Expected:** One current description explains what is implemented, its known
  limitations, and the external operational dependencies.
- **Actual:** The repository gives contradictory descriptions of a central
  evidentiary control.
- **Impact:** Incorrect launch decisions, duplicated work, or an unjustified
  belief that a control is absent/present.
- **Root cause:** Remediation changed the architecture without updating all
  canonical documentation.
- **Recommended fix:** Update readiness documentation after F30/F38 are resolved;
  identify one canonical control description and link to it elsewhere.
- **Tests required:** Documentation link/checklist review in the release process;
  no code test is sufficient.
- **Related:** F30 means the documentation must not overstate the current anchor's
  assurance while it is being corrected.

## 4. Cross-cutting inconsistencies

1. **Authorization scope versus verification scope:** Packet authorization is
   target-specific, while verification is case-wide and unfiltered (F32/F35).
2. **Per-operation limits versus aggregate resource limits:** Request and packet
   limits exist, but are applied after or outside the expensive operation
   (F31/F36).
3. **Integrity claims versus proof structure:** Documentation describes signed
   checkpoints, but the stored structure cannot prove prefix continuity (F30).
4. **Release policy versus executable fixtures:** Production validation and
   candidate-bound signoffs are mandatory in scripts but absent from CI fixtures
   (F33).
5. **Credential aggregate ownership:** Administrative mutations are serialized;
   equivalent self-service transitions are not (F39).
6. **Immutable history versus mutable dependencies:** Runtime audit data is
   append-oriented, but historical migrations depend on code that can change
   (F37).

## 5. Missing safeguards and tests

- No test replaces an externally anchored prefix and then appends records.
- No aggregate-memory/concurrency test opens many unauthenticated request bodies.
- No packet authorization test inspects nested chain data or database/PDF access
  for a production-only reviewer.
- No request-cost test proves packet pagination bounds queries and bytes read.
- No packet generation test proves pre-limit rejection or lock release during
  rendering.
- No concurrent credential transition tests exist.
- No repository-wide migration import/reproducibility invariant exists.
- No scheduler, freshness SLO, or alert test exists for external anchors.
- The restore drill does not verify application usability or integrity.
- Frontend persistence tests omit semantic validation and storage-removal failure.
- The focused existing backend suites for anchors, packet retrieval, limits, and
  migration reproducibility passed 24 tests during this review. The prior
  complete remediation run reported 378 passed and 2 skipped. Neither run
  covers the adverse scenarios above, as demonstrated by the independent F30
  reproduction.

## 6. Recommended remediation plan

### Immediate blockers

1. Replace count-only advancement with a verifiable signed-prefix design (F30).
2. Move authentication/global admission ahead of large-body buffering and add
   proxy-level abuse controls (F31).
3. Separate target-artifact verification from privileged full-chain verification
   and close packet metadata leakage (F32).
4. Keep release rollback armed through every mandatory gate (F34).
5. Repair the CI production and candidate-signoff fixtures so all subsequent
   fixes are protected by canonical CI (F33).

### Next stabilization work

1. Make packet read and generation cost bounded and observable (F35/F36).
2. Serialize all password and MFA state transitions (F39).
3. Package recurring anchor creation, immutable offsite retention, freshness
   monitoring, and failure alerts (F38).
4. Upgrade restore certification from row presence to schema, permissions,
   integrity, and application usability (F41).
5. Strengthen local snapshot validation and error propagation (F40).

### Longer-term architectural improvements

1. Freeze every historical migration and enforce that rule automatically (F37).
2. Move large PDF generation and full forensic verification to idempotent,
   observable background jobs with explicit snapshot/version contracts.
3. Consolidate integrity, release, and recovery documentation around executable
   controls and update the readiness record (F42).

## 7. Verification commands

Start the test database before backend tests:

```bash
docker compose up -d db
cd server
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

Run frontend verification:

```bash
npm test -- --run
npm run typecheck
npm run build
```

Run production/release checks using the environment fixture after F33 is fixed:

```bash
bash scripts/validate_production_env.sh
bash scripts/launch_gate.sh
bash scripts/test_ops.sh
```

Reproduce F30 against the current implementation:

```bash
tmp=$(mktemp -d)
mkdir "$tmp/anchors"
printf '0123456789abcdef0123456789abcdef' > "$tmp/key"
ATLAS_ARGUS_INTEGRITY_ANCHOR_DIR="$tmp/anchors" \
ATLAS_ARGUS_INTEGRITY_ANCHOR_KEY_FILE="$tmp/key" \
server/.venv/bin/python -c "from atlas_argus.integrity_anchors import write_anchor,verify_latest_anchor; make=lambda count,root:{'audit':{'checked':count,'rootIntegrityHash':root},'packets':{'checked':0,'rootIntegrityHash':None},'sections':{'checked':0,'rootIntegrityHash':None},'decisions':{'checked':0,'rootIntegrityHash':None},'extractions':{'runs':0,'pages':0,'documents':0,'rootIntegrityHash':None}}; write_anchor('case-1',make(2,'root-A')); print(verify_latest_anchor('case-1',make(3,'root-B')))"
trash "$tmp"
```

The current result is `ok: true, status: advanced`; the corrected result must be
a prefix/checkpoint mismatch.

After remediation, add and run focused suites for each finding before repeating
the complete backend/frontend and operational checks above.
