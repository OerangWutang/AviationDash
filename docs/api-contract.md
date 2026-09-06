# Atlas Argus API contract

The FastAPI backend uses camelCase JSON for its public wire format. Core case
entities intentionally align with `src/domain/types.ts`, but the API is not a
one-to-one serialization of that file: authentication, case-membership,
administration, integrity, pagination, and stored-artifact DTOs are defined in
`src/api/client.ts` and the server serializers.

Development base URL: `http://localhost:8100/api`.

Paths below are relative to `/api` unless explicitly stated otherwise.

## Transport and errors

Sessions use the `atlas_session` cookie (`HttpOnly`, `SameSite=Lax`, 12-hour
server expiry; `Secure` is forced in production). Only the token's SHA-256 is
stored in PostgreSQL. Identity and global account role come from that session,
never from a request-body `reviewerId` or role field.

Domain failures use:

```json
{"detail": "Human-readable domain message."}
```

Common status codes:

- `400`: malformed transport input such as an invalid `Content-Length`;
- `401`: absent, revoked, expired, or invalid authentication;
- `403`: missing matter membership on the *requested* matter, insufficient
  role, forced password change, missing MFA enrollment/verification, reopening
  a stored internal packet without privilege clearance, or a rejected browser
  Origin;
- `404`: object not found in the requested matter (also used to avoid
  cross-matter object disclosure);
- `409`: duplicate open conflict or stale optimistic version;
- `413`: request body exceeds the configured byte limit;
- `422`: schema or domain validation failure; and
- `429`: persistent login or MFA throttle lockout.

FastAPI/Pydantic schema errors retain the standard structured `422` shape.

### Unreadable evidence answers as non-existent

When a request *references* an object by identifier, an object the caller may
not read answers exactly as one that was never created. Claim and source
identifiers are issued from dense sequences, so a distinguishable rejection
would let any authenticated reviewer walk the ID space and learn which evidence
in their own matter is privilege-withheld, and which identifiers belong to
matters they are walled off from.

| Reference | Withheld, out-of-matter, or absent |
| --- | --- |
| `sourceDocumentId` on claim extraction | `404` `Source document not found.` |
| `claimAId`/`claimBId` on conflict flagging | `422` `Both claims must exist.` |
| `claimIds` on report-section drafting/revision | `422` `Cited claim not found: <id>.` |

This applies only to referenced objects. The caller's own membership in the
matter named by the route is still checked first and still returns `403`.

## CORS, CSRF, and response policy

Packaged production is same-origin and defaults to an empty CORS allowlist.
When `ATLAS_ARGUS_CORS_ORIGINS` is explicitly set in production, every entry
must be an exact HTTPS origin without credentials, path, query, or fragment.
Development defaults to `http://localhost:5173` and
`http://127.0.0.1:5173`.

SameSite cookies and an Origin check protect state-changing browser requests.
The request's own origin is accepted; configured origins are accepted; a
different supplied Origin is rejected. Non-browser clients that omit Origin are
not treated as browser CSRF requests and must protect their credentials.

