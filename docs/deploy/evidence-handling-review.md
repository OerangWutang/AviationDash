# Evidence-handling review

This is the legal/evidence-governance sign-off checklist for a real deployment.
It describes the controls the application implements today and the questions that
remain deployment-specific. It is not a representation that Atlas Argus itself
provides a legally sufficient chain of custody, records-retention program, or
production protocol.

## Implemented review model

- Privilege classifications are `public`, `confidential`, `attorney_client`,
  `work_product`, and `restricted`. The last three are withholding statuses.
- A reviewer's global account role and per-matter membership role are separate.
  The global `Senior Aviation Counsel` role controls administrative functions;
  the per-matter role controls evidence and legal-review actions in that matter.
- Only an active per-matter `Senior Aviation Counsel` membership has privilege
  clearance. Other active members receive filtered matter data: withheld source
  content and claims are removed, along with dependent conflicts, decisions,
  report sections, and audit events.
- Review decisions are immutable, ordered records. A conflict's optimistic
  version prevents stale decisions; a new decision records the next state
  transition while every earlier decision remains in history.
- Report-section revisions are immutable. Production-mode packet generation
  accepts only the active approved revision of each included section.
- Preserved conflicts remain part of the review record. The application does not
  infer that discrediting one source proves the opposing claim.

Confirm with counsel:

- Does the privilege taxonomy match the organization's operating model and
  applicable jurisdiction?
- Are matter roles, clearance rules, and the Senior approval gates appropriate?
- Are the conflict and decision states, reason fields, and supersession model
  sufficient for the intended review process?
- Does the production privilege log disclose enough to support production while
  avoiding privileged substance?
- Are internal packet banners and labels adequate to prevent accidental
  production?

## Provenance, audit, and integrity

Source records include origin, custodian, optional docket reference, ingestion
timestamp, content SHA-256, privilege status, and custody events. Extracted
claims add a page/section reference and verbatim quote; report sections have a
paragraph reference and cited-claim list. Case audit events, account audit
events, review decisions, report-section revisions, and packet-artifact metadata
are protected by append-only database controls. Integrity verification covers
the case audit chain and its related packet, report-revision, and review-decision
chains; a separate administrator-only check covers the account audit chain.

These controls use SHA-256 hash chains stored in the same operational database.
They detect many ordinary edits and broken relationships, but they are not
digital signatures, trusted timestamps, external notarization, immutable object
storage, or a WORM archive. A database owner or host administrator remains in the
trust boundary. If independent proof is required, add externally anchored,
signed evidence and a controlled retention system.

Confirm with evidence operations and counsel:

- Are the source and custody fields sufficient for every evidence class in
  scope, including native files and physical evidence?
- What is the authoritative evidence repository, and how are originals tied to
  Atlas Argus document references?
- Are append-only application records sufficient for the intended use, or must
  hashes/manifests be signed or anchored outside the application database?
- What retention, legal-hold, deletion, export, and account-deactivation rules
  apply?
- Who investigates a failed integrity check, and what quarantine/escalation
  procedure applies?

## Packet review

Internal packets include reviewer attribution and privileged working material.
They require an active matter-level Senior membership and MFA verification for
the current session both when generated and when reopened.

Production packets include substantive report text only from eligible, approved
active revisions and use a privilege log rather than withheld substance. The
production document and its
persisted manifest omit generator identity. Production artifact list/detail
responses also hide generator identity from reviewers without matter-level
privilege clearance; cleared Senior members may see that administrative
attribution. All server packet generation, listing, retrieval, and verification
operations require MFA verification for the current session.

Withheld and excluded manifest entries use packet-local opaque references and
generic metadata rather than internal section, revision, or claim identifiers.
Privileged claims are excluded from cited-claim and evidence-appendix output;
the privilege log retains only source-identity fields. If a disclosed conflict
has a privileged counterpart, the production document states only that the
counterpart was withheld.

The packet manifest and stored metadata support integrity checks, and the UI
blocks opening, downloading, or printing an artifact whose verification fails.
Packet hashes are still subject to the trust limitations above. Browser-created
local previews are not server artifacts and have no server provenance record.

Confirm before production use:

- Is HTML plus browser print-to-PDF acceptable, or is a controlled PDF renderer
  and archival format required?
- Do the production document, manifest, artifact metadata, and privilege log
  expose exactly the fields approved by counsel?
- Is server-side production sanitization sufficient for the data model in scope,
  including reviewer identity and internal rationale?
- Must downloaded artifacts be digitally signed, timestamped, watermarked, or
  deposited in an external evidence system?
- Are the local preview and server artifact visibly distinct enough for users?

## Access and operational governance

Passwords, active matter membership, forced password rotation, MFA, and
role checks form the interactive access boundary. High-risk legal, packet, and
administrative operations require TOTP verification for the current session.
An MFA-verified global Senior can reset another reviewer's enabled factor. The
operation cannot target the actor, revokes every target session, clears the
target MFA throttle, appends an account-audit event, and requires re-enrollment.
There is no self-service recovery or backup code. At least two independently
controlled global Senior accounts and an approved identity-verification ceremony
are therefore required before launch.

Confirm with security and operations:

- Who approves account creation, role changes, matter membership, deactivation,
  credential recovery, and emergency access?
- How are access reviews performed and evidenced?
- What is the approved MFA recovery ceremony, and how does it preserve an audit
  trail without weakening the second factor?
- Are incident response, backup custody, restore testing, and breach-notification
  procedures documented and rehearsed?
- Is the deployment restricted to the approved matters, users, jurisdictions,
  and evidence classes?

## Required sign-off record

- Reviewing attorney:
- Security/privacy stakeholder:
- Evidence operations stakeholder:
- Operations owner:
- Date:
- Approved deployment scope:
- Approved retention and legal-hold policy:
- Approved external integrity/archival controls:
- Required changes before launch:
