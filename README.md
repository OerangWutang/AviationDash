# Atlas Argus

Atlas Argus is an evidence-review cockpit for aviation accident litigation.
Every claim is source-backed, conflicts remain visible, review decisions carry
reasoning, and report sections retain a traceable evidence path.

**Core workflow:** Case → Claim → Source → Conflict → Review Decision → Report

The repository contains two deliberately different modes:

- **Local demo mode** stores one mutable sample matter in this browser's
  `localStorage` as a clearly labelled local working copy. The “Acting as”
  selector is only a role simulation.
- **Server-authoritative mode** uses FastAPI and PostgreSQL for authenticated
  sessions, matter membership, evidence visibility, audit controls, reviewer
  administration, report approval, and packet generation. Reviewers can switch
  among matters for which they have an active membership.

The bundled development sample is Colgan Air Flight 3407, NTSB DCA09MA027.
Production does not load that sample.

## Doctrine

- **Privilege withholds.** Attorney-client, work-product, and restricted
  material cannot become report-eligible, regardless of review outcome.
- **Nothing silently disappears.** Rejected, superseded, disputed, and
  preserved claims keep their source and history through the supported API.
- **No report text without evidence references.** A report section must cite at
  least one existing claim and remains blocked while a citation is unusable.
- **Every review decision needs reasoning.** A note of at least 20 characters
  lands in decision history and the case audit trail.
- **Discrediting one source does not prove the opposing claim.** The surviving
  claim returns to review rather than becoming automatically accepted.
- **No silently dead controls.** Disabled actions explain the governing role,
  approval, privilege, or conflict rule.

## Document ingestion and quote verification

Server mode accepts PDF uploads. Each page is extracted through its native text
layer where it has a usable one, and OCR where it does not — decided primarily
by how much of the page is a raster image, because a scanned page carrying a
Bates or protective-order header in its text layer would otherwise be mistaken
for a native page and its entire body silently discarded.

A claim that cites an extracted page has its quote checked against that page:

- present in native text, or in OCR text the engine is confident about →
  **exact match**, always shown alongside which of the two it was;
- present in low-confidence OCR, or the page failed to extract, or the retained
  text was truncated → **needs attestation**;
- absent from complete, untruncated page text → **rejected outright**.

That last distinction is the point. An absence only counts as a finding when
the text is complete; where extraction fell short, the honest answer is "not
established", never a silent pass. Matching ignores typography — Unicode
normalisation, curly quotes, line-wrap hyphenation, whitespace, case — so a
correctly pasted quote is not rejected over formatting.

A claim needing attestation cannot reach a report until an MFA-verified Senior
Aviation Counsel **other than the claim's author** confirms it with recorded
reasoning. There is no in-app page-image viewer in this build, so that reviewer
is attesting they consulted the original document outside Atlas Argus.

Extraction is versioned and append-only, and each run carries a manifest hash
over every page's text hash, method, confidence, and failure code; that hash
and the document's content hash both enter the case audit chain. Extraction
itself runs in a killable child process with capped memory, CPU, page count,
page canvas, and text budgets. Sources ingested before this feature keep
working and are labelled `legacy_unverified` rather than retroactively blocked.

Uploading requires the poppler-utils and tesseract-ocr system packages; the
image and CI install both.

## Claims, conflicts, and report sections

Claims are extracted from source documents with claim text, a page or section
reference, and a verbatim quote. Privilege is inherited from the source; a
reviewer cannot downgrade it during extraction. `pageRef` remains the human
citation locator and is never conflated with a PDF's internal page index.

Flagging a conflict selects exactly two claims and records a type, severity, and
summary. The pair cannot have two open conflicts. Both claims become disputed
until review resolves, preserves, or escalates the conflict.

Report sections:

- require at least one cited claim;
- use unique, case-insensitive paragraph references;
- expose live citation usability in the editor;
- retain append-only revision records; and
- revert to `draft` whenever edited in server mode.

In server mode, a production packet includes a section only when its active
revision has been approved by a reviewer whose role in that matter is Senior
Aviation Counsel. Approval is available only while the section is report-
eligible and is bound to a digest of its cited claims, sources, quote
verification, and conflicts. Any later evidence-state change requires explicit
reapproval before the section can return to production. Approval and revision
requests use optimistic versions so a stale browser cannot overwrite a newer
decision or draft.

## Identity, roles, and privilege visibility

Server mode has two authorization planes:

- `Reviewer.role` is the **global account role** returned with the session. It
  governs reviewer-directory and account administration.
- `CaseMember.role`, returned as `caseMembership.role`, is the **matter role**.
  It governs evidence visibility and legal acts in that matter, including
  critical decisions, report approval, packet generation, and case-audit role
  attribution.