Every `/api/*` response is `Cache-Control: no-store`. Responses also carry a
restrictive CSP, `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, and `Permissions-Policy`.

## Authentication DTO and lifecycle

`POST /auth/login` and `GET /auth/session` return:

```json
{
  "reviewer": {
    "id": "rev-1",
    "name": "Reviewer Name",
    "role": "Senior Aviation Counsel"
  },
  "mustChangePassword": false,
  "mfa": {"enabled": true, "verified": false},
  "expiresAt": "2026-07-18T20:00:00Z"
}
```

`reviewer.role` here is the global account role. It is not necessarily the role
the reviewer holds in a particular matter.

The frontend rejects invalid or elapsed `expiresAt` values, schedules a local
expiry, rechecks `/auth/session` every five minutes and on focus, pageshow, or
visibility activation, and clears authenticated case state on expiry, a session
401, or reviewer-identity mismatch. Session and case epochs prevent responses
started under an older login or matter from updating the current store.

Provisioned production accounts have `mustChangePassword=true`. Case access,
MFA enrollment/verification, and administration remain blocked until the
initial password is rotated; session inspection, MFA status, password change,
and logout remain available. Changing a password revokes every other session.

### MFA

MFA uses six-digit TOTP:

1. `POST /auth/mfa/enroll` returns `{secret, otpauthUri}` for an account that has
   not enabled MFA.
2. `POST /auth/mfa/enable` with `{code}` verifies the new factor, enables it,
   and marks the current session verified.
3. A later session uses `POST /auth/mfa/verify` with `{code}`.

Five invalid MFA codes produce a 60-second `429` lockout. Counters live in
PostgreSQL, so they persist across requests and API processes. Invalid attempts
before lockout return `422`; their counters are committed even though the
verification fails.

Enrollment deliberately refuses to replace an enabled factor. An MFA-verified
global Senior Aviation Counsel can reset another reviewer's enabled factor
through `POST /admin/reviewers/{id}/reset-mfa`. Self-reset is forbidden. A
successful reset clears the target's factor and MFA throttle, revokes all target
sessions, appends an account-audit event, and requires re-enrollment. Password
reset does not remove MFA. There is no self-service recovery or backup-code
endpoint, so deployments need at least two global Senior accounts and an
identity-verification procedure for administrator-mediated recovery.

The following operations require MFA verification for the current session:

- conflict review decisions;
- report-section approval;
- packet generation, list, retrieval, and packet/case integrity checks; and
- every global-account or matter-membership administration route.

## Endpoint map

### Authentication and operations

| Operation | Endpoint | Success | Common errors |
| --- | --- | --- | --- |
| Login | `POST /auth/login` | `200` | `401`, `422`, `429` |
| Logout | `POST /auth/logout` | `200` | `403` Origin |
| Inspect session | `GET /auth/session` | `200` | `401` |
| Change password | `POST /auth/change-password` | `200` | `401`, `422` |
| MFA status | `GET /auth/mfa/status` | `200` | `401` |
| Start MFA enrollment | `POST /auth/mfa/enroll` | `200` | `401`, `403` |
| Enable MFA | `POST /auth/mfa/enable` | `200` | `401`, `403`, `422`, `429` |
| Verify MFA | `POST /auth/mfa/verify` | `200` | `401`, `403`, `422`, `429` |
| Database readiness | `GET /health` | `200` | service unavailable |
| Prometheus metrics | `GET /metrics` | `200` | `401`, `503` |

`GET /metrics` does not use the session cookie. When a metrics token is
configured, send `Authorization: Bearer <token>` or `X-Metrics-Token`. Production
configuration requires a token; the endpoint still fails closed with `503` if a
production process somehow starts without one.

### Matters and evidence

| Operation | Endpoint | Success | Common errors |
| --- | --- | --- | --- |
| List active matters | `GET /cases` | `200` | `401`, `403` |
| Load one matter | `GET /cases/{case_id}` | `200` | `401`, `403`, `404` |
| Extract claim | `POST /cases/{case_id}/claims` | `201` | `401`, `403`, `404`, `422` |
| Flag conflict | `POST /cases/{case_id}/conflicts` | `201` | `401`, `403`, `404`, `409`, `422` |
| Save review decision | `POST /cases/{case_id}/conflicts/{id}/decisions` | `201` | `401`, `403`, `404`, `409`, `422` |
| Draft report section | `POST /cases/{case_id}/report-sections` | `201` | `401`, `403`, `404`, `422` |
| Revise report section | `PUT /cases/{case_id}/report-sections/{id}` | `200` | `401`, `403`, `404`, `409`, `422` |
| Approve report section | `POST /cases/{case_id}/report-sections/{id}/approve?expectedVersion=N` | `200` | `401`, `403`, `404`, `409`, `422` |
| Verify case integrity | `GET /cases/{case_id}/audit/verify` | `200` | `401`, `403`, `404` |
| Download packet PDF | `GET /cases/{case_id}/packets/{packet_id}/pdf` | `200` | `401`, `403`, `404` |
| Upload source document | `POST /cases/{case_id}/sources` | `201` | `401`, `403`, `404`, `409`, `413`, `422`, `503` |
| List extracted pages | `GET /cases/{case_id}/sources/{source_id}/pages` | `200` | `401`, `403`, `404` |
| View original source PDF | `GET /cases/{case_id}/sources/{source_id}/file` | `200` | `401`, `403`, `404` |
| Attest to a claim quote | `POST /cases/{case_id}/claims/{claim_id}/verify-quote` | `200` | `401`, `403`, `404`, `422` |

Conflict decisions and integrity verification require MFA. Approval additionally
requires a Senior Aviation Counsel role in the selected matter.

## Document ingestion and quote verification

Uploading a PDF stores its bytes, extracts each page (native text layer where
one exists, OCR where it does not), and makes those pages citable. A claim that
cites an extracted page has its quote checked against that page's text.

### Upload

`POST /cases/{case_id}/sources` takes the document base64-encoded in
`contentBase64`, plus `title`, `type`, `origin`, `custodian`, `docketRef`,
`privilegeStatus`, `originalFilename`, and an optional `idempotencyKey`.

There is deliberately **no `custody` field**. Custody is what makes a document
admissible, so the server builds the first entry from what it knows — who
uploaded it, in what matter role, when, and the SHA-256 of the bytes.

Requires MFA. Any active member may upload non-withholding material; uploading
`attorney_client`, `work_product`, or `restricted` material requires a Senior
Aviation Counsel matter role, because privilege-aware row-level security would
otherwise hide the row from its own uploader.

The response returns the source (including `contentSha256`, the authoritative
hash computed from the stored bytes, and `pageCount`), the audit event, and
`possibleDuplicateSourceIds`.

**Idempotency and duplicates are different things.** Repeating a request with
the same `idempotencyKey` returns the original result (`replayed: true`); reusing
that key with different content is a `409`, since answering with the stored
result would silently substitute one piece of evidence for another. Uploading
byte-identical content *without* a repeated key succeeds and creates a separate
record — the same document legitimately enters a matter through different
custodians — and lists the matches in `possibleDuplicateSourceIds`, filtered to
sources the caller may see.

A `503` means too many uploads are already in flight; retry shortly.

### Quote verification

`POST /cases/{case_id}/claims` accepts `sourcePageExtractionId`. There is no
field for the verification result: that is the server's conclusion, not the
client's assertion, and a supplied value is ignored.

| Situation | `quoteVerification` |
| --- | --- |
| Source predates ingestion | `legacy_unverified` |
| Quote found in native page text | `verified_exact` |
| Quote found in OCR text at or above `ATLAS_ARGUS_MIN_OCR_CONFIDENCE_FOR_AUTO_VERIFY` | `verified_exact` |
| Quote found in OCR text below that threshold | `manual_verification_required` |
| Page could not be extracted | `manual_verification_required` |
| Quote absent from *truncated* page text | `manual_verification_required` |
| Quote absent from complete page text | **`422` — the claim is not created** |

The last two rows are the distinction the feature turns on: an absence is only
a finding when the text is complete. Matching ignores typography (Unicode
normalisation, curly quotes, line-wrap hyphenation, whitespace, case) so a
correctly pasted quote is not rejected over formatting.

`manual_verification_required` blocks report eligibility regardless of review
status. `legacy_unverified` does not — existing matters are labelled honestly
rather than retroactively blocked.

### Manual attestation

`POST /cases/{case_id}/claims/{claim_id}/verify-quote` takes `reasoning` (20+
characters). It requires MFA, a Senior Aviation Counsel matter role, and a
reviewer **other than the claim's author** — one person must not be able to
both assert a quote and clear it. The attestation is recorded as a structured,
append-only row (who, when, what role, why, which page, which basis hash), not
as prose inside an audit detail string.

This build has no in-app page-image viewer, so the reviewer is attesting that
they consulted the original document outside Atlas Argus.

### Packet artifacts

| Operation | Endpoint | Success | Common errors |
| --- | --- | --- | --- |
| Generate packet | `POST /cases/{case_id}/packets` | `201` | `401`, `403`, `404`, `422` |
| List stored packets | `GET /cases/{case_id}/packets?limit=25&offset=0` | `200` | `401`, `403`, `404` |
| Read stored packet | `GET /cases/{case_id}/packets/{id}` | `200` | `401`, `403`, `404` |
| Verify packet | `GET /cases/{case_id}/packets/{id}/verify` | `200` | `401`, `403`, `404` |

All packet routes require MFA and active matter membership. Generation of both
packet types requires a Senior Aviation Counsel matter role. Internal packet
content requires the same privilege clearance when reopened. Production packet
content is already redacted, so another active matter member may reopen it; a
requester without privilege clearance does not receive generator reviewer ID,
name, or role in stored-artifact metadata.

### Account and matter-membership administration

These routes require a global Senior Aviation Counsel account and verified MFA.
Matter-membership routes also require the administrator to be an active member
of the target matter.

| Operation | Endpoint | Success | Common errors |
| --- | --- | --- | --- |
| List reviewer accounts | `GET /admin/reviewers` | `200` | `401`, `403` |
| Create reviewer | `POST /admin/reviewers` | `201` | `401`, `403`, `422` |
| Deactivate reviewer | `POST /admin/reviewers/{id}/deactivate` | `200` | `401`, `403`, `404`, `422` |
| Reactivate reviewer | `POST /admin/reviewers/{id}/reactivate` | `200` | `401`, `403`, `404` |
| Reset reviewer password | `POST /admin/reviewers/{id}/reset-password` | `200` | `401`, `403`, `404`, `422` |
| Reset reviewer MFA | `POST /admin/reviewers/{id}/reset-mfa` | `200` | `401`, `403`, `404`, `422` |
| Read account audit | `GET /admin/account-audit` | `200` | `401`, `403` |
| Verify account audit | `GET /admin/account-audit/verify` | `200` | `401`, `403` |
| List matter members | `GET /admin/cases/{case_id}/members` | `200` | `401`, `403`, `404` |
| Add/update matter member | `POST /admin/cases/{case_id}/members` | `201` | `401`, `403`, `404`, `422` |
| Deactivate matter member | `POST /admin/cases/{case_id}/members/{reviewer_id}/deactivate` | `200` | `401`, `403`, `404`, `422` |
| Reactivate matter member | `POST /admin/cases/{case_id}/members/{reviewer_id}/reactivate` | `200` | `401`, `403`, `404`, `422` |

There is no server-side case-reset endpoint. Local demo reset only clears the
browser working copy.

## Matter DTOs and authorization planes

`GET /cases` returns:

```json
{
  "cases": [
    {
      "caseFile": {
        "id": "case-1",
        "name": "Matter name",
        "aircraft": "Airbus A320-200",
        "accidentDate": "2025-04-19",
        "location": "North Sea",
        "matterType": "wrongful_death",
        "status": "open",
        "docketRef": "MATTER-2025-001"
      },
      "caseMembership": {
        "caseId": "case-1",
        "reviewerId": "rev-1",
        "role": "Claims Reviewer",
        "isActive": true
      }
    }
  ]
}
```

Only active memberships are listed. `GET /cases/{case_id}` returns the store
bootstrap shape:

- `caseFile`
- `caseMembership`
- `reviewers`
- `sources`
- `claims`
- `conflicts`
- `conflictOrder`
- `decisions`
- `auditEvents` (newest first)
- `reportSections`

`caseMembership.role` is the authenticated reviewer's authority in this matter.
The `reviewers[].role` values in this payload are also matter roles. They are not
the reviewers' global account roles.

An active matter membership authorizes case access. Only the Senior Aviation
Counsel matter role has privilege clearance. Other roles receive a filtered
payload: withholding-privilege sources and claims are removed, along with
conflicts, decisions, report sections, and audit events that would reveal them.
Visible claims contain only visible `relatedConflictIds`; historical claim and
section audit events are also removed when old relationships or citations would
reveal evidence now hidden from the requester.

Explicit case routes reject IDs belonging to another matter, generally as 404.
For example, posting a claim to `case-a` with a source ID from `case-b` cannot
create cross-matter evidence.

`claims[].relatedConflictIds` and `conflicts[].decisionIds` are derived from
relational tables at read time rather than stored arrays.

### Global role versus matter role

- The session's `reviewer.role` controls the global account plane.
- `caseMembership.role` controls evidence visibility, critical-decision
  authority, report approval, packet generation, and case audit/decision/packet
  role attribution.
- Account audit events use the actor's global account role.
- A global Senior account can hold a different role in a matter; the frontend
  displays both when they differ.
- `POST /admin/cases/{case_id}/members` treats `role` as optional. An omitted
  `role` keeps the member's existing matter role and only falls back to the
  global account role when the reviewer is joining the matter for the first
  time. Reactivation therefore never widens a matter role — changing one
  requires sending it explicitly.

## Optimistic concurrency

Existing mutable aggregates carry a `version`:

- a decision request must send the conflict's `expectedVersion` in JSON;
- a report-section revision must send the section's `expectedVersion` in JSON;
  and
- approval must send `expectedVersion` as a query parameter.

Approval is rejected while the section is not report-eligible. A successful
approval is bound to the cited evidence state; later claim, source, quote-
verification, or conflict changes make `approvalState` read as `draft` and
exclude the section from production until Senior counsel reapproves it.

The server locks the current row and returns `409` when actual and expected
versions differ. The frontend then reloads the selected matter, discards the
stale server response, and keeps unsaved form input where applicable so the
reviewer can reconcile it. Requests completed after logout, a new login, or a
matter switch are also discarded client-side through operation epochs.

## Mutation responses

Mutations return the affected entities and newly appended audit events. Typical
shapes are:

- decision → `{conflict, claims, decision, auditEvents}`;
- claim → `{claim, auditEvent}`;
- conflict → `{conflict, claims, auditEvents}`;
- report draft/revision/approval → `{section, auditEvent}`;
- account or membership administration →
  `{reviewer|member, accountAuditEvent}`; and
- packet generation → rendered document, redacted entry summary, hashes,
  filename, packet integrity hash, and audit event.

Admin reviewer DTOs additionally contain `username`, `isActive`, `mfaEnabled`,
`mustChangePassword`, and `passwordChangedAt`. Password hashes and MFA secrets
never leave the server.

Production packet documents and persisted manifests omit generator identity.
The database retains generator attribution for privileged audit use; stored
artifact list/detail DTOs disclose it only to a privilege-cleared matter role.
Internal packets remain fully attributed.

Production manifests retain full section/revision/claim traceability only for
included nonprivileged entries. Every withheld or excluded entry instead uses a
packet-local `WITHHELD-###` or `EXCLUDED-###` section/paragraph reference,
generic title/status/reason, and no original title/text, citations, internal
impact note, `revisionId`, `revisionContentSha256`, or `claimIds`. Withheld
claims are absent from `citedClaimIds` and the evidence appendix. A source-
identity-only privilege log uses opaque references, and a disclosed conflict
involving a redacted counterpart uses the generic summary `Counterpart withheld
under privilege`.

