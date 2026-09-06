# Repository-wide code review - 2026-07-30

This is the active findings list. It supplements, but does not rewrite, the
historical [2026-07-18 review](codebase-review-2026-07-18.md). Statements marked
resolved in that report describe an earlier source state.

The review covered the React client, API routes and middleware, authentication,
domain transitions, SQLAlchemy persistence, PostgreSQL row-level security
(RLS), Alembic migrations, ingestion workers, packet generation, integrity
chains, backup and restore tooling, container configuration, deployment and
release scripts, and the test suite. Important workflows were traced from
client input through authorization, persistence, audit, and rendered output.

## Remediation status - 2026-07-31

All 29 findings in this report have been addressed in the current working tree:

- F01-F03, F09-F10, and F28-F29: complete-scope evidence verification,
  stored-PDF hashing, immutable custody metadata, transitive disclosure,
  serialized approvals, privileged packet metadata filtering, and externally
  authenticated integrity checkpoints.
- F04-F08, F11, F24, and F26: coherent case snapshots, transactional upload
  retries, clarified claim-scoped reliability actions, effective-Senior
  invariants, MFA-gated password changes, one-use TOTP counters, and serialized
  credential administration.
- F12-F15 and F25: post-auth workload admission, total body deadlines,
  per-source login throttling with bounded Argon2 concurrency, packet
  idempotency, ordered client/server deadlines, upload abort propagation, and
  synchronous duplicate-submission guards.
- F16-F23 and F27: bounded interactive verification plus an owner-run full
  checkpoint job, batched state serialization, supporting indexes, frozen
  migration SQL/downgrade restoration, authoritative production configuration
  parsing and memory accounting, candidate-bound release gates with rollback,
  a zero-matter administration workspace, and aligned validation/persistence/
  documentation contracts.

Verification after remediation: 378 backend tests passed (2 optional ingestion
tests skipped), 125 frontend tests passed, the production frontend build and
Python lint passed, operational shell tests passed, and `git diff --check`
reported no whitespace errors. Production readiness still depends on supplying
the required external sign-offs, key custody, encrypted WORM checkpoint copy,
backup/restore evidence, and deployment-specific security review.

## 1. Executive summary

Overall health is mixed. The repository has substantial automated coverage,
explicit domain services, transaction-scoped RLS, append-only audit controls,
bounded ingestion subprocesses, and careful operational scripts. The checked
source state passed its unit/integration tests, frontend build, linter, and
shell tests.

It is not ready for production handling of legal evidence without further
stabilization. The most serious risks are false-positive integrity results,
production disclosure that can differ from approved evidence, inconsistent
multi-query reads, and
administrative recovery paths that can strand a matter. Several timeout,
idempotency, release, and rollback defects can also turn partial failures into
duplicate records or outages.

No Critical finding was confirmed. Three findings are High, with the remainder
primarily Medium. Immediate work should focus on findings 1-3, then transaction
serialization, authority recovery, request admission, and release safety.

Review limitations:

- PostgreSQL-specific concurrency and RLS findings were established from code
  paths and existing tests; not every race was reproduced under a multi-session
  stress harness.
- Docker runtime inspection was unavailable through the local daemon.
- `npm audit` could not run in the restricted environment, and the request to
  submit dependency metadata externally was not approved.
- `pip-audit` and `bandit` are not installed in the project virtual environment.
  Dependency vulnerability status therefore remains unverified.
- Absence of a finding in an area is not evidence that the area is safe.

## 2. Critical and high-severity findings

### F01. Case integrity can report success without checking privileged extraction data

- **Classification:** Confirmed defect
- **Severity / confidence:** High / High
- **Category:** Integrity verification, authorization, RLS
- **Affected code:** `api/routes.py:722-728`;
  `db/guards.py:226-275`; `services.py:1162-1300`
- **Trigger:** An MFA-verified matter member without privilege clearance calls
  `GET /cases/{case_id}/audit/verify` for a case containing privileged sources.
- **Expected:** The endpoint either verifies every integrity-bearing row or
  clearly returns an incomplete/unauthorized result that cannot be interpreted
  as overall success.
- **Actual:** Production RLS hides source files, extraction runs, and extracted
  pages from the caller. Verification iterates only visible rows and can return
  `ok: true`.
- **Impact:** Corrupt or missing privileged evidence can be represented to a
  reviewer as a fully verified matter. That weakens the evidentiary and
  operational meaning of the integrity endpoint.
- **Root cause:** Verification treats an RLS-filtered result set as complete and
  does not bind completeness to expected row counts or caller clearance.
- **Recommended fix:** Require cleared Senior access for full verification, or
  run a narrowly scoped privileged verifier and return separate
  `complete`, `authorized`, and `ok` states. Never derive overall success from
  a caller-filtered subset.
- **Required tests:** Corrupt a privileged source/extraction record, call the
  endpoint as cleared and non-cleared members, and prove the latter cannot
  receive an unqualified success.
- **Related findings:** F02, F09, F28, F29

### F02. Case and packet-list integrity can be green while stored PDF bytes are corrupt

- **Classification:** Confirmed defect
- **Severity / confidence:** High / High
- **Category:** Evidence integrity, API correctness
- **Affected code:** `services.py:1009-1058`, `services.py:1162-1214`,
  `services.py:1308-1374`, `services.py:1439-1497`;
  `integrity.py:139-178`; `PacketExportView.tsx:411-459`
- **Trigger:** A stored packet PDF is truncated, corrupted, restored
  incorrectly, or modified while its database `pdf_sha256` value is unchanged.
