from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select, text

from atlas_argus.db import models as m
from atlas_argus.db.session import SessionLocal
from conftest import login_as


def _insert_other_case(*, with_okafor_membership: bool = False) -> None:
    with SessionLocal() as session, session.begin():
        session.add(
            m.CaseFile(
                id="case-other",
                name="Other Matter",
                aircraft="N000AA",
                accident_date="2026-01-01",
                location="Test Range",
                matter_type="wrongful_death",
                status="open",
                docket_ref="OTHER-1",
            )
        )
        session.flush()
        if with_okafor_membership:
            session.add(
                m.CaseMember(
                    id="cm-case-other-rev-okafor",
                    case_id="case-other",
                    reviewer_id="rev-okafor",
                    role="Senior Aviation Counsel",
                    is_active=True,
                )
            )
        session.add(
            m.SourceDocument(
                id="src-other",
                case_id="case-other",
                title="Other public record",
                type="official_report",
                origin="NTSB",
                custodian="Records",
                docket_ref="OTHER-1",
                ingested_at=datetime.now(UTC),
                sha256="0" * 64,
                privilege_status="public",
                custody=[],
            )
        )


def _new_claim_body(source_id: str = "src-other") -> dict:
    return {
        "sourceDocumentId": source_id,
        "text": "A sufficiently specific claim from another case.",
        "pageRef": "p. 1",
        "quote": "A sufficiently specific quote from another case.",
        "confidence": 0.8,
        "evidenceQuality": "primary",
    }


def _set_matter_role(reviewer_id: str, role: str) -> None:
    with SessionLocal() as session, session.begin():
        session.execute(
            text(
                """
                UPDATE case_member
                   SET role = :role
                 WHERE case_id = 'case-3407'
                   AND reviewer_id = :reviewer_id
                """
            ),
            {"reviewer_id": reviewer_id, "role": role},
        )


def test_case_state_exposes_active_membership(client):
    login_as(client, "mokafor")

    response = client.get("/api/case")

    assert response.status_code == 200
    assert response.json()["caseMembership"] == {
        "caseId": "case-3407",
        "reviewerId": "rev-okafor",
        "role": "Senior Aviation Counsel",
        "isActive": True,
    }


def test_explicit_case_state_exposes_active_membership(client):
    login_as(client, "mokafor")

    response = client.get("/api/cases/case-3407")

    assert response.status_code == 200
    assert response.json()["caseFile"]["id"] == "case-3407"
    assert response.json()["caseMembership"]["reviewerId"] == "rev-okafor"


def test_case_list_returns_active_memberships(client):
    login_as(client, "mokafor")
    _insert_other_case(with_okafor_membership=True)

    response = client.get("/api/cases")

    assert response.status_code == 200
    case_ids = {item["caseFile"]["id"] for item in response.json()["cases"]}
    assert case_ids == {"case-3407", "case-other"}


def test_case_state_requires_active_case_membership(client):
    login_as(client, "pnatarajan")
    with SessionLocal() as session, session.begin():
        session.execute(
            text(
                """
                UPDATE case_member
                   SET is_active = false
                 WHERE case_id = 'case-3407'
                   AND reviewer_id = 'rev-natarajan'
                """
            )
        )

    response = client.get("/api/case")

    assert response.status_code == 403
    assert response.json()["detail"] == "Reviewer is not an active member of any case."


def test_explicit_case_state_requires_membership_in_requested_case(client):
    login_as(client, "mokafor")
    _insert_other_case()

    response = client.get("/api/cases/case-other")

    assert response.status_code == 403
    assert response.json()["detail"] == "Reviewer is not an active member of this case."


def test_create_claim_requires_membership_in_source_case(client):
    """A source in a matter the reviewer is not on must answer exactly like a
    source identifier that was never ingested, so the unscoped claim route
    cannot be used to map another matter's document set."""
    login_as(client, "mokafor")
    _insert_other_case()

    response = client.post("/api/claims", json=_new_claim_body())
    missing = client.post("/api/claims", json=_new_claim_body("src-never-ingested"))

    assert response.status_code == 404
    assert missing.status_code == 404
    assert response.json()["detail"] == missing.json()["detail"] == "Source document not found."