## Integrity model

The runtime role cannot update or delete these append records, and PostgreSQL
triggers reject those operations regardless of application path:

- case `audit_event`;
- global `account_audit_event`;
- `review_decision`;
- `report_section_revision`; and
- `packet_artifact`.

The API verifies chained canonical SHA-256 content for case audit events,
account audit events, review decisions, per-section revisions, and the per-case
packet sequence. Packet verification also recomputes document/manifest/artifact
relationships and stored PDF bytes. Matter verification includes
`externalAnchor`, whose `status` is `matched`, `advanced`, `missing`,
`invalid`, `rollback_detected`, `checkpoint_mismatch`, or `not_configured`.
Production requires external checkpoint configuration; missing or invalid
checkpoints fail complete matter verification. Responses expose roots, counts,
and issues rather than treating a stored boolean as proof.

Internal chains alone are not signatures. Scheduled HMAC-authenticated
checkpoints bind their roots and counts outside the database and are copied to
encrypted immutable storage. They detect database rollback, while key custody,
checkpoint cadence, and the external store remain operational trust boundaries.

## Runtime RLS boundary

In production-style deployments the runtime role is constrained by PostgreSQL
row-level security using a transaction-local reviewer context. RLS catches
application queries that accidentally omit matter predicates. It is not a
boundary against a stolen runtime database credential or arbitrary SQL, because
that caller can set the custom reviewer-context setting. Protect and rotate the
runtime credential and treat SQL-injection prevention as part of isolation.

