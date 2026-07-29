# Production readiness

This is an evidence-backed status record, not a claim that the application is
approved for production. “Implemented” means the control exists in this
repository and is covered by the automated gate or a focused operational test.
Deployment-specific controls and independent review remain the operator's work.

> **MFA recovery posture:** Atlas Argus implements an audited, administrator-
> mediated factor reset. It deliberately has no self-service reset or backup
> codes and forbids an administrator from resetting their own factor. A real
> deployment therefore requires at least two independently controlled global
> Senior accounts and an approved identity-verification/recovery procedure.

## Implemented — authentication and authorization

| Control | Current behavior |
| --- | --- |
| Passwords and sessions | Argon2id password hashes; 12-hour server-side revocable sessions; HttpOnly, SameSite=Lax cookie; only the session token's SHA-256 is stored. Production forces `Secure`. Login/session responses include absolute `expiresAt`. |
| Frontend expiry containment | The server-mode store rejects invalid/expired session timestamps, schedules local expiry, revalidates every five minutes and on focus/pageshow/visibility, and scrubs session and matter state on expiry, 401, or identity mismatch. Session/matter epochs discard late responses from superseded requests. |
| Password lifecycle | Provisioned production accounts require password rotation before case API use. A password change revokes all other sessions. Account deactivation is enforced at login and session resolution. |
| Login throttle | Five consecutive failures per normalized username produce a 60-second `429`. A fixed aggregate 60-second admission window also caps total login attempts and serializes Argon2 work across API processes. Unknown usernames run a real verify against a fixed dummy Argon2 hash; stale unlocked throttle rows are swept after a quiet window. |
| TOTP MFA | UI and API support enrollment, enablement, status, and per-session verification. Five invalid codes produce a persistent 60-second lockout. Decision, approval, packet, integrity, and administration routes require verified MFA. Enrollment cannot replace an enabled factor. Another MFA-verified global Senior can reset it; reset revokes target sessions, clears the target throttle, and appends account audit. |
| Two role planes | Global `Reviewer.role` governs reviewer-account administration. Per-matter `CaseMember.role` governs evidence visibility, critical decisions, report approval, packet generation, and case-event attribution. The UI displays both when they differ. |
| Matter membership | Explicit `/cases/{case_id}` routes require an active membership and reject cross-matter object IDs. The UI lists and switches among the reviewer's active matters. |
| Privilege visibility | Only a Senior Aviation Counsel matter role has privilege clearance. Other matter roles do not receive withholding-privilege sources/claims or dependent conflicts, decisions, report sections, and audit entries. Visible claim relationships and history-aware claim/section audit filtering also avoid dangling IDs or historical citation leaks. |
| Administration | Global Senior Aviation Counsel account plus verified MFA is required to list/provision/deactivate/reactivate accounts, reset passwords or another reviewer's MFA, and manage matter memberships. MFA self-reset is forbidden. Account changes append durable global account-audit events and revoke affected sessions where appropriate. |
| Identity integrity | Actor identity comes from the authenticated session. Matter roles come from active membership, not body fields. Smuggled reviewer or role values do not control authorization or audit attribution. |

## Implemented — application and browser security

| Control | Current behavior |
| --- | --- |
| CSRF/CORS | SameSite=Lax plus Origin validation on state-changing browser requests. Packaged production is same-origin and defaults to no CORS origins; configured production origins must be exact HTTPS origins. Development permits the two local Vite origins by default. |
| Fail-closed production config | Production requires non-default runtime DB credentials and a metrics token, requires a separate migration URL for migration/bootstrap operations, rejects identical runtime/owner URLs, rejects unsafe CORS entries and invalid resource limits, and forces secure cookies. |
| Browser response policy | API responses use `Cache-Control: no-store`. Responses add a restrictive CSP (including only the exact packet-style hash needed for stored packet HTML), `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Permissions-Policy`. |
| Request limits | State-changing bodies are capped from both `Content-Length` and actual streamed bytes. Pydantic bounds fields and collections. Packet entry, document, manifest, and total artifact sizes are checked before persistence or audit append. |
| SQL/XSS posture | Application data uses ORM or bound parameters. Deployment SQL interpolates only validated/quoted identifiers. Packet renderers HTML-escape evidence and metadata; the production document and persisted manifest omit internal assessment and generator identity. |
| Container | The API runs as uid 999 with a read-only root filesystem, dropped capabilities, `no-new-privileges`, and a bounded noexec/nosuid `/tmp`. Forwarded headers are trusted only from configured proxy addresses. |
| Observability | JSON request logs are emitted at INFO. Token-protected `/api/metrics` counters use matched route templates rather than unbounded raw paths. Prometheus receives its credential through a Compose secret file. |

## Implemented — evidence, packets, and integrity

| Control | Current behavior |
| --- | --- |
| Server disclosure doctrine | The backend builds, sanitizes, redacts, renders, hashes, and persists server packets. It does not serialize withholding-source/claim fields into the production document/manifest. It cannot semantically detect privileged substance manually repeated in approved report prose; Senior approval remains a disclosure boundary. A client can also fabricate an unrelated local file without server provenance. |
| Report approval | Server production packets include only an eligible section whose active revision has a current evidence-bound approval. Approval requires verified MFA, a Senior Aviation Counsel matter role, current eligibility, and the current `expectedVersion`. The approval hashes cited claims, sources, quote verification, and conflicts; any text or evidence-state change returns the section to effective `draft` until explicit reapproval. |
| Production sanitization | Production document and persisted manifest omit generator identity and internal reviewer assessments/reasoning. Withheld/excluded entries use packet-local opaque references and generic metadata, omit revision/claim identifiers, and keep privileged claims out of cited-claim/appendix output; the privilege log is source-identity-only. Non-cleared artifact readers do not receive generator ID/name/role in list/detail metadata. Internal packets remain fully attributed and require privilege clearance to reopen. |
| Controlled PDF export | Packet generation renders the stored HTML document to a paginated PDF server-side: fixed Letter page box, running classification header, and every page carrying the packet ID, page number, and body-hash prefix so a detached page stays identifiable. The PDF is stored on the artifact, hashed, and its hash joins the packet integrity chain. Download applies the same access rules as reading the artifact and refuses to serve a packet that fails verification. Production refuses to boot without the renderer, so disclosure output cannot quietly revert to browser print-to-PDF. |
| Artifact retrieval | MFA-protected per-matter routes list, reopen, and verify stored artifacts. Stored document, sanitized manifest, hashes, and integrity result are exposed according to matter privilege clearance. The UI quarantines a failed-integrity artifact by disabling open/download/print. |
| Append-only records | PostgreSQL triggers reject update/delete for case audit, account audit, review decisions, report revisions, and packet artifacts. The runtime role is also denied update/delete privileges on these tables. |
| Hash chains | Canonical SHA-256 chains cover case audit, global account audit, review decisions, per-section revisions, and each matter's packet sequence. Packet verification recomputes body/document/manifest/artifact relationships. Case/account verification endpoints report roots and issues. |
| Optimistic concurrency | Conflict decisions, report revisions, and approvals require the current aggregate version. Rows are locked before mutation; stale requests return `409`, and the frontend reloads current matter state rather than applying the stale response. |
| Packet snapshot consistency | Packet generation holds the case audit-chain transaction lock while reading and appending the artifact/event, preventing an internally inconsistent mix of concurrent case mutations. |
| Document ingestion | Uploaded PDFs are stored with an authoritative content SHA-256 and extracted per page (native text layer, OCR fallback). Extraction runs in a spawned, process-group-killable child with `RLIMIT_AS`/`RLIMIT_CPU`, bounded page count, page canvas, per-page and total text budgets, and per-page subprocess timeouts. Extraction runs and pages are append-only and versioned; the active-run pointer is set-once at the database. Custody is server-synthesized; filenames are sanitized. |
| Quote verification | A claim citing an extracted page has its quote checked against that page. A quote absent from *complete* page text is rejected `422`. A quote absent from failed, truncated, or low-confidence text becomes `manual_verification_required`, which blocks report eligibility until an MFA-verified, privilege-cleared reviewer **other than the claim's author** attests with recorded reasoning. Verification state and its basis hash are server-controlled and trigger-guarded, so no generic claim write can relabel them. |
| Extraction integrity | Each run stores a canonical manifest hash over the content hash, parser/OCR versions, config snapshot, and every page's text hash, method, confidence, truncation, and failure code. Both the content and manifest hashes are written into the per-case audit hash chain at ingestion. |