The roles can differ. The header shows both when they do. An active matter
membership is required for every matter route. Only a Senior Aviation Counsel
matter role has privilege clearance: other matter roles do not receive
withholding-privilege sources or claims, nor dependent conflicts, decisions,
report sections, or related audit entries.

Reviewer and membership administration requires a global Senior Aviation
Counsel account, a verified MFA session, and—when changing a matter's
membership—active access to that matter.

## Authentication and browser session handling

Passwords are Argon2id hashes. Sessions are server-side and revocable; the
browser receives an HttpOnly, SameSite=Lax cookie while PostgreSQL stores only
the token's SHA-256. Production forces `Secure` cookies.

Login and session responses include an absolute `expiresAt` value. The frontend
enforces it locally, revalidates the session every five minutes and when the page
becomes active, and clears authenticated matter state on expiry, a session 401,
or an identity mismatch. Request epochs prevent late responses from an old
session or matter from being applied after sign-out, sign-in, or matter switch.

Provisioned production accounts must change their initial password before case
routes are available. Password changes revoke every other session. Login and
TOTP failures use persistent PostgreSQL throttles shared across API processes.
Login also has a fixed aggregate admission window whose row lock serializes
Argon2 work, so rotating through random usernames cannot run unbounded password
hashes concurrently.

TOTP MFA can be enrolled and verified in the UI. Review decisions, report
approval, packet operations, integrity verification, and all administration
routes require MFA verification for the current session.

An MFA-verified global Senior Aviation Counsel can reset another reviewer's
enabled factor through reviewer administration. The reset cannot target the
administrator's own factor; it clears the target's MFA throttle, revokes every
target session, appends an account-audit event, and requires fresh enrollment.
Password reset deliberately does not clear MFA. There is no self-service
recovery or backup-code flow, so a real deployment must provision at least two
global Senior accounts and approve an identity-verification/recovery procedure.

## Evidence packets

Two packet types implement different disclosure rules:

- **Internal work-product packet:** every report section regardless of status,
  its cited evidence (including privileged material marked `DO NOT PRODUCE`),
  internal assessments, and the decision log with reasoning.
- **Production packet:** only approved, report-eligible sections. Withheld
  material appears only in a source-identity privilege log. Withheld and
  excluded manifest rows use packet-local `WITHHELD-###`/`EXCLUDED-###`
  references and generic metadata instead of internal section/revision/claim
  identifiers. Confidence, evidence quality, reviewer identities, and decision
  reasoning are stripped. A conflict with a privileged counterpart identifies
  only that a counterpart was withheld.

In server mode, generating either packet requires a Senior Aviation Counsel
matter role and verified MFA. Internal packet content can only be reopened by a
privilege-cleared matter member. A redacted production artifact can be reopened
by another active matter member with MFA, but generator attribution is omitted
from artifact metadata shown to a requester without privilege clearance.

The backend builds, redacts, renders, and stores server packets. Evidence and
metadata are escaped for HTML. The production document and persisted manifest
omit generator attribution; the database retains it separately for privileged
audit use. The production renderer does not serialize fields from withholding-
privilege sources or claims into the stored document/manifest. It cannot detect
privileged substance manually repeated in counsel-authored section text, so
Senior approval remains a substantive disclosure boundary. A client can also
fabricate an unrelated local file; it will not have the server's stored
artifact, audit, or integrity-chain provenance.

Generating a packet also renders a **paginated PDF** server-side and stores it
with the artifact. That PDF is the produced document: fixed Letter pages, a
running classification header, and the packet ID, page number, and body-hash
prefix on every page, so a page separated from the bundle is still
identifiable. Its hash joins the packet integrity chain, and download is
refused for any artifact that fails verification. Printing the HTML preview
from a browser remains available for reading, but produces a different document
that depends on the browser and page setup — it is not the produced artifact.

The rendered packet is also a self-contained, print-ready HTML document. Its body
SHA-256 is embedded in the footer. The server also records document, manifest,
artifact, and packet-chain hashes. These hashes detect inconsistency; they are
not an external digital signature, trusted timestamp, or WORM guarantee.

Local mode still uses the TypeScript packet renderer. Doctrine changes must
keep the TypeScript and Python renderers aligned, while respecting the explicit
difference that local mode has no separate revision-approval workflow.

## Development

Local browser mode:

```bash
npm install
npm run dev                 # http://localhost:5173
```

Containerized development stack:

```bash
docker compose up -d        # http://localhost:8100
```

Development-only sample logins are `mokafor`, `dreeves`, `pnatarajan`, and
`tlindqvist`, all with password `argus-demo`.

Vite hot reload against the server-authoritative backend:

```bash
docker compose up -d db
cd server
.venv/bin/python -m atlas_argus.db.bootstrap
.venv/bin/uvicorn atlas_argus.api.app:app --port 8100
# In another shell from the repository root:
VITE_API_URL=http://localhost:8100 npm run dev
```

Verification:

```bash
npm test
npm run typecheck
npm run build
bash scripts/verify.sh       # canonical frontend + backend gate against PostgreSQL
```

See [the API contract](docs/api-contract.md) for request/response behavior.

## Production initialization and launch

Production never seeds the demo matter or demo reviewers. Normal production
bootstrap migrates with the schema-owner URL, grants the restricted runtime
role, and refuses to start if the application database is empty.

For a new database, follow the one-shot
[greenfield initialization guide](docs/deploy/greenfield-initialization.md).
The initializer:

- requires `ATLAS_ARGUS_ENV=production` and the migration-owner connection;
- accepts a strict, credential-free manifest for the first matter and Senior
  Aviation Counsel account;
- reads the initial password only from the one-shot
  `ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD` environment variable;
- refuses any populated application database;
- creates the matter, account, active Senior membership, and root case/account
  audit events atomically; and
- marks the account for password rotation and leaves MFA unenrolled.

Do not put `ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD` in `.env.production` or in the
manifest. Remove it from the operator environment after the initializer exits.

Production helpers:

```bash
cp .env.production.example .env.production   # replace every placeholder
set -a && source .env.production && set +a
bash scripts/validate_production_env.sh
bash scripts/validate_release_package.sh
bash scripts/production_launch_check.sh
```

The launch check validates a clean reviewed source package, runs verification
in an isolated development Compose project, builds the non-demo image as
`ATLAS_ARGUS_IMAGE_TAG`, force-recreates the production API from that exact tag,
waits for health, then performs smoke, backup, offsite-copy, restore-drill, and
sign-off gates. Build/deploy skips are paired rehearsal-only controls; a skipped
deployment cannot be used to produce smoke evidence.

Backup and restore helpers independently select
`ATLAS_ARGUS_PRODUCTION_COMPOSE_FILE` in production and refuse
`docker-compose.yml`:

```bash
bash scripts/backup_db.sh
ATLAS_ARGUS_BACKUP_OFFSITE_ENCRYPTED=1 \
  bash scripts/backup_offsite.sh backups/<dump>.dump
CONFIRM_API_QUIESCED=1 CONFIRM_RESTORE=1 \
  bash scripts/restore_db.sh backups/<dump>.dump
```

See the [production release gate](docs/deploy/release-gate.md),
[production-readiness record](docs/production-readiness.md), and
[backup cron example](docs/deploy/backup-cron.example).

## Backend safeguards

- Application evidence operations require active matter membership; PostgreSQL
  row-level security provides a second check against omitted case predicates.
- The runtime role is distinct from the schema owner and is rejected if it is a
  superuser, bypasses RLS, or owns application tables.
- Case audit, account audit, review decisions, report revisions, and packet
  artifacts reject update/delete at the database and carry SHA-256 chains.
- Conflict and report-section writes use `expectedVersion`; stale writes return
  `409`, and the frontend reloads current matter state without applying the
  stale response.
- State-changing bodies and persisted packet artifacts have configurable size
  limits.
- API responses are `Cache-Control: no-store` and include CSP,
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and
  `Permissions-Policy` protections.
- Production CORS defaults to no cross-origin origins. If explicitly configured,
  only exact HTTPS origins are accepted. Development defaults to the two local
  Vite origins.
- JSON request logs and token-protected Prometheus metrics are available for
  deployment monitoring.

RLS and hash chains are defense in depth, not substitutes for database-secret
protection, SQL-injection prevention, independent signatures, or immutable
storage. The production limitations are tracked explicitly in
[production readiness](docs/production-readiness.md).

## Repository structure

```text
src/                         React UI, domain logic, local state, typed API client
server/src/atlas_argus/      FastAPI services, auth, packet engine, DB and migrations
scripts/                     verification, launch, smoke, backup and restore automation
docs/deploy/                 release, initialization, security and evidence checklists
```

## Current product limitations

- Local mode is browser-local and intentionally not an authoritative legal
  record.
- Server mode can list and switch active matters, but there is no general
  matter-create/import/search workflow after the one-shot first-matter
  initializer.
- Uploaded documents are stored in PostgreSQL rather than object storage, so
  every backup and replica carries the full corpus.
- There is no in-app document or page-image viewer; manual quote attestation
  means consulting the original evidence outside the application.
- A source can be extracted once. Re-running extraction against improved OCR
  would change what existing claims were verified against, so switching a
  source's active run needs a reviewed activation workflow that does not exist
  yet.
- Ingestion pins the deployment to a single API process; see
  [production readiness](docs/production-readiness.md) for why and what
  scaling out would require.
- Report text uses a structural cited-claims list; inline bracket references
  remain an authorial convention.
- PDF export is byte-reproducible only for a fixed image; verification checks
  the stored bytes rather than re-rendering.
- MFA recovery is administrator-mediated only: there are no backup codes or
  self-service reset, and a second MFA-verified global Senior is required.