## Controlled PDF export

Generating a packet also renders it server-side to a paginated PDF and stores
it with the artifact. `GET /cases/{case_id}/packets/{packet_id}/pdf` returns
`application/pdf` with `Cache-Control: no-store`.

The PDF is the produced document, not a convenience rendering: fixed Letter
pages, a running classification header, and the packet ID, `Page N of M`, and
body-hash prefix on every page, so a page separated from the bundle remains
identifiable.

Access matches reading the artifact — an internal packet still requires
privilege clearance — with one addition: a packet whose integrity check fails
returns `403` rather than the bytes. Serving a document the system cannot
vouch for is worse than serving none, because the recipient cannot tell.

`404` with a "regenerate the packet" message means the artifact predates this
feature. Those are deliberately not back-filled: re-rendering one now would
manufacture a document that was never generated, served, or audited.
`hasPdf`, `pdfSha256` and `pdfFilename` on artifact listings say which is which.
The packet-generation response exposes the same fields so development runtimes
that intentionally lack the renderer do not offer a dead PDF action.

**Verification recomputes the hash of the stored bytes and never re-renders.**
Output legitimately depends on the installed renderer and font versions, so
re-rendering would compare a disclosed document against what today's image
would produce. Rendering is byte-reproducible for a fixed image — font
subsetting is pinned with `SOURCE_DATE_EPOCH` — which is what makes a
rendering regression detectable in CI.

## Request and artifact limits

State-changing request bodies are capped by
`ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES` (default `262144`) using both
`Content-Length` and streamed-byte accounting. Packet persistence is bounded by:

- `ATLAS_ARGUS_MAX_PACKET_ENTRIES` (default `250`);
- `ATLAS_ARGUS_MAX_PACKET_DOCUMENT_BYTES` (default `2097152`);
- `ATLAS_ARGUS_MAX_PACKET_MANIFEST_BYTES` (default `524288`); and
- `ATLAS_ARGUS_MAX_PACKET_ARTIFACT_BYTES` (default `3145728`).

Packet-limit failures occur before artifact persistence or audit append. A
reverse proxy should use an equal or lower request limit where practical.
Packet entry count is checked before assembly. CPU-heavy PDF pagination runs
without holding the matter mutation lock; persistence rechecks the matter audit
root and returns `409` for a retry if evidence changed during rendering.

Document upload is the one route exempt from the generic body cap, because its
body carries a base64-encoded document. Its own limit is **derived**, not
separately configured — `ceil(4 × ATLAS_ARGUS_MAX_SOURCE_UPLOAD_BYTES / 3)`
plus 16 KiB of JSON envelope — so a second hand-set value cannot drift from the
first and start rejecting files that are within the configured size. A reverse
proxy fronting `/api/cases/*/sources` must allow at least that much.
Before reading that larger body, middleware authenticates the session, requires
password rotation and MFA to be complete, confirms active membership in the
target matter, and acquires a bounded body-buffer slot. Rejected callers do not
consume the upload body.