Hash chains are tamper-evident consistency controls, not external signatures,
trusted timestamps, or WORM storage. A schema owner can rewrite rows and
recompute unkeyed hashes.

## Implemented — database and operations

| Control | Current behavior |
| --- | --- |
| Migrations | Alembic scripts ship in the Python package. The test schema is built by upgrading from the migration baseline. Production migration uses `ATLAS_ARGUS_MIGRATION_DATABASE_URL`; the running API uses a distinct runtime URL. |
| Runtime role | Bootstrap verifies that the runtime role exists, differs from the owner, is not superuser/BYPASSRLS, and owns no application tables before granting restricted DML/function privileges. The Compose initializer also asserts `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOREPLICATION`, and `NOBYPASSRLS`. |
| RLS | Evidence tables use transaction-local reviewer context as defense in depth against omitted matter predicates. See the limitation below: this does not contain a stolen runtime credential. |
| Development seed | Development bootstrap can seed/reset the sample matter. Seed data is generated from `src/data/sampleCase.ts`. Production bootstrap never loads or resets sample data. |
| Greenfield initialization | A production-only one-shot command accepts a strict credential-free manifest, reads the first administrator password only from `ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD`, verifies the owner connection and an empty application database under locks, then creates the first matter/account/Senior membership/root audit events. Any populated table or concurrent initializer makes it refuse. |
| Normal production bootstrap | Migrates to head, reapplies runtime grants, verifies a real matter already exists, and refuses an empty production database. The container unsets the migration URL before executing Uvicorn. |
| Stranded-matter recovery | An owner-credentialed command readmits one active global Senior Aviation Counsel to a matter that has no active membership backed by an active account, and refuses any matter that is still reachable. It asserts schema ownership so a runtime connection cannot declare a matter stranded through its own RLS blindfold, and appends a `matter access restored` event attributed to the database operator rather than to the reviewer. See [matter access recovery](deploy/matter-access-recovery.md). |
| Health and dependencies | `/api/health` checks PostgreSQL. Compose health conditions sequence DB, API, proxy, and monitoring startup. Backend and frontend dependencies are locked. |
| Release provenance | Release-package validation rejects dirty tracked trees and forbidden secret/build/backup paths. `production_launch_check.sh` verifies in an isolated development project, builds the reviewed `ATLAS_ARGUS_IMAGE_TAG`, force-recreates and waits for that exact API tag, then runs smoke and operational gates. |
| Backups and restore | Backups are compressed, checksum-verified, mode `0600`, and emit only the dump path on stdout. Offsite copy requires an explicit encrypted-destination acknowledgement. Restore and drill are fail-fast and transactional; live restore also requires API-quiesced and restore confirmations. Production helpers select the production Compose file and refuse `docker-compose.yml`. |
| Launch sign-offs | The launch gate requires non-empty security-review, evidence-review, and restore-drill records plus validated production environment variables. |

## Production workflow

1. Build and review a clean release package; run `bash scripts/verify.sh`.
2. Create distinct migration-owner and runtime database credentials, metrics
   token, TLS/proxy settings, backup destination, and sign-off locations from
   `.env.production.example`.
3. For the first deployment only, keep the API stopped and follow
   [greenfield initialization](deploy/greenfield-initialization.md). Supply
   `ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD` only to that one-shot process and unset
   it afterward.
4. Validate the environment, build the reviewed image, and start the production
   stack as described by the initializer guide. Have the first Senior Aviation
   Counsel rotate the initial password and enroll MFA.
5. Provision a separately controlled second global Senior account and have that
   reviewer rotate and enroll. Exercise administrator-mediated reset on a test
   account and approve the identity-verification/recovery procedure.
6. Complete the independent application-security and evidence-handling reviews,
   then run the full `scripts/production_launch_check.sh` from the reviewed
   release directory. It rebuilds/redeploys the reviewed tag before smoke.
7. Capture authenticated packet-flow smoke, backup/offsite copy, restore drill,
   and review evidence in the release record.

Persistent production configuration includes:

```bash
ATLAS_ARGUS_ENV=production
ATLAS_ARGUS_DATABASE_URL=postgresql+psycopg://atlas_app:...@db:5432/atlas_argus
ATLAS_ARGUS_MIGRATION_DATABASE_URL=postgresql+psycopg://atlas_owner:...@db:5432/atlas_argus
ATLAS_ARGUS_METRICS_TOKEN=<generated>
ATLAS_ARGUS_CORS_ORIGINS=                  # empty for packaged same-origin
ATLAS_ARGUS_FORWARDED_ALLOW_IPS=<proxy-ip-or-cidr>
ATLAS_ARGUS_PRODUCTION_COMPOSE_FILE=docker-compose.production.example.yml
ATLAS_ARGUS_IMAGE_TAG=atlas-argus:production
ATLAS_ARGUS_BACKUP_DB_USER=atlas_owner
```

`ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD` is deliberately absent from this persistent
configuration. There is no production seed-password setting.

## Known gaps and residual risks

1. **MFA recovery depends on another administrator.** Reset is audited and
   revokes the target's sessions, but there is no self-service flow or backup
   code and self-reset is forbidden. Losing the only global Senior factor would
   still require database-level intervention. Provision at least two independent
   global Senior accounts and maintain a reviewed recovery ceremony.
2. **Hash chains are not independent proof.** They are unkeyed and live beside
   the data. Use external signing/timestamping or immutable storage if the
   evidentiary standard requires resistance to a schema-owner rewrite.
3. **RLS does not contain runtime-credential compromise.** A holder of the
   runtime credential can set the transaction-local reviewer context. Secret
   protection and SQL-injection prevention remain part of matter isolation.
4. **MFA seed confidentiality relies on database controls.** TOTP secrets must
   be recoverable by the application for verification and are not protected by
   a separate application-level envelope-encryption key in this repository.
5. **Dual packet renderers require coordinated doctrine changes.** TypeScript
   serves local demo mode and Python serves authoritative server mode. Local
   mode also intentionally lacks server revision approval.
6. **Deployment ownership remains external.** Off-host encryption/key rotation,
   legal-hold retention, job monitoring, incident response, independent review,
   and recurring restore drills must be operated and evidenced by the deployer.
7. **General matter intake is not implemented.** Server mode lists and switches
   active matters, but only the one-shot initializer creates the first matter;
   there is no normal matter-create/import/search workflow.
8. **Recovering a stranded matter is an operator act.** Membership
   administration requires the actor's own active membership, so a matter with
   no active membership backed by an active account has no in-app way back.
   Recovery runs against the migration-owner connection and is audited on the
   matter chain; see [matter access recovery](deploy/matter-access-recovery.md).
   Deliberately not an API route — the runtime role cannot even see a stranded
   matter, and giving it that power would extend it to a stolen runtime
   credential per gap 3.