- **Expected:** Case verification and packet history identify the artifact as
  corrupt, or explicitly state that blob bytes were not checked.
- **Actual:** The packet chain recomputes links using the stored hash column but
  does not load and hash PDF bytes. Only direct verification of one selected
  artifact calls `_verify_stored_pdf`. Case verification and packet listing can
  remain green until the artifact is opened.
- **Impact:** Operators and reviewers can rely on a false green matter or
  history status even though an archived legal deliverable is unreadable or
  altered.
- **Root cause:** Chain integrity and blob integrity are presented as one
  result, although only the former is evaluated in list/case paths.
- **Recommended fix:** Hash every referenced PDF for a full verification run,
  or expose independent `chainVerified` and `pdfBytesVerified` states with a
  bounded/deferred full verification workflow.
- **Required tests:** Tamper with stored PDF bytes without changing metadata;
  assert case verification, packet list, direct verification, and download
  behavior all report consistent failure.
- **Related findings:** F01, F16, F29

### F03. Production packets can omit preserved conflicts included in approval evidence

- **Classification:** Confirmed defect
- **Severity / confidence:** High / High
- **Category:** Business logic, legal disclosure
- **Affected code:** `approval.py:12-35`; `packet.py:214-232`
- **Trigger:** Preserved conflicts form a transitive graph, such as A-B and B-C,
  and a report section directly cites only claim A.
- **Expected:** The packet discloses the same complete evidence/conflict graph
  that was hashed and accepted during approval.
- **Actual:** Approval expands through every `preserved_both` edge, while packet
  construction renders only conflicts directly attached to original section
  claim IDs. The packet can include A-B and omit B-C.
- **Impact:** A production packet can materially differ from its approved
  evidence state and omit a conflict whose preservation was a condition of
  approval.
- **Root cause:** Approval and rendering implement separate graph traversal
  rules.
- **Recommended fix:** Create one domain-level evidence expansion function and
  use its result for approval hashing, eligibility, and packet disclosure.
- **Required tests:** Cover preserved chains, branches, cycles, duplicate edges,
  and mixed accepted/rejected decisions; assert approval input and rendered
  disclosure contain the same claim and conflict IDs.
- **Related findings:** F06, F27

## 3. Medium and low-severity findings

### Persistence, workflows, and integrity

### F04. Matter state responses are not transactionally snapshot-consistent

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Concurrency, API consistency
- **Affected code:** `db/session.py:20-21,48-57`;
  `services.py:1604-1774`; compare `services.py:3312-3317`
- **Trigger:** A decision, conflict, approval, or claim update commits between
  the separate SELECT statements used to assemble one case-state response.
- **Expected:** One response represents one coherent committed matter version.
- **Actual:** Default `READ COMMITTED` isolation allows each SELECT to see a
  newer snapshot. A response can combine an old claim/conflict state with a new
  decision or approval. Packet generation already takes the case audit lock
  before reading specifically to avoid this issue.
- **Impact:** The UI can render impossible combinations and ask users to act on
  a state that never existed in the database.
- **Root cause:** A compound aggregate is assembled from many independent
  queries without a snapshot boundary.
- **Recommended fix:** Batch the queries, then use a read-only
  `REPEATABLE READ` transaction or the established per-case serialization lock.
  Avoid holding a write-contention lock across the current N+1 query pattern.
- **Required tests:** Use two database sessions and barriers to commit mutations
  between aggregate queries; assert every response corresponds to either the
  before or after state, never a mixture.
- **Related findings:** F10, F17

### F05. Concurrent idempotent uploads fail before reconciliation

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Concurrency, idempotency, transaction handling
- **Affected code:** `services.py:2295-2316`; `api/routes.py:375-396`
- **Trigger:** Two uploads with the same idempotency key pass the initial lookup
  and race to insert the unique key.
- **Expected:** The loser rolls back and returns the artifact committed by the
  winner.
- **Actual:** The loser catches `IntegrityError` after `flush()` and raises a
  custom signal, but the route catches it inside the session context. The
  poisoned SQLAlchemy transaction is then committed and raises
  `PendingRollbackError`; fresh-session reconciliation is unreachable.
- **Impact:** A normal overlapping retry can return 500, encouraging further
  retries and creating uncertainty around whether sensitive evidence was
  accepted.
- **Root cause:** Database constraint failure is converted to control flow
  without restoring transaction state.
- **Recommended fix:** Catch the custom signal outside the transaction context,
  or use a savepoint and explicit rollback before reconciliation.
- **Required tests:** Coordinate two real PostgreSQL sessions at the insert
  barrier and assert one artifact, two successful equivalent responses, and no
  failed session reuse.
- **Related findings:** F14, F15, F25

### F06. Marking a source unreliable affects only one selected conflict

- **Classification:** Highly likely defect
- **Severity / confidence:** Medium / Medium
- **Category:** Business rules, state transitions
- **Affected code:** `domain/permissions.py:3-20`;
  `domain/decisions.py:83-92`; `services.py:2957-3001`;
  `src/domain/types.ts:211`
- **Trigger:** One source supports claims in multiple conflicts and a reviewer
  chooses the action labelled "mark source unreliable" in one conflict.
- **Expected:** Either every dependent claim is re-evaluated, or the operation
  is explicitly scoped and named as rejecting one claim.
- **Actual:** Only the two claims in the selected conflict are changed. Other
  claims from the same `SourceDocument` remain eligible.