Ingestion limits:

- `ATLAS_ARGUS_MAX_SOURCE_UPLOAD_BYTES` (default `26214400`, decoded size);
- `ATLAS_ARGUS_MAX_SOURCE_PAGE_COUNT` (default `200`);
- `ATLAS_ARGUS_SOURCE_OCR_PAGE_TIMEOUT_SECONDS` (default `20`);
- `ATLAS_ARGUS_SOURCE_INGESTION_TIMEOUT_SECONDS` (default `300`);
- `ATLAS_ARGUS_MAX_EXTRACTED_TEXT_BYTES_PER_PAGE` (default `51200`);
- `ATLAS_ARGUS_MAX_TOTAL_EXTRACTED_TEXT_BYTES` (default `25165824`);
- `ATLAS_ARGUS_MAX_CONCURRENT_SOURCE_INGESTIONS` (default `2`, **per process**);
- `ATLAS_ARGUS_SOURCE_CHILD_MEMORY_LIMIT_BYTES` (default `536870912`); and
- `ATLAS_ARGUS_MIN_OCR_CONFIDENCE_FOR_AUTO_VERIFY` (default `70`, percent); and
- `ATLAS_ARGUS_MAX_PACKET_PDF_BYTES` (default `20971520`).
- `ATLAS_ARGUS_MAX_CASE_STATE_ROWS` (default `10000`, per collection);
- `ATLAS_ARGUS_MAX_PACKET_HISTORY_ROWS` (default `1000`); and
- `ATLAS_ARGUS_MAX_CONCURRENT_PACKET_RENDERS` (default `1`, **per process**).

The per-page cap alone does not bound memory — 500 pages × 1 MiB is still half a
gigabyte — so the total is enforced as a running budget; pages past it are
recorded as `failed` with `total_text_budget_exhausted` rather than silently
dropped.

Production refuses to start if `ATLAS_ARGUS_SOURCE_CHILD_MEMORY_LIMIT_BYTES` is
below three times the upload cap: the extraction child must hold the decoded
document, a rendered page raster, and OCR working memory at once, and a
deployment that cannot is better stopped at boot than discovered one failed
upload at a time.

## Compatibility aliases

The older default-matter paths remain for existing clients: `/case`, `/claims`,
`/conflicts`, `/conflicts/{id}/decisions`, `/report-sections...`, `/packets...`,
and `/audit/verify`. They resolve the authenticated reviewer's first active
matter. New clients should use explicit `/cases/{case_id}` paths.

## Development run

```bash
docker compose up -d db
cd server
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m atlas_argus.db.bootstrap
.venv/bin/uvicorn atlas_argus.api.app:app --port 8100
.venv/bin/python -m pytest
```

This bootstrap command seeds the sample matter only in development. Production
bootstrap never loads the sample or resets data and refuses an empty database.
Use the [greenfield production initializer](deploy/greenfield-initialization.md)
once for the first real matter.
