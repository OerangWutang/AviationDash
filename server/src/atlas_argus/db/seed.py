"""Sample-case seeding.

``seed_data.json`` is generated from the frontend's ``src/data/sampleCase.ts``
by ``npx tsx scripts/export-seed.ts`` — the TS module stays the single source
of truth; never edit the JSON by hand.

Denormalized frontend fields (``relatedConflictIds`` on claims,
``decisionIds`` on conflicts) are intentionally NOT stored — the API derives
them from the conflict and decision tables.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..auth import hash_password
from ..config import is_production, seed_password
from ..integrity import (
    audit_event_content,
    chain_sha256,
    report_section_revision_content,
    review_decision_content,
    revision_content_sha256,
)
from . import models as m

SEED_PATH = Path(__file__).parent / "seed_data.json"

#: Demo usernames for the seeded reviewers (documented in the README and on
#: the login screen — this is sample-case tooling, not a production
#: directory). The development password comes from ATLAS_ARGUS_SEED_PASSWORD;
#: sample-case loading itself is refused in ATLAS_ARGUS_ENV=production.
DEMO_USERNAMES = {
    "rev-okafor": "mokafor",
    "rev-reeves": "dreeves",
    "rev-natarajan": "pnatarajan",
    "rev-lindqvist": "tlindqvist",
}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def load_seed() -> dict:
    return json.loads(SEED_PATH.read_text())


def seed_database(session: Session) -> None:
    if is_production():
        raise RuntimeError(
            "Refusing to load the public sample case in production; use the "
            "approved greenfield initializer or an import workflow."
        )
    data = load_seed()

    case = data["caseFile"]
    session.add(
        m.CaseFile(
            id=case["id"],
            name=case["name"],
            aircraft=case["aircraft"],
            accident_date=case["accidentDate"],
            location=case["location"],
            matter_type=case["matterType"],
            status=case["status"],
            docket_ref=case["docketRef"],
        )
    )

    # Reviewers (and their auth sessions) survive a case reset, so insert
    # only the ones that don't already exist.
    existing_reviewers = set(session.execute(select(m.Reviewer.id)).scalars())
    missing = [r for r in data["reviewers"] if r["id"] not in existing_reviewers]
    demo_hash = hash_password(seed_password()) if missing else ""
    for reviewer in missing:
        session.add(
            m.Reviewer(
                id=reviewer["id"],
                name=reviewer["name"],
                role=reviewer["role"],
                username=DEMO_USERNAMES.get(reviewer["id"], reviewer["id"]),
                password_hash=demo_hash,
                # Production: the shared initial password must be personalized
                # on first sign-in. Dev/demo stays frictionless.
                must_change_password=is_production(),
                is_active=True,
            )
        )
    # No relationship() mappings — flush per entity group so FK targets exist
    # before dependents are inserted.
    session.flush()

    existing_memberships = set(
        session.execute(
            select(m.CaseMember.case_id, m.CaseMember.reviewer_id)
        ).all()
    )
    for reviewer in data["reviewers"]:
        key = (case["id"], reviewer["id"])
        if key in existing_memberships:
            continue
        session.add(
            m.CaseMember(
                id=f"cm-{case['id']}-{reviewer['id']}",
                case_id=case["id"],
                reviewer_id=reviewer["id"],
                role=reviewer["role"],
                is_active=True,
            )
        )
    session.flush()

    for source in data["sources"]:
        session.add(
            m.SourceDocument(
                id=source["id"],
                case_id=source["caseId"],
                title=source["title"],
                type=source["type"],
                origin=source["origin"],
                custodian=source["custodian"],
                docket_ref=source["docketRef"],
                ingested_at=parse_ts(source["ingestedAt"]),
                sha256=source["sha256"],
                privilege_status=source["privilegeStatus"],
                custody=source["custody"],
            )
        )
    session.flush()

    for claim in data["claims"]:
        session.add(
            m.Claim(
                id=claim["id"],
                case_id=claim["caseId"],
                text=claim["text"],
                source_document_id=claim["sourceDocumentId"],
                page_ref=claim["pageRef"],
                quote=claim["quote"],
                confidence=claim["confidence"],
                evidence_quality=claim["evidenceQuality"],
                status=claim["status"],
                privilege_status=claim["privilegeStatus"],
                report_eligibility=claim["reportEligibility"],
                reviewer=claim["reviewer"],
                updated_at=parse_ts(claim["updatedAt"]),
            )
        )
    session.flush()

    for conflict in data["conflicts"]:
        session.add(
            m.Conflict(
                id=conflict["id"],
                case_id=conflict["caseId"],
                claim_a_id=conflict["claimAId"],
                claim_b_id=conflict["claimBId"],
                conflict_type=conflict["conflictType"],
                severity=conflict["severity"],
                status=conflict["status"],
                summary=conflict["summary"],
                created_at=parse_ts(conflict["createdAt"]),
                version=1,
                claim_a_disposition=conflict["claimADisposition"],
                claim_b_disposition=conflict["claimBDisposition"],
            )
        )
    session.flush()

    case_by_conflict_id = {
        conflict["id"]: conflict["caseId"] for conflict in data["conflicts"]
    }
    previous_decision_hash_by_case: dict[str, str | None] = {}
    for decision in sorted(data["decisions"], key=lambda row: row["createdAt"]):
        decision_case_id = case_by_conflict_id[decision["conflictId"]]
        previous_hash = previous_decision_hash_by_case.get(decision_case_id)
        review_decision = m.ReviewDecision(
            id=decision["id"],
            conflict_id=decision["conflictId"],
            decision_type=decision["decisionType"],
            selected_claim_id=decision["selectedClaimId"],
            reasoning=decision["reasoning"],
            reviewer_name=decision["reviewerName"],
            reviewer_role=decision["reviewerRole"],
            created_at=parse_ts(decision["createdAt"]),
            previous_status=decision["previousStatus"],
            new_status=decision["newStatus"],
            report_impact=decision["reportImpact"],
            previous_integrity_hash=previous_hash,
            integrity_hash="",
        )
        review_decision.integrity_hash = chain_sha256(
            previous_hash=previous_hash,
            content=review_decision_content(review_decision),
        )
        previous_decision_hash_by_case[decision_case_id] = (
            review_decision.integrity_hash
        )
        session.add(review_decision)
        # Preserve deterministic identity order even if SQLAlchemy changes
        # insert batching behavior.
        session.flush()

    # Chronological insertion keeps `seq` aligned with event time.
    previous_audit_hash_by_case: dict[str, str | None] = {}
    for event in sorted(data["auditEvents"], key=lambda e: e["at"]):
        actor = next(
            (r for r in data["reviewers"] if r["name"] == event["actor"]),
            None,
        )
        previous_hash = previous_audit_hash_by_case.get(event["caseId"])
        audit_event = m.AuditEvent(
            id=event["id"],
            case_id=event["caseId"],
            at=parse_ts(event["at"]),
            actor_reviewer_id=actor["id"] if actor is not None else None,
            actor=event["actor"],
            actor_role=event["actorRole"],
            action=event["action"],
            subject_type=event["subjectType"],
            subject_id=event["subjectId"],
            previous_status=event["previousStatus"],
            new_status=event["newStatus"],
            detail=event["detail"],
            report_eligibility_changed=event["reportEligibilityChanged"],
            previous_integrity_hash=previous_hash,
            integrity_hash="",
        )
        audit_event.integrity_hash = chain_sha256(
            previous_hash=previous_hash,
            content=audit_event_content(audit_event),
        )
        previous_audit_hash_by_case[event["caseId"]] = audit_event.integrity_hash
        session.add(audit_event)

    seeded_revision_time = parse_ts("2026-07-09T21:00:00Z")
    for position, section in enumerate(data["reportSections"]):
        revision_id = f"{section['id']}-rev-001"
        report_section = m.ReportSection(
            id=section["id"],
            case_id=section["caseId"],
            title=section["title"],
            paragraph_ref=section["paragraphRef"],
            text=section["text"],
            claim_ids=section["claimIds"],
            position=position,
            active_revision_id=None,
            version=1,
        )
        session.add(report_section)
        session.flush()
        seed_revision = m.ReportSectionRevision(
            id=revision_id,
            section_id=section["id"],
            case_id=section["caseId"],
            title=section["title"],
            paragraph_ref=section["paragraphRef"],
            text=section["text"],
            claim_ids=section["claimIds"],
            author_reviewer_id="rev-okafor",
            author="Miriam Okafor",
            author_role="Senior Aviation Counsel",
            created_at=seeded_revision_time,
            revision_reason="Seeded approved sample section.",
            parent_revision_id=None,
            approval_state="approved",
            content_sha256=revision_content_sha256(
                case_id=section["caseId"],
                section_id=section["id"],
                title=section["title"],
                paragraph_ref=section["paragraphRef"],
                text=section["text"],
                claim_ids=section["claimIds"],
            ),
            previous_integrity_hash=None,
            integrity_hash="",
        )
        seed_revision.integrity_hash = chain_sha256(
            previous_hash=None,
            content=report_section_revision_content(seed_revision),
        )
        session.add(seed_revision)
        session.flush()
        report_section.active_revision_id = revision_id
    session.flush()

    # Human-readable ID sequences continue where the seed leaves off
    # (frontend parity: next claim is clm-11, next conflict cf-6, next
    # section rpt-7).
    session.execute(text("SELECT setval('claim_id_seq', :n)"), {"n": len(data["claims"])})
    session.execute(
        text("SELECT setval('conflict_id_seq', :n)"), {"n": len(data["conflicts"])}
    )
    session.execute(
        text("SELECT setval('section_id_seq', :n)"), {"n": len(data["reportSections"])}
    )


#: Case-record tables only — reviewers and auth sessions survive a reset so
#: the person resetting stays signed in.
CASE_TABLES = (
    "packet_artifact",
    "report_section_revision",
    "report_section",
    "audit_event",
    "review_decision",
    "conflict",
    "claim",
    "source_document",
    "case_member",
    "case_file",
)


def reset_and_seed(session: Session) -> None:
    """Discard the working record and restore the sample case.

    TRUNCATE bypasses the append-only row trigger by design — resetting the
    demo world is a meta-action, not evidence destruction.
    """
    session.execute(text(f"TRUNCATE {', '.join(CASE_TABLES)} CASCADE"))
    seed_database(session)


def is_seeded(session: Session) -> bool:
    return session.execute(select(m.CaseFile.id).limit(1)).first() is not None