- **Impact:** One document can be considered unreliable and reliable
  simultaneously with no narrower finding recorded, producing contradictory
  eligibility and packet results.
- **Root cause:** User-facing doctrine is source-scoped while the transition is
  conflict/claim-scoped.
- **Recommended fix:** Decide and document the intended doctrine. If
  source-scoped, persist source reliability and atomically recompute all
  dependent conflicts, sections, and approvals. Otherwise rename the action.
- **Required tests:** Use one source across several claims and conflicts; verify
  every dependent state and approval invalidation after the action.
- **Related findings:** F03, F09, F27

### F07. Account deactivation can strand a matter without usable Senior counsel

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Authorization, data invariant
- **Affected code:** `services.py:1855-1866`, `services.py:1978-2025`
- **Trigger:** The sole active Senior member of a matter has their global
  reviewer account deactivated while another Senior account exists elsewhere.
- **Expected:** Deactivation is rejected or responsibility is atomically
  transferred before the account becomes unusable.
- **Actual:** Membership validation counts the active membership flag without
  joining reviewer account status. Account deactivation checks only that one
  active Senior remains globally.
- **Impact:** Privilege review, section approval, membership administration, and
  internal packet access can become unavailable for that matter.
- **Root cause:** The same "effective Senior" invariant is implemented at two
  layers with different definitions.
- **Recommended fix:** Lock every affected matter and require at least one
  active Senior membership backed by an active Senior account, or require an
  explicit transfer transaction.
- **Required tests:** Deactivate the sole effective Senior for one of several
  matters; cover concurrent deactivation and membership removal.
- **Related findings:** F08

### F08. Break-glass restore refuses matters that have users but no administrator

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Disaster recovery, authorization
- **Affected code:** `db/restore_matter_access.py:68-85,184-189`;
  `tests/test_restore_matter_access.py:19-52,108-128,158-164`
- **Trigger:** A matter retains active ordinary reviewers, but its only Senior
  member's account is inactive or otherwise unusable.
- **Expected:** Break-glass tooling recognizes that the matter has no effective
  administrator and restores a Senior under its documented safety checks.
- **Actual:** `_effective_members` treats any active membership backed by any
  active account as sufficient and refuses recovery.
- **Impact:** The exact authority-stranding state created by F07 cannot be
  repaired by the recovery tool without manual database intervention.
- **Root cause:** Recovery defines "accessible" as having any user, not having a
  user authorized to perform the stranded administrative operations.
- **Recommended fix:** Define and test an effective-administrator predicate
  based on active account, active membership, and required Senior authority.
- **Required tests:** Leave active Claims Reviewers but no effective Senior and
  prove recovery succeeds; prove it still refuses a healthy matter.
- **Related findings:** F07

### F09. Source custody metadata is mutable and omitted from case integrity verification

- **Classification:** Missing test or safeguard
- **Severity / confidence:** Medium / High
- **Category:** Evidence integrity, persistence
- **Affected code:** `db/guards.py:144-166`;
  `services.py:1162-1305`; `packet.py:516-533`;
  `approval.py:91-105`; `tests/test_packets.py:147-181`
- **Trigger:** A direct database operation, maintenance script, or future API
  changes source title, origin, custodian, docket reference, privilege, custody
  metadata, or recorded SHA metadata after ingestion.
- **Expected:** Legally relevant source metadata is immutable, revisioned and
  audited, or included in an integrity structure that detects alteration.
- **Actual:** Database guards protect active extraction pointers but not these
  fields. Approval hashes include them, so production approval becomes stale,
  but case integrity remains green and internal packet output can silently
  change.
- **Impact:** Chain-of-custody representations can change without an integrity
  failure or dedicated audit event.
- **Root cause:** Byte/extraction integrity and descriptive custody integrity
  have different, undocumented protection boundaries.
- **Recommended fix:** Store metadata revisions as append-only audited records,
  or bind a canonical source-record hash into the matter chain and packet.
- **Required tests:** Attempt direct metadata mutation and assert either database
  rejection or case-integrity failure plus approval invalidation.
- **Related findings:** F01, F02, F29

### F10. Approval can succeed while becoming stale during the same transaction

- **Classification:** Highly likely defect
- **Severity / confidence:** Medium / Medium
- **Category:** Concurrency, workflow atomicity
- **Affected code:** `services.py:2976-2985`, `services.py:3238-3269`
- **Trigger:** Evidence state changes concurrently after section approval
  computes its evidence hash but before approval acquires the case audit lock
  and commits.
- **Expected:** Approval serializes against evidence changes and is current at
  commit, or fails explicitly due to a concurrent transition.
- **Actual:** The section is locked and the old evidence hash is computed before
  `_append_audit` acquires the case lock. A decision can commit in that window.
  The approval response and audit event report success, but currentness checks
  can reject the revision immediately afterward.
- **Impact:** Reviewers receive a misleading successful approval and production
  generation fails later. Lock-order differences also make future deadlocks
  more likely as workflows expand.
- **Root cause:** The serialization lock covers audit append, not the evidence
  snapshot that the audit event attests to.
- **Recommended fix:** Establish one case-lock ordering and acquire it before
  computing approval impact/evidence state; hold it through revision and audit
  persistence.
- **Required tests:** Race approval against a conflict decision with barriers;
  prove the approval is either current and committed or rejected as stale.
- **Related findings:** F04, F06

### Authentication and request handling