9. **PDF output depends on the installed renderer and fonts.** Packets are now
   rendered server-side to a paginated PDF, stored, and hashed. But the exact
   bytes depend on the WeasyPrint, fontTools, Pango and font versions in the
   image, so re-rendering an old packet on a newer image may not reproduce it.
   Verification therefore recomputes the hash of the **stored** bytes and never
   re-renders. Rendering is byte-reproducible for a fixed image (font
   subsetting is pinned via `SOURCE_DATE_EPOCH`), which is what makes a
   rendering regression visible in CI.
10. **Database-level privilege isolation is partial.** The ingestion tables
    (`source_document_file`, `source_extraction_run`, `source_page_extraction`)
    have privilege-aware RLS mirroring `_source_visible_to`. The pre-existing
    `source_document` and `claim` tables still use the membership-only policies
    from `0011_case_rls.py` and rely on the service-layer check alone, exactly
    as before this feature. Tightening them is a narrowly-scoped, natural
    follow-up, but it changes already-shipped behavior and deserves its own
    review rather than riding along with an ingestion migration.
11. **Ingestion pins the deployment to one API process.** Admission control is
    a process-local semaphore, so N workers would allow N times the configured
    concurrent uploads and N times the memory, while still reporting the
    configured value. The maintained production Compose file pins
    `--workers 1` and a test asserts it. Horizontal scaling requires moving
    extraction to a dedicated ingestion service or adding a cross-process
    coordinator first — it is a real architecture step, not a flag change.
12. **Extraction isolation is not a sandbox.** The child process is killable as
    a group and capped on address space and CPU, which contains hangs and
    runaway allocation. It still inherits the parent's filesystem and
    environment, including database credentials. A separate service with no
    credentials, no network, and cgroup limits remains the stronger answer.
13. **Documents are stored in Postgres `bytea`.** Deliberate for this pass, and
    fine at modest volume, but every backup, WAL segment, and replica carries
    the full document corpus. Object storage is the scaling answer.
14. **There is no in-app document or page-image viewer.** Manual attestation
    means a reviewer consulted the original evidence *outside* Atlas Argus; the
    verification screen states this explicitly rather than letting extracted
    text stand in for having looked at the page.
15. **Reprocessing a source is not implemented.** The schema supports multiple
    immutable extraction runs, but the active-run pointer may only go from NULL
    to a source's first run — a database trigger refuses anything else.
    Re-pointing a source changes what every existing claim on it was verified
    against, so it needs an MFA-gated activation workflow with its own history
    and claim-impact review.
16. **OCR matching proves what Tesseract read, not what the page says.** A
    `verified_exact` badge on an OCR page means the quote appears in the
    engine's interpretation of the image. The UI always names the extraction
    method alongside the badge so the two are never read as equal-strength
    evidence, and low-confidence matches are routed to human attestation.
17. **PDF rendering pulls in a tri-licensed dependency.** WeasyPrint is BSD-3,
    but depends on pyphen, which is GPL2+/LGPL2.1+/MPL 1.1. LGPL or MPL is
    selectable and applies to an unmodified imported library, and the packet
    stylesheet never enables `hyphens: auto`, which is the only path that loads
    pyphen's separately-licensed dictionaries. Confirm that conclusion with
    counsel, as with poppler-utils.
18. **Missing fonts degrade silently.** The packet stylesheet names serif and
    sans families. On an image without them, text falls back and repaginates
    while still producing a plausible-looking PDF. The image installs
    metric-compatible families and a deployment test asserts it, but a custom
    image that drops them would not fail loudly.
19. **Quote normalisation includes a heuristic.** Line-wrap de-hyphenation
    joins `cross-\ncheck` into `crosscheck`, which is right for PDF line breaks
    and wrong for a genuinely hyphenated compound broken across lines. It is
    applied because the former is far more common in extracted text, but it is
    an imprecision, not an exact rule.

Track release evidence with [the release gate](deploy/release-gate.md),
[security-review scope](deploy/security-review-scope.md), and
[evidence-handling review](deploy/evidence-handling-review.md).