def test_scoped_claim_route_rejects_source_from_another_case(client):
    login_as(client, "mokafor")
    _insert_other_case()

    response = client.post("/api/cases/case-3407/claims", json=_new_claim_body())

    assert response.status_code == 404
    assert response.json()["detail"] == "Source document not found in this case."


def test_scoped_decision_route_rejects_conflict_from_another_case(client):
    login_as(client, "mokafor")
    _insert_other_case(with_okafor_membership=True)

    response = client.post(
        "/api/cases/case-other/conflicts/cf-1/decisions",
        json={
            "decisionType": "preserve_both",
            "reasoning": "This reasoning note is definitely long enough to satisfy validation.",
            "expectedVersion": 1,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Conflict not found in this case."


def _flag_conflict(client, claim_a_id: str, claim_b_id: str):
    return client.post(
        "/api/conflicts",
        json={
            "claimAId": claim_a_id,
            "claimBId": claim_b_id,
            "conflictType": "factual",
            "severity": "low",
            "summary": "A sufficiently long conflict summary for validation.",
        },
    )


def test_flag_conflict_does_not_leak_privilege_status_for_unmembered_case(client):
    """A reviewer who isn't an active member of a claim's case must get the
    same rejection whether or not that claim happens to be privileged —
    otherwise the distinct error messages become a cross-case oracle for
    which claims are privilege-withheld. The rejection must also match a
    claim that does not exist at all, or the ID space itself is enumerable."""
    login_as(client, "pnatarajan")
    with SessionLocal() as session, session.begin():
        session.execute(
            text(
                """
                UPDATE case_member
                   SET is_active = false
                 WHERE case_id = 'case-3407'
                   AND reviewer_id = 'rev-natarajan'
                """
            )
        )

    def _flag(claim_a_id: str, claim_b_id: str) -> dict:
        response = _flag_conflict(client, claim_a_id, claim_b_id)
        assert response.status_code == 422, response.text
        return response.json()

    # clm-a5 is privilege-withheld (attorney_client); clm-a1 is public.
    privileged = _flag("clm-a5", "clm-b5")
    public = _flag("clm-a1", "clm-b1")
    missing = _flag("clm-does-not-exist", "clm-also-missing")
    assert privileged["detail"] == "Both claims must exist."
    assert public["detail"] == "Both claims must exist."
    assert missing["detail"] == "Both claims must exist."


def test_flag_conflict_does_not_leak_privilege_status_inside_own_case(client):
    """Claim identifiers come from one global sequence, so a non-cleared member
    must not be able to tell a privilege-withheld claim in their own matter
    apart from an identifier that was never issued."""
    login_as(client, "pnatarajan")  # Claims Reviewer: no privilege clearance

    # clm-a5 is attorney-client privileged; clm-a1 is public.
    withheld = _flag_conflict(client, "clm-a1", "clm-a5")
    missing = _flag_conflict(client, "clm-a1", "clm-never-issued")

    assert withheld.status_code == 422, withheld.text
    assert missing.status_code == 422, missing.text
    assert withheld.json()["detail"] == missing.json()["detail"] == "Both claims must exist."


def test_public_section_approval_state_is_role_independent(client):
    login_as(client, "mokafor")
    counsel_state = client.get("/api/cases/case-3407").json()
    counsel_section = next(
        section
        for section in counsel_state["reportSections"]
        if section["id"] == "rpt-acft"
    )
    assert counsel_section["approvalState"] == "approved"

    assert client.post("/api/auth/logout").status_code == 200
    login_as(client, "pnatarajan")
    reviewer_state = client.get("/api/cases/case-3407").json()
    reviewer_section = next(
        section
        for section in reviewer_state["reportSections"]
        if section["id"] == "rpt-acft"
    )
    assert reviewer_section["approvalState"] == "approved"


def test_section_citation_does_not_leak_privilege_status(client):
    """Citing a privilege-withheld claim must fail exactly like citing a claim
    identifier that does not exist."""
    login_as(client, "pnatarajan")

    def _draft(paragraph_ref: str, claim_id: str):
        return client.post(
            "/api/report-sections",
            json={
                "title": "Privilege probe",
                "paragraphRef": paragraph_ref,
                "text": "This draft cites a single claim to probe the validation message.",
                "claimIds": [claim_id],
            },
        )

    withheld = _draft("PRIV-PROBE-1", "clm-a5")
    missing = _draft("PRIV-PROBE-2", "clm-never-issued")

    assert withheld.status_code == 422, withheld.text
    assert missing.status_code == 422, missing.text
    assert withheld.json()["detail"] == "Cited claim not found: clm-a5."
    assert missing.json()["detail"] == "Cited claim not found: clm-never-issued."


def test_noncleared_bootstrap_hides_privileged_conflict_and_section_history(client):
    """A public claim and currently-public section must not carry dangling
    identifiers or audit prose from privilege-hidden historical relationships."""
    login_as(client, "mokafor")
    flagged = client.post(
        "/api/conflicts",
        json={
            "claimAId": "clm-a4",  # public
            "claimBId": "clm-a5",  # attorney-client privileged
            "conflictType": "factual",
            "severity": "high",
            "summary": "SENTINEL privileged counterpart conflict summary.",
        },
    )
    assert flagged.status_code == 201, flagged.text
    hidden_conflict_id = flagged.json()["conflict"]["id"]

    drafted = client.post(
        "/api/report-sections",
        json={
            "title": "Historical privilege probe",
            "paragraphRef": "HIST-PRIV-1",
            "text": "This initial draft cites material that is later removed from the section.",
            "claimIds": ["clm-a5"],
        },
    )
    assert drafted.status_code == 201, drafted.text
    section = drafted.json()["section"]
    revised = client.put(
        f"/api/report-sections/{section['id']}",
        json={
            "title": "Current public probe",
            "paragraphRef": "HIST-PUB-1",
            "text": "The current section now cites only public evidence and remains visible.",
            "claimIds": ["clm-a4"],
            "expectedVersion": section["version"],
        },
    )
    assert revised.status_code == 200, revised.text

    login_as(client, "pnatarajan")
    response = client.get("/api/case")
    assert response.status_code == 200
    state = response.json()

    public_claim = next(claim for claim in state["claims"] if claim["id"] == "clm-a4")
    assert hidden_conflict_id not in public_claim["relatedConflictIds"]
    assert hidden_conflict_id not in state["conflictOrder"]
    assert all(conflict["id"] != hidden_conflict_id for conflict in state["conflicts"])

    # The current section remains usable in the bootstrap, but every event for
    # it is hidden because an immutable older revision cites privileged data.
    assert any(row["id"] == section["id"] for row in state["reportSections"])
    assert all(
        not (event["subjectType"] == "report_section" and event["subjectId"] == section["id"])
        for event in state["auditEvents"]
    )
    assert all(
        not (
            event["subjectType"] == "claim"
            and event["subjectId"] == "clm-a4"
            and event["action"] == "claim status updated"
        )
        for event in state["auditEvents"]
    )

    serialized = json.dumps(state, sort_keys=True)
    assert hidden_conflict_id not in serialized
    assert hidden_conflict_id.upper() not in serialized
    assert "SENTINEL privileged counterpart conflict summary" not in serialized


def test_admin_can_manage_case_membership(client):
    login_as(client, "mokafor")
    _insert_other_case(with_okafor_membership=True)

    members = client.get("/api/admin/cases/case-other/members")
    assert members.status_code == 200
    assert [member["reviewerId"] for member in members.json()["members"]] == ["rev-okafor"]

    added = client.post(
        "/api/admin/cases/case-other/members",
        json={"reviewerId": "rev-natarajan"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["member"]["reviewerId"] == "rev-natarajan"
    assert added.json()["member"]["isActive"] is True
    assert added.json()["accountAuditEvent"]["action"] == "case member added"

    client.post("/api/auth/logout")
    login_as(client, "pnatarajan")
    visible = client.get("/api/cases/case-other")
    assert visible.status_code == 200
    assert visible.json()["caseMembership"]["reviewerId"] == "rev-natarajan"

    client.post("/api/auth/logout")
    login_as(client, "mokafor")
    deactivated = client.post("/api/admin/cases/case-other/members/rev-natarajan/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["member"]["isActive"] is False

    client.post("/api/auth/logout")
    login_as(client, "pnatarajan")
    blocked = client.get("/api/cases/case-other")
    assert blocked.status_code == 403

    client.post("/api/auth/logout")
    login_as(client, "mokafor")
    reactivated = client.post("/api/admin/cases/case-other/members/rev-natarajan/reactivate")
    assert reactivated.status_code == 200
    assert reactivated.json()["member"]["isActive"] is True


def test_admin_cannot_remove_own_case_membership(client):
    login_as(client, "mokafor")
    response = client.post("/api/admin/cases/case-3407/members/rev-okafor/deactivate")

    assert response.status_code == 422
    assert "cannot remove your own access" in response.json()["detail"]


def test_admin_cannot_downgrade_the_last_case_senior(client):
    login_as(client, "mokafor")
    response = client.post(
        "/api/admin/cases/case-3407/members",
        json={"reviewerId": "rev-okafor", "role": "Claims Reviewer"},
    )

    assert response.status_code == 422
    assert "retain at least one active Senior" in response.json()["detail"]


def test_global_deactivation_cannot_strand_a_matter_without_senior(client):
    with SessionLocal() as session, session.begin():
        session.execute(
            text(
                "UPDATE reviewer SET role = 'Senior Aviation Counsel' "
                "WHERE id = 'rev-natarajan'"
            )
        )

    login_as(client, "pnatarajan")
    response = client.post("/api/admin/reviewers/rev-okafor/deactivate")

    assert response.status_code == 422
    assert "case-3407" in response.json()["detail"]
    assert "no active Senior" in response.json()["detail"]


def test_non_counsel_cannot_manage_case_membership(client):
    login_as(client, "pnatarajan")
    response = client.get("/api/admin/cases/case-3407/members")

    assert response.status_code == 403


def test_reactivation_preserves_a_narrowed_matter_role(client):
    """A member walled off from a matter's privileged evidence must not regain
    it by being deactivated and reactivated. The global Reviewer.role governs
    the account plane only — it must never become the matter role by default."""
    with SessionLocal() as session, session.begin():
        # A global account administrator, deliberately restricted on this matter.
        session.execute(
            text("UPDATE reviewer SET role = 'Senior Aviation Counsel' WHERE id = 'rev-natarajan'")
        )
    _set_matter_role("rev-natarajan", "Claims Reviewer")

    login_as(client, "pnatarajan")
    before = client.get("/api/case")
    assert before.status_code == 200
    assert "src-memo" not in {source["id"] for source in before.json()["sources"]}

    client.post("/api/auth/logout")
    login_as(client, "mokafor")
    off = client.post("/api/admin/cases/case-3407/members/rev-natarajan/deactivate")
    assert off.status_code == 200, off.text
    on = client.post("/api/admin/cases/case-3407/members/rev-natarajan/reactivate")
    assert on.status_code == 200, on.text
    assert on.json()["member"]["role"] == "Claims Reviewer"

    client.post("/api/auth/logout")
    login_as(client, "pnatarajan")
    after = client.get("/api/case")
    assert after.status_code == 200
    body = after.json()
    assert body["caseMembership"]["role"] == "Claims Reviewer"
    assert "src-memo" not in {source["id"] for source in body["sources"]}
    assert "clm-a5" not in {claim["id"] for claim in body["claims"]}


def test_explicit_role_still_changes_a_matter_role(client):
    """Preserving an omitted role must not make matter roles unchangeable."""
    login_as(client, "mokafor")
    _set_matter_role("rev-natarajan", "Claims Reviewer")

    promoted = client.post(
        "/api/admin/cases/case-3407/members",
        json={"reviewerId": "rev-natarajan", "role": "Safety Investigator"},
    )

    assert promoted.status_code == 201, promoted.text
    assert promoted.json()["member"]["role"] == "Safety Investigator"


def test_global_senior_downgraded_on_matter_loses_case_powers(client):
    login_as(client, "mokafor")
    internal = client.post("/api/packets", json={"packetType": "internal"})
    assert internal.status_code == 201
    packet_id = internal.json()["packetId"]

    _set_matter_role("rev-natarajan", "Senior Aviation Counsel")
    _set_matter_role("rev-okafor", "Claims Reviewer")

    state = client.get("/api/case")
    assert state.status_code == 200
    body = state.json()
    assert body["caseMembership"]["role"] == "Claims Reviewer"
    assert "src-memo" not in {source["id"] for source in body["sources"]}
    assert "clm-a5" not in {claim["id"] for claim in body["claims"]}
    okafor = next(row for row in body["reviewers"] if row["id"] == "rev-okafor")
    assert okafor["role"] == "Claims Reviewer"

    critical = client.post(
        "/api/conflicts/cf-1/decisions",
        json={
            "decisionType": "accept_claim_a",
            "reasoning": "The record supports accepting the first claim as controlling.",
            "expectedVersion": 1,
        },
    )
    assert critical.status_code == 403

    approval = client.post(
        "/api/report-sections/rpt-acft/approve",
        params={"expectedVersion": 1},
    )
    assert approval.status_code == 403
    assert client.post("/api/packets", json={"packetType": "production"}).status_code == 403
    assert client.get(f"/api/packets/{packet_id}").status_code == 403

    # Reviewer.role still governs the global account plane.
    assert client.get("/api/admin/reviewers").status_code == 200


def test_global_claims_promoted_on_matter_gains_case_powers_only(client):
    _set_matter_role("rev-natarajan", "Senior Aviation Counsel")
    login_as(client, "pnatarajan")

    state = client.get("/api/case")
    assert state.status_code == 200
    assert state.json()["caseMembership"]["role"] == "Senior Aviation Counsel"
    assert "src-memo" in {source["id"] for source in state.json()["sources"]}
    assert "clm-a5" in {claim["id"] for claim in state.json()["claims"]}
    assert client.get("/api/admin/reviewers").status_code == 403

    decision = client.post(
        "/api/conflicts/cf-1/decisions",
        json={
            "decisionType": "accept_claim_a",
            "reasoning": "The flight-recorder evidence is controlling for this finding.",
            "expectedVersion": 1,
        },
    )
    assert decision.status_code == 201, decision.text
    assert decision.json()["decision"]["reviewerRole"] == "Senior Aviation Counsel"
    assert all(
        event["actorRole"] == "Senior Aviation Counsel"
        for event in decision.json()["auditEvents"]
    )

    drafted = client.post(
        "/api/report-sections",
        json={
            "title": "Matter Role Finding",
            "paragraphRef": "ROLE-1",
            "text": "This complete paragraph records the controlling matter-role finding.",
            "claimIds": ["clm-a4"],
        },
    )
    assert drafted.status_code == 201, drafted.text
    section = drafted.json()["section"]
    assert drafted.json()["auditEvent"]["actorRole"] == "Senior Aviation Counsel"

    approved = client.post(
        f"/api/report-sections/{section['id']}/approve",
        params={"expectedVersion": section["version"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["auditEvent"]["actorRole"] == "Senior Aviation Counsel"

    packet = client.post("/api/packets", json={"packetType": "production"})
    assert packet.status_code == 201, packet.text
    assert packet.json()["generatedByRole"] == "Senior Aviation Counsel"
    assert packet.json()["auditEvent"]["actorRole"] == "Senior Aviation Counsel"

    with SessionLocal() as session:
        revisions = list(
            session.execute(
                select(m.ReportSectionRevision)
                .where(m.ReportSectionRevision.section_id == section["id"])
                .order_by(m.ReportSectionRevision.created_at)
            ).scalars()
        )
        artifact = session.get(m.PacketArtifact, packet.json()["packetId"])
    assert revisions
    assert all(row.author_role == "Senior Aviation Counsel" for row in revisions)
    assert artifact is not None
    assert artifact.generated_by_role == "Senior Aviation Counsel"