### F11. MFA-enabled accounts can change password before MFA verification

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Authentication, account takeover
- **Affected code:** `api/routes.py:246-260`; `auth.py:445-473`
- **Trigger:** An attacker knows a user's password, starts an MFA-pending
  session, and calls `POST /auth/change-password`.
- **Expected:** Existing MFA-enabled users must complete MFA before changing a
  password or revoking sessions; only forced initial rotation has a documented
  exception.
- **Actual:** The route requires a session but not MFA. It changes the password
  and revokes all other sessions.
- **Impact:** Password-only compromise can lock out the legitimate owner despite
  MFA protecting the rest of the application.
- **Root cause:** The endpoint reuses the initial-password-rotation permission
  for all password changes.
- **Recommended fix:** Conditionally require current-session MFA when a factor
  is enabled, while preserving a narrowly defined bootstrap path.
- **Required tests:** Cover initial forced rotation, normal MFA-enabled change,
  MFA-pending rejection, stale sessions, and concurrent factor reset.
- **Related findings:** F24, F26

### F12. Anonymous slow requests can reserve scarce ingestion and render slots

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Denial of service, request admission
- **Affected code:** `api/app.py:105-145,171-188`;
  `docs/deploy/Caddyfile.example`
- **Trigger:** Anonymous clients slowly send bodies to upload or packet paths.
- **Expected:** Authentication and bounded body deadlines occur before scarce
  workload capacity is reserved.
- **Actual:** Admission is path-based and precedes authentication/body
  completion. Body size is bounded, but read time is not; the supplied proxy
  adds no body deadline or per-client admission limit.
- **Impact:** A few unauthenticated connections can occupy all upload or render
  slots and deny legitimate work.
- **Root cause:** Middleware reserves expensive-operation capacity based on URL
  alone and assumes upstream timeout/rate controls not present in the example.
- **Recommended fix:** Authenticate before slot acquisition where possible,
  enforce header/body deadlines at proxy and application layers, and use
  per-source admission controls.
- **Required tests:** Slow-stream unauthenticated bodies past the configured
  deadline and prove slots are released and authenticated requests proceed.
- **Related findings:** F13, F14, F15

### F13. Global login throttling is an unauthenticated all-user denial switch

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Authentication availability, denial of service
- **Affected code:** `auth.py:44-48,207-233`;
  `docs/deploy/Caddyfile.example`
- **Trigger:** Any source submits roughly 31 login attempts per minute and
  continues during each 60-second lock period.
- **Expected:** Abuse by one source/account is limited without disabling login
  for unrelated users.
- **Actual:** All callers share one global attempt key. The supplied edge
  configuration has no per-source login limiter.
- **Impact:** An anonymous remote client can continuously prevent every user
  from signing in.
- **Root cause:** Argon2 workload protection and abuse throttling are conflated
  in one caller-independent lockout.
- **Recommended fix:** Keep bounded password-hash concurrency, but throttle with
  per-source and per-account buckets plus an edge limiter and observable
  rejection metrics.
- **Required tests:** Exhaust one source/account bucket and prove unrelated
  users can log in; stress the Argon2 concurrency bound separately.
- **Related findings:** F12

### F14. Packet generation has an ambiguous timeout and no idempotency

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Partial failure, API idempotency
- **Affected code:** `src/api/client.ts:124,641-647`;
  `services.py:3405-3436`
- **Trigger:** PDF rendering and commit exceed the client's generic 20-second
  timeout.
- **Expected:** The client can discover the definitive result of one generation
  operation and retry without creating a second artifact.
- **Actual:** The UI reports a timeout while the server can still commit.
  Retrying has no idempotency key and creates another packet and audit event.
- **Impact:** Duplicate legal artifacts, misleading failure messages, excess
  rendering load, and uncertainty about which artifact is authoritative.
- **Root cause:** A long-running state-changing operation is modelled as a
  short synchronous request without operation identity.
- **Recommended fix:** Use a server-bounded asynchronous operation with a stable
  ID, or add a request idempotency key and a client timeout longer than the
  validated server/proxy deadline.
- **Required tests:** Force rendering beyond the client timeout, retry the same
  operation, and assert exactly one packet/audit event and a recoverable status.
- **Related findings:** F02, F12, F15

### F15. Upload cancellation and deadlines contradict the UI and server contract

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Frontend async behavior, partial failure
- **Affected code:** `SourceUploadForm.tsx:100-151,265-268`;
  `api/client.ts:138-143`; `config.py:70-71,235-242`
- **Trigger:** A user leaves the matter during upload, or valid ingestion lasts
  between the client's 10-minute timeout and the server's 15-minute allowance.
- **Expected:** Leaving either really cancels the request or the UI accurately
  states that processing continues; deadlines have one documented ordering.
- **Actual:** No abort signal is supplied. The component only ignores the later
  response, so sensitive evidence can still be stored in the previous matter.
  Long valid jobs time out client-side and encourage retries.
- **Impact:** Users can falsely believe an upload was cancelled, and overlapping
  retries can consume capacity or expose the F05 race.
- **Root cause:** UI lifecycle, HTTP cancellation, subprocess deadline, and
  idempotency behavior were designed independently.
- **Recommended fix:** Wire `AbortController` through the client, define what
  server work cancellation guarantees, correct the copy, and validate ordered
  client/proxy/server deadlines from shared configuration.
- **Required tests:** Navigate away mid-stream, abort before and after server
  acceptance, and exercise a job between both current timeout values.
- **Related findings:** F05, F12, F14

### Performance and scalability

### F16. Integrity and packet retrieval perform unbounded historical work

- **Classification:** Risk requiring verification
- **Severity / confidence:** Medium / High
- **Category:** Performance, availability
- **Affected code:** `services.py:1162-1300`, `services.py:1308-1590`
- **Trigger:** A long-lived matter or account accumulates a large number of
  audit events, packet revisions, decisions, extraction pages, or source bytes.
- **Expected:** Interactive endpoints have predictable resource limits, while
  full forensic verification runs as a controlled operation.
- **Actual:** Case verification loads and hashes complete histories. Direct
  packet verification/retrieval scans the complete packet chain even though
  only packet listing applies `MAX_PACKET_HISTORY_ROWS`. Account verification
  is also unbounded.
- **Impact:** An authorized request can monopolize database connections, memory,
  CPU, and response time; adding PDF-byte verification for F02 would increase
  this cost without a redesigned workflow.
- **Root cause:** Full forensic verification and interactive API behavior share
  the same synchronous implementation.
- **Recommended fix:** Add incremental checkpoints and bounded interactive
  summaries; run explicit full verification in a rate-limited background job
  with progress, cancellation, and resource accounting.
- **Required tests:** Load histories beyond expected production size and measure
  query count, peak memory, bytes read, cancellation, and concurrent request
  latency.
- **Related findings:** F02, F17, F18

### F17. Case-state serialization permits a query-count explosion

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Database performance, API scalability
- **Affected code:** `services.py:336-505,1604-1769`
- **Trigger:** A matter approaches the independent row caps for claims,
  conflicts, decisions, and sections.
- **Expected:** Query count remains roughly constant or grows in a small number
  of batched relationship queries.
- **Actual:** Serializers issue per-claim conflict queries, per-conflict decision
  queries, and per-section approval/evidence graph queries. Independent table
  caps still allow thousands of SQL statements.
- **Impact:** State loading can time out, hold a transaction for a long period,
  and amplify the inconsistency window in F04.
- **Root cause:** Row-count bounding was added without changing N+1
  relationship loading.
- **Recommended fix:** Batch-load each relationship into indexed maps and apply
  a total response-envelope limit with explicit pagination for histories.
- **Required tests:** Assert bounded query count at minimum, typical, and
  maximum supported graph sizes; add a realistic PostgreSQL latency benchmark.
- **Related findings:** F04, F18

### F18. Core case, auth, and history queries lack supporting index coverage

- **Classification:** Risk requiring verification
- **Severity / confidence:** Medium / Medium
- **Category:** Database performance, schema design
- **Affected code:** `auth.py:149-160,462-472`;
  `services.py:1632-1701,1777-1787`; ORM models and Alembic migrations
- **Trigger:** Auth sessions and matter tables grow to production-scale row
  counts while queries filter by reviewer, expiry, active membership, or case.
- **Expected:** Frequent filters and ordered history scans use selective
  indexes confirmed by query plans.
- **Actual:** Several foreign-key/filter paths have no matching index. The
  unique case-member index begins with `case_id` and does not support listing
  cases by `reviewer_id`; session purge/revocation and several case history
  paths similarly risk full scans.
- **Impact:** Latency and lock duration can grow sharply with total system size,
  even when returned rows are capped.
- **Root cause:** Constraints exist, but query-plan-oriented indexes were not
  designed alongside service access patterns.
- **Recommended fix:** Capture `EXPLAIN (ANALYZE, BUFFERS)` first, then add
  targeted composite/partial indexes such as reviewer-active membership,
  session reviewer/expiry, and case-sequence history paths.
- **Required tests:** Seed representative cardinalities, record query plans, and
  fail performance checks when critical paths regress to broad scans.
- **Related findings:** F16, F17

### Migrations, deployment, and operations

### F19. Historical migrations import mutable application SQL and downgrade incorrectly

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Migration reproducibility
- **Affected code:** `0017_source_ingestion.py:43,285`;
  `0021_case_domain_constraints.py:15,46,50-53`; `db/guards.py`
- **Trigger:** A helper SQL constant changes and a clean database replays old
  migrations, or revision 0021 is downgraded.
- **Expected:** A migration revision always performs the same operation, and
  downgrade restores the exact predecessor schema behavior.
- **Actual:** Old revisions import current guard SQL. Revision 0021 replaces the
  verification trigger, but downgrade drops constraints without restoring the
  0017 trigger function.
- **Impact:** Two databases at the same Alembic revision can have different
  behavior; downgrade can silently remove a data-integrity guard.
- **Root cause:** Versioned schema logic is coupled to mutable runtime modules,
  and downgrade coverage is incomplete.
- **Recommended fix:** Freeze SQL in each revision, add a corrective migration,
  and test every supported upgrade/downgrade path from immutable historical
  schema fixtures.
- **Required tests:** Compare schema/trigger definitions after clean install and
  historical upgrade; downgrade 0021 and execute rows that its predecessor
  trigger should reject.
- **Related findings:** F27

### F20. Production preflight omits current settings and aggregate memory validation

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Configuration, resource safety
- **Affected code:** `config.py:55-80,389-395`;
  `scripts/validate_production_env.sh:78-95,128-132`;
  `docker-compose.production.example.yml:26-30,64-71`
- **Trigger:** A newly introduced bounded setting is malformed, or individually
  valid ingestion child memory/concurrency values exceed the fixed 1536 MiB
  API container budget.
- **Expected:** Preflight validates the same settings as application startup and
  rejects aggregate resource envelopes that cannot fit the deployment.
- **Actual:** The shell script checks only an older subset. For example,
  malformed packet-history configuration passes preflight. Accepted child
  memory and concurrency can deterministically exceed the container limit.
- **Impact:** Release checks can pass before startup failure or repeated OOM
  restarts.
- **Root cause:** Validation rules are duplicated across shell, Python, and
  compose files without one authoritative resource model.
- **Recommended fix:** Invoke the application settings parser during preflight
  and validate aggregate process/container memory with explicit overhead.
- **Required tests:** Generate invalid values for every production setting and
  boundary combinations for child memory, concurrency, and container limits.
- **Related findings:** F21, F22

### F21. Production launch can report success after release gates are skipped

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Release assurance, operational correctness
- **Affected code:** `scripts/production_launch_check.sh:43-53,70-71,100-148`;
  `scripts/launch_gate.sh`
- **Trigger:** A launch uses flags that skip verification, build, deployment,
  smoke, or offsite copy while stale sign-off artifacts remain available.
- **Expected:** A command presented as production certification cannot succeed
  unless the exact candidate completed every mandatory gate.
- **Actual:** Rehearsal flags can skip core work, and the final gate checks
  environment/sign-off files rather than provenance for this run. It can print
  `production launch check ok`.
- **Impact:** Operators can mistake a partial rehearsal for evidence that the
  candidate was built, deployed, tested, and backed up.
- **Root cause:** Run identity and candidate digest are not bound to gate
  artifacts, and skip modes share the production success result.
- **Recommended fix:** Reject skip flags in production mode; issue signed or
  checksummed per-run gate artifacts bound to source/image digest and emit a
  distinct non-certifying rehearsal result.
- **Required tests:** Exercise every skip-flag combination with stale artifacts
  and assert none can produce a production-success exit/result.
- **Related findings:** F20, F22

### F22. Failed deployment checks leave the candidate in service

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Deployment rollback, availability
- **Affected code:** `scripts/production_launch_check.sh:110-123`
- **Trigger:** The force-recreated API fails compose health or post-deployment
  smoke while the proxy continues routing to that service.
- **Expected:** Candidate failure preserves or restores the last known-good
  version automatically.
- **Actual:** The script does not retain the previous immutable image digest or
  execute rollback after failure.
- **Impact:** A release process can detect a bad candidate yet leave production
  unavailable.
- **Root cause:** In-place replacement is used without a rollback target or
  traffic handoff.
- **Recommended fix:** Deploy immutable digests with an automatic rollback
  record, or use blue/green deployment and move traffic only after candidate
  health and smoke pass.
- **Required tests:** Deliberately deploy failed health and failed smoke
  candidates and prove traffic remains on/restores the prior digest.
- **Related findings:** F20, F21

### Frontend and contract consistency

### F23. Users with no assigned matter cannot reach creation or administration

- **Classification:** Confirmed defect
- **Severity / confidence:** Medium / High
- **Category:** Frontend workflow, authorization usability
- **Affected code:** `src/state/store.tsx:800-805,948-952`;
  `App.tsx:36-64`; `CaseSidebar.tsx:75`
- **Trigger:** A newly provisioned global Senior signs in with no matter
  membership.
- **Expected:** The authenticated empty state exposes global administration and
  matter creation allowed by backend policy.
- **Actual:** Boot calls `loadCase(cases[0]?.caseFile.id)`, falls back to legacy
  `/case`, receives 403, and renders a boot error. Creation/admin controls exist
  only inside the loaded workspace.
- **Impact:** A legitimate administrator cannot bootstrap the first accessible
  matter through the UI.
- **Root cause:** Client boot assumes every authenticated user has at least one
  case and conflates empty data with authorization failure.
- **Recommended fix:** Add a first-class authenticated empty workspace with
  global controls, and remove the legacy fallback for an empty case list.
- **Required tests:** Sign in as global Senior and non-Senior users with zero
  memberships; verify correct controls, focus, routing, and error behavior.
- **Related findings:** F07, F08

## Low-severity findings

### F24. TOTP codes can be replayed within the accepted time window

- **Classification:** Confirmed defect
- **Severity / confidence:** Low / High
- **Category:** MFA replay protection
- **Affected code:** `auth.py:128-137`
- **Trigger:** The same captured TOTP is submitted to more than one pending
  session within the current/adjacent accepted counters.
- **Expected:** A valid code is accepted once per reviewer counter.
- **Actual:** No last-accepted counter is persisted, so the code can verify
  multiple sessions for roughly the configured drift window.
- **Impact:** A captured second factor has a larger replay opportunity than
  necessary.
- **Root cause:** TOTP validity is checked cryptographically but not consumed
  atomically.
- **Recommended fix:** Persist and atomically advance the last accepted counter
  per reviewer, while preserving documented clock drift.
- **Required tests:** Concurrently submit the same code to two sessions and
  assert exactly one succeeds; test adjacent counters and clock skew.
- **Related findings:** F11

### F25. Several create and administration controls allow duplicate submissions

- **Classification:** Confirmed defect
- **Severity / confidence:** Low / High
- **Category:** Frontend concurrency, idempotency
- **Affected code:** `NewClaimForm.tsx:55-65`;
  `SectionEditor.tsx:56-64,146-152`;
  `AdminView.tsx:120-140,195-213`
- **Trigger:** Double click, keyboard repeat, or slow response causes the same
  action to be submitted twice before local state updates.
- **Expected:** One user action creates one domain operation, and uncertain
  responses are safely recoverable.
- **Actual:** These controls have no per-operation busy guard. Duplicate claims,
  sections, or requests can be created, or the second response can show an
  error after the first succeeded.
- **Impact:** Duplicate records, confusing feedback, and avoidable load.
- **Root cause:** Pending state is inconsistent across mutation controls and
  create endpoints do not all provide idempotency.
- **Recommended fix:** Add synchronous submission guards, disable mutable
  controls while pending, and use idempotency keys for material creates.
- **Required tests:** Double-activate by mouse and keyboard under delayed
  responses; assert one request and correct focus/error restoration.
- **Related findings:** F05, F14, F15

### F26. Concurrent administrator credential resets are last-write-wins

- **Classification:** Confirmed defect
- **Severity / confidence:** Low / High
- **Category:** Concurrency, audit semantics
- **Affected code:** `services.py:2043-2093`
- **Trigger:** Two administrators reset the same reviewer's password or MFA
  state concurrently.
- **Expected:** Operations serialize or one fails against an expected version,
  and the audit trail identifies the superseded state.
- **Actual:** The reviewer row is loaded without a row lock/version. Both
  administrators can receive success and audit records while only the last
  credential state remains effective.
- **Impact:** Misleading administrative confirmation and harder incident
  reconstruction.
- **Root cause:** Security-sensitive writes omit the concurrency pattern used by
  other domain transitions.
- **Recommended fix:** Lock the target reviewer, add an expected version where
  appropriate, and audit prior/new credential-state metadata without secrets.
- **Required tests:** Race two resets and assert deterministic serialization or
  conflict, session revocation, and truthful audit output.
- **Related findings:** F11

### F27. Validation, persistence, and documentation describe different contracts

- **Classification:** Maintainability issue
- **Severity / confidence:** Low / High
- **Category:** Schema drift, incomplete validation
- **Affected code:** `src/domain/matters.ts:26`;
  `domain/matters.py:49`; `domain/report_sections.py:44`;
  `0021_case_domain_constraints.py:39-44`; `api/schemas.py:110-116`;
  `src/state/persistence.ts:58-94,128-135`;
  `.env.production.example`; `scripts/backup_offsite.sh:17-20`;
  `README.md:335-341`
- **Trigger:** Future accident dates, duplicate report claim IDs, a non-open
  case status, malformed local snapshots, browser storage quota failure, or
  following the production example/README literally.
- **Expected:** Frontend, backend, database, deployment examples, and
  documentation enforce and describe one contract.
- **Actual:** Frontend permits dates the backend rejects; report sections accept
  duplicate claim IDs; the database allows only `open` although schemas imply
  lifecycle states; snapshot parsing validates arrays rather than object
  shapes and silently drops quota errors; required offsite-encryption config is
  absent from the example; README says matter creation is unimplemented.
- **Impact:** Late failures, misleading operator instructions, corrupted local
  state, and increased risk that future changes update only one representation.
- **Root cause:** Rules are duplicated and no schema/contract drift checks bind
  the layers together.
- **Recommended fix:** Generate client/runtime schemas from one domain contract
  where practical, validate snapshots structurally, surface persistence
  failures, and add documentation/config assertions to release tests.
- **Required tests:** Boundary contract tests across TypeScript, API, and DB;
  malformed snapshot/property tests; production example validation; docs
  command smoke tests.
- **Related findings:** F03, F06, F19, F20

### F28. Internal packet history exposes privileged work-product metadata

- **Classification:** Risk requiring verification
- **Severity / confidence:** Low / Medium
- **Category:** Privacy, authorization policy
- **Affected code:** `services.py:1385-1497`, `services.py:1522-1524`,
  `services.py:1564-1570`; `api/routes.py:635-664`
- **Trigger:** A non-cleared active matter member lists packet artifacts for a
  matter containing internal work-product packets.
- **Expected:** The product/legal policy explicitly defines whether existence,
  timing, filenames, hashes, and packet statistics are themselves privileged.
- **Actual:** Detail and download are denied, and generator identity is hidden,
  but listing still exposes internal packet type, generation time, filename,
  hashes, PDF hash, and statistics.
- **Impact:** Metadata can reveal when internal work product was generated, its
  approximate scope, and stable correlation hashes. Whether this is a defect
  depends on the intended privilege policy.
- **Root cause:** Content authorization and metadata authorization use different
  rules without a documented threat-model decision.
- **Recommended fix:** Obtain an explicit legal/product policy decision. If
  metadata is privileged, omit internal rows entirely or return a minimal
  non-correlatable placeholder to non-cleared members.
- **Required tests:** Encode the decided matrix for packet type, clearance,
  membership, list, detail, and download responses.
- **Related findings:** F01

### F29. Internal hash chains cannot detect valid tail deletion without an external anchor

- **Classification:** Risk requiring verification
- **Severity / confidence:** Low / High
- **Category:** Integrity architecture, backup assurance
- **Affected code:** `services.py:902-935` and equivalent packet, revision,
  decision, and audit chain verifiers;
  `docs/deploy/evidence-handling-review.md:54-65,117-118`
- **Trigger:** A database owner, compromised host, faulty restore, or storage
  corruption removes the newest chain rows while leaving the remaining prefix
  unchanged.
- **Expected:** Verification detects that committed history is missing.
- **Actual:** Verifiers establish consistency only among rows that remain. A
  valid prefix, including an empty chain, has no broken link and can verify
  successfully because no expected root/count is anchored outside the same
  mutable database.
- **Impact:** Append-only triggers protect ordinary application writes, but do
  not make integrity claims robust against owner-level tampering or rollback.
  Existing documentation partially acknowledges the lack of signatures/WORM.
- **Root cause:** Hash chaining proves internal linkage, not completeness
  against an independently trusted checkpoint.
- **Recommended fix:** Periodically sign and publish/retain chain roots and row
  counts in independent WORM or externally controlled storage, then verify
  database state against those anchors.
- **Required tests:** After creating an external anchor, delete a chain tail in
  a controlled fixture and assert verification reports rollback/incompleteness.
- **Related findings:** F01, F02, F09

## 4. Cross-cutting inconsistencies

1. **Evidence scope differs by subsystem.** Approval uses transitive conflict
   expansion, packet disclosure uses direct edges, matter integrity is
   caller/RLS scoped, and packet list integrity omits blob bytes.
2. **Authority invariants have competing definitions.** Membership changes
   count active Senior memberships, account deactivation counts global active
   Senior accounts, and break-glass restore counts any active member.
3. **Serialization is applied unevenly.** Packet generation deliberately locks
   before reading a coherent evidence snapshot, while matter state and approval
   evidence hashing do not use the same boundary.
4. **Long-running operations have inconsistent contracts.** Upload has an
   idempotency key but broken race recovery; packet generation has none; client,
   server, and proxy deadlines are not aligned; cancellation copy overstates
   actual behavior.
5. **Integrity terminology conflates different guarantees.** Link consistency,
   blob-byte verification, result-set completeness, metadata immutability, and
   external rollback detection are presented through similar green indicators.
6. **Configuration validation is duplicated.** Python startup, shell preflight,
   compose limits, example environment files, and release gates do not share
   one authoritative schema or candidate identity.
7. **Domain rules drift across layers.** Dates, case statuses, source
   reliability semantics, report claim IDs, and local snapshot validation have
   separate frontend, backend, database, and documentation representations.

## 5. Missing safeguards and tests

The current suite does not sufficiently prove:

- full integrity behavior under RLS and corrupt privileged records;
- case/list behavior after stored PDF-byte corruption;
- historical upgrade from an authentic frozen pre-0017 schema;
- transitive conflict disclosure parity between approval and packet output;
- snapshot consistency during concurrent case-state reads;
- approval serialization against concurrent evidence transitions;
- two-session idempotent upload collision recovery;
- account deactivation and break-glass recovery with active ordinary reviewers
  but no effective Senior;
- MFA-pending password-change rejection and one-time TOTP consumption;
- slow-body admission exhaustion and timeout cleanup;
- packet-generation retry after an ambiguous timeout;
- real browser upload cancellation and navigation;
- maximum-scale query counts, plans, memory, and verification cancellation;
- production launch behavior for every skip flag and automatic rollback;
- zero-matter bootstrap behavior for each global role;
- external chain-anchor rollback detection;
- the intended authorization matrix for internal packet metadata.

Security dependency scanning is also missing from the verified evidence for this
review. It should run in CI using approved registry access, pinned scanner
versions, and retained reports rather than relying on an ad hoc local request.

## 6. Recommended remediation plan

### Immediate blockers

1. Fix F01 and F02 so integrity results accurately state scope and verify stored
   artifact bytes.
2. Unify approval and packet evidence expansion for F03.
3. Prevent F11 password changes from bypassing enabled MFA.

### Next stabilization work

1. Define one per-matter serialization and lock order; fix F04, F05, and F10.
2. Define and enforce one effective-Senior invariant across membership,
   deactivation, and break-glass recovery (F07-F08).
3. Align source reliability and source metadata protection (F06, F09).
4. Redesign request admission, long-operation identity, cancellation, and
   deadline ordering (F12-F15).
5. Batch case-state loading, add measured indexes, and separate interactive
   from forensic verification (F16-F18).
6. Make release certification candidate-bound and add rollback (F20-F22).
7. Add the zero-matter workspace and duplicate-submission guards (F23, F25).

### Longer-term improvements

1. Freeze every migration and continuously test clean install, historical
   upgrade, and supported downgrade equivalence.
2. Consolidate runtime schemas and domain constants across Python, TypeScript,
   SQL constraints, environment examples, and documentation.
3. Add externally anchored integrity checkpoints appropriate for legal evidence
   retention and document the exact guarantee of every verification indicator.
4. Establish production-scale PostgreSQL plan/query budgets, concurrency stress
   tests, and approved dependency/security scanning in CI.

## 7. Verification commands

Run the existing checks:

```bash
cd "/home/pinguicx/Documents/aviation-litigation-platform part 2"
npm test
npm run build
cd server
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
cd ..
bash scripts/test_ops.sh
git diff --check
```

Useful focused suites after remediation:

```bash
cd "/home/pinguicx/Documents/aviation-litigation-platform part 2/server"
.venv/bin/python -m pytest -q \
  tests/test_integrity_verification.py \
  tests/test_packet_pdf.py \
  tests/test_packets.py \
  tests/test_ingestion.py \
  tests/test_source_upload_api.py \
  tests/test_migration_backfill_with_existing_data.py \
  tests/test_restore_matter_access.py \
  tests/test_auth.py
```

The source state reviewed before this documentation update produced:

- Backend: `356 passed, 2 skipped`
- Frontend: `123 passed`
- Frontend production build: passed
- Ruff: passed
- Operational shell tests: passed
- `git diff --check`: passed

Those passing checks do not cover the concurrency, historical migration,
authorization-completeness, release-failure, and production-scale scenarios
listed above.
