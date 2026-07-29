"""HTTP-level doctrine: the story a reviewer actually drives, against real
Postgres. Identity comes from the session cookie — demo logins: mokafor
(Senior Aviation Counsel), dreeves (Reconstruction Expert), pnatarajan
(Claims Reviewer)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from conftest import login_as

REASONING = "This reasoning note is definitely long enough to satisfy validation."


def _decide(client, conflict_id, decision_type, **extra):
    if "expectedVersion" not in extra:
        state = client.get("/api/case").json()
        conflict = next(
            (row for row in state.get("conflicts", []) if row["id"] == conflict_id),
            None,
        )
        extra["expectedVersion"] = conflict["version"] if conflict is not None else 1
    return client.post(
        f"/api/conflicts/{conflict_id}/decisions",
        json={"decisionType": decision_type, "reasoning": REASONING, **extra},
    )


class TestCaseState:
    def test_health_is_open(self, client):
        assert client.get("/api/health").json() == {"status": "ok", "database": "ok"}

    def test_bootstrap_shape_and_derived_fields(self, client):
        login_as(client)
        state = client.get("/api/case").json()
        assert state["caseFile"]["name"] == "Colgan Air Flight 3407"
        assert state["conflictOrder"] == ["cf-1", "cf-2", "cf-3", "cf-4", "cf-5"]
        claims = {c["id"]: c for c in state["claims"]}
        assert claims["clm-a1"]["relatedConflictIds"] == ["cf-1"]
        assert claims["clm-a4"]["reportEligibility"] == "eligible"
        conflicts = {c["id"]: c for c in state["conflicts"]}
        assert conflicts["cf-4"]["decisionIds"] == ["dec-cf4-1"]
        # Audit newest-first; timestamps ISO-8601 Zulu.
        assert state["auditEvents"][0]["id"] == "aud-016"
        assert state["auditEvents"][0]["at"].endswith("Z")
        assert [s["paragraphRef"] for s in state["reportSections"]][:2] == ["PC-1", "SOE-3"]


class TestDecisions:
    def test_expected_version_is_required(self, client):
        login_as(client)
        response = client.post(
            "/api/conflicts/cf-1/decisions",
            json={"decisionType": "escalate", "reasoning": REASONING},
        )
        assert response.status_code == 422
        assert any(error["loc"][-1] == "expectedVersion" for error in response.json()["detail"])

    def test_preserve_both_full_cycle(self, client):
        login_as(client)
        response = _decide(client, "cf-1", "preserve_both")
        assert response.status_code == 201
        body = response.json()
        assert body["conflict"]["status"] == "preserved_both"
        for claim in body["claims"]:
            assert claim["status"] == "preserved"
            assert claim["reportEligibility"] == "eligible"
        assert body["decision"]["previousStatus"] == "unresolved"
        assert body["decision"]["reviewerName"] == "Miriam Okafor"
        assert len(body["auditEvents"]) == 3
        assert body["auditEvents"][0]["detail"] == REASONING

        state = client.get("/api/case").json()
        conflict = next(c for c in state["conflicts"] if c["id"] == "cf-1")
        assert conflict["status"] == "preserved_both"
        assert conflict["version"] == 2
        assert body["decision"]["id"] in conflict["decisionIds"]

    def test_stale_conflict_decision_is_rejected(self, client):
        login_as(client)
        state = client.get("/api/case").json()
        conflict = next(c for c in state["conflicts"] if c["id"] == "cf-1")
        assert conflict["version"] == 1

        first = client.post(
            "/api/conflicts/cf-1/decisions",
            json={
                "decisionType": "preserve_both",
                "reasoning": REASONING,
                "expectedVersion": conflict["version"],
            },
        )
        assert first.status_code == 201
        assert first.json()["conflict"]["version"] == 2

        stale = client.post(
            "/api/conflicts/cf-1/decisions",
            json={
                "decisionType": "escalate",
                "reasoning": REASONING,
                "expectedVersion": conflict["version"],
            },
        )
        assert stale.status_code == 409
        assert "expected version 1, current version 2" in stale.json()["detail"]

    def test_accept_claim_a(self, client):
        login_as(client)
        body = _decide(client, "cf-1", "accept_claim_a").json()
        a, b = body["claims"]
        assert (a["status"], a["reportEligibility"]) == ("supported", "eligible")
        assert (b["status"], b["reportEligibility"]) == ("superseded", "excluded")
        assert body["decision"]["selectedClaimId"] == "clm-a1"

    def test_source_unreliable_does_not_auto_accept(self, client):
        login_as(client)
        body = _decide(
            client, "cf-1", "mark_source_unreliable", selectedClaimId="clm-b1"
        ).json()
        a, b = body["claims"]
        assert (b["status"], b["reportEligibility"]) == ("rejected", "excluded")
        assert (a["status"], a["reportEligibility"]) == ("unreviewed", "needs_review")
        assert "not auto-accepted" in body["decision"]["reportImpact"]

    def test_claim_in_another_open_conflict_stays_blocked(self, client):
        # clm-b2 is in cf-2; flag it into a second, still-open conflict, then
        # preserve cf-2. Its global status is the aggregate of BOTH conflicts:
        # the open conflict's "disputed" outranks cf-2's "preserved", so clm-b2
        # reads "disputed" — not the misleading "preserved" the old last-
        # conflict-wins overwrite produced. Either way it stays blocked.
        login_as(client)
        flag = client.post(
            "/api/conflicts",
            json={
                "claimAId": "clm-b2",
                "claimBId": "clm-a4",
                "conflictType": "interpretation",
                "severity": "medium",
                "summary": "Icing finding conflicts with autopilot-state analysis.",
            },
        )
        assert flag.status_code == 201
        body = _decide(client, "cf-2", "preserve_both").json()
        b2 = next(c for c in body["claims"] if c["id"] == "clm-b2")
        assert b2["status"] == "disputed"
        assert b2["reportEligibility"] == "blocked_by_conflict"

    def test_critical_gating_and_escalation(self, client):
        login_as(client, "pnatarajan")
        denied = _decide(client, "cf-1", "accept_claim_a")
        assert denied.status_code == 403
        assert "Senior Aviation Counsel" in denied.json()["detail"]
        allowed = _decide(client, "cf-1", "escalate")
        assert allowed.status_code == 201
        assert allowed.json()["conflict"]["status"] == "escalated"
        assert allowed.json()["decision"]["reviewerName"] == "Priya Natarajan"

    def test_validation_and_not_found(self, client):
        login_as(client)
        short = client.post(
            "/api/conflicts/cf-1/decisions",
            json={
                "decisionType": "escalate",
                "reasoning": "x",
                "expectedVersion": 1,
            },
        )
        assert short.status_code == 422
        assert "reasoning note" in short.json()["detail"].lower()

        missing_claim = _decide(client, "cf-1", "mark_source_unreliable")
        assert missing_claim.status_code == 422

        foreign_claim = _decide(
            client, "cf-1", "mark_source_unreliable", selectedClaimId="clm-a4"
        )
        assert foreign_claim.status_code == 422

        unknown = _decide(client, "cf-1", "split_the_difference")
        assert unknown.status_code == 422

        assert _decide(client, "cf-99", "escalate").status_code == 404


class TestClaimsAndConflicts:
    NEW_CLAIM = {
        "sourceDocumentId": "src-fdr",
        "text": "Flight-recorder traces identified stable engine parameters during approach.",
        "pageRef": "p. 51",
        "quote": "Engine parameters remained within normal ranges throughout the approach.",
        "confidence": 0.8,
        "evidenceQuality": "primary",
    }
    PRIVILEGED_CLAIM = {
        "sourceDocumentId": "src-memo",
        "text": "Training-department interviews identified simulator scheduling shortfalls.",
        "pageRef": "p. 4, §III",
        "quote": "Sim availability forced repeated deferral of upset-recovery modules.",
        "confidence": 0.6,
        "evidenceQuality": "secondary",
    }

    def test_claim_inherits_source_privilege(self, client):
        login_as(client)
        response = client.post("/api/claims", json=self.PRIVILEGED_CLAIM)
        assert response.status_code == 201
        claim = response.json()["claim"]
        assert claim["id"] == "clm-11"
        assert claim["privilegeStatus"] == "attorney_client"
        assert claim["reportEligibility"] == "privileged"
        assert claim["status"] == "unreviewed"
        event = response.json()["auditEvent"]
        assert "Privilege inherited from source: attorney_client" in event["detail"]
        assert event["actor"] == "Miriam Okafor"

        state = client.get("/api/case").json()
        assert any(c["id"] == "clm-11" for c in state["claims"])

    def test_non_counsel_cannot_access_privileged_sources(self, client):
        """A withheld source is not merely refused — it is indistinguishable
        from a source that does not exist, so the rejection cannot be used to
        enumerate which documents are privilege-withheld."""
        login_as(client, "pnatarajan")
        state = client.get("/api/case").json()
        assert all(source["id"] != "src-memo" for source in state["sources"])
        assert all(claim["privilegeStatus"] != "attorney_client" for claim in state["claims"])

        response = client.post("/api/claims", json=self.PRIVILEGED_CLAIM)
        missing = client.post(
            "/api/claims",
            json={**self.PRIVILEGED_CLAIM, "sourceDocumentId": "src-never-ingested"},
        )

        assert response.status_code == 404
        assert missing.status_code == 404
        assert response.json()["detail"] == missing.json()["detail"] == (
            "Source document not found."
        )

    def test_claim_validation(self, client):
        login_as(client, "pnatarajan")
        assert (
            client.post("/api/claims", json={**self.NEW_CLAIM, "text": "short"}).status_code
            == 422
        )
        assert (
            client.post("/api/claims", json={**self.NEW_CLAIM, "quote": "x"}).status_code == 422
        )
        assert (
            client.post("/api/claims", json={**self.NEW_CLAIM, "pageRef": " "}).status_code
            == 422
        )
        assert (
            client.post("/api/claims", json={**self.NEW_CLAIM, "confidence": 1.5}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/claims", json={**self.NEW_CLAIM, "evidenceQuality": "vibes"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/claims", json={**self.NEW_CLAIM, "sourceDocumentId": "src-none"}
            ).status_code
            == 404
        )

    def test_flag_conflict_disputes_and_blocks(self, client):
        login_as(client, "pnatarajan")
        client.post("/api/claims", json=self.NEW_CLAIM)
        flag = {
            "claimAId": "clm-11",
            "claimBId": "clm-b5",
            "conflictType": "interpretation",
            "severity": "medium",
            "summary": "Simulator shortfalls contradict FAA-approved program adequacy.",
        }
        response = client.post("/api/conflicts", json=flag)
        assert response.status_code == 201
        body = response.json()
        assert body["conflict"]["id"] == "cf-6"
        assert body["conflict"]["status"] == "unresolved"
        a, b = body["claims"]
        assert a["status"] == b["status"] == "disputed"
        assert a["reportEligibility"] == b["reportEligibility"] == "blocked_by_conflict"
        assert "cf-6" in a["relatedConflictIds"]

        duplicate = client.post("/api/conflicts", json=flag)
        assert duplicate.status_code == 409
        assert "CF-6" in duplicate.json()["detail"]
        reversed_pair = client.post(
            "/api/conflicts", json={**flag, "claimAId": "clm-b5", "claimBId": "clm-11"}
        )
        assert reversed_pair.status_code == 409

    def test_flag_conflict_validation(self, client):
        login_as(client, "pnatarajan")
        base = {
            "claimAId": "clm-a4",
            "claimBId": "clm-b2",
            "conflictType": "factual",
            "severity": "medium",
            "summary": "A summary long enough to pass validation checks.",
        }
        assert (
            client.post("/api/conflicts", json={**base, "claimBId": "clm-a4"}).status_code
            == 422
        )
        assert (
            client.post("/api/conflicts", json={**base, "summary": "short"}).status_code == 422
        )
        assert (
            client.post("/api/conflicts", json={**base, "claimBId": "clm-none"}).status_code
            == 422
        )
        # A pair whose prior conflict is resolved may be re-flagged.
        refl = client.post(
            "/api/conflicts", json={**base, "claimAId": "clm-a4", "claimBId": "clm-b4"}
        )
        assert refl.status_code == 201


class TestReportSections:
    SECTION = {
        "title": "Engine Performance",
        "paragraphRef": "ENG-1",
        "text": "Engine parameters remained within normal ranges throughout the approach [FDR Data Readout, p. 51].",
        "claimIds": ["clm-a4"],
    }

    def test_existing_section_mutations_require_expected_version(self, client):
        login_as(client)
        created = client.post("/api/report-sections", json=self.SECTION)
        assert created.status_code == 201  # New sections do not need a version.
        section_id = created.json()["section"]["id"]

        revised = client.put(
            f"/api/report-sections/{section_id}",
            json={**self.SECTION, "text": self.SECTION["text"] + " Revised."},
        )
        assert revised.status_code == 422
        assert any(error["loc"][-1] == "expectedVersion" for error in revised.json()["detail"])

        approved = client.post(f"/api/report-sections/{section_id}/approve")
        assert approved.status_code == 422
        assert any(error["loc"][-1] == "expectedVersion" for error in approved.json()["detail"])

    def test_draft_and_revise_with_impact_audit(self, client):
        login_as(client)
        response = client.post("/api/report-sections", json=self.SECTION)
        assert response.status_code == 201
        body = response.json()
        assert body["section"]["id"] == "rpt-7"
        assert body["section"]["version"] == 1
        assert body["auditEvent"]["action"] == "report section drafted"
        assert body["auditEvent"]["newStatus"] == "eligible"

        revise = client.put(
            "/api/report-sections/rpt-7",
            json={
                **self.SECTION,
                "claimIds": ["clm-a4", "clm-a1"],
                "expectedVersion": 1,
            },
        )
        assert revise.status_code == 200
        assert revise.json()["section"]["version"] == 2
        event = revise.json()["auditEvent"]
        assert event["action"] == "report section revised"
        assert "citations added: clm-a1" in event["detail"]
        assert event["previousStatus"] == "eligible"
        assert event["newStatus"] == "blocked"
        assert event["reportEligibilityChanged"] is True

        stale = client.put(
            "/api/report-sections/rpt-7",
            json={
                **self.SECTION,
                "text": "This stale update is long enough to pass validation but must fail.",
                "expectedVersion": 1,
            },
        )
        assert stale.status_code == 409
        assert "expected version 1, current version 2" in stale.json()["detail"]

    def test_revisions_preserve_historical_wording(self, client):
        from sqlalchemy import select

        from atlas_argus.db.models import ReportSectionRevision
        from atlas_argus.db.session import SessionLocal

        login_as(client)
        created = client.post("/api/report-sections", json=self.SECTION).json()
        section_id = created["section"]["id"]
        revised_text = (
            "Engine parameters remained within normal ranges; this revised wording "
            "is preserved as a separate report revision."
        )
        revise = client.put(
            f"/api/report-sections/{section_id}",
            json={**self.SECTION, "text": revised_text, "expectedVersion": 1},
        )
        assert revise.status_code == 200

        with SessionLocal() as s:
            revisions = list(
                s.execute(
                    select(ReportSectionRevision)
                    .where(ReportSectionRevision.section_id == section_id)
                    .order_by(ReportSectionRevision.created_at)
                ).scalars()
            )
        assert len(revisions) == 2
        assert revisions[0].text == self.SECTION["text"]
        assert revisions[1].text == revised_text
        assert revisions[1].parent_revision_id == revisions[0].id

        state = client.get("/api/case").json()
        assert state["reportSections"][-1]["id"] == "rpt-7"

    def test_approval_state_is_exposed_and_resets_on_revision(self, client):
        login_as(client)
        created = client.post("/api/report-sections", json=self.SECTION).json()
        section = created["section"]
        section_id = section["id"]
        assert section["approvalState"] == "draft"
        assert section["activeRevisionId"] is not None

        state = client.get("/api/case").json()
        listed = next(s for s in state["reportSections"] if s["id"] == section_id)
        assert listed["approvalState"] == "draft"

        approved = client.post(
            f"/api/report-sections/{section_id}/approve",
            params={"expectedVersion": section["version"]},
        )
        assert approved.status_code == 200
        assert approved.json()["section"]["approvalState"] == "approved"

        state = client.get("/api/case").json()
        listed = next(s for s in state["reportSections"] if s["id"] == section_id)
        assert listed["approvalState"] == "approved"

        # Any further edit creates a new draft revision — approval doesn't
        # silently carry over to changed text.
        revised = client.put(
            f"/api/report-sections/{section_id}",
            json={
                **self.SECTION,
                "text": self.SECTION["text"] + " Revised.",
                "expectedVersion": approved.json()["section"]["version"],
            },
        )
        assert revised.json()["section"]["approvalState"] == "draft"

    def test_section_validation(self, client):
        login_as(client)
        assert (
            client.post(
                "/api/report-sections", json={**self.SECTION, "claimIds": []}
            ).status_code
            == 422
        )
        duplicate = client.post(
            "/api/report-sections", json={**self.SECTION, "paragraphRef": "pc-1"}
        )
        assert duplicate.status_code == 422
        assert "Probable Cause" in duplicate.json()["detail"]
        assert (
            client.post(
                "/api/report-sections", json={**self.SECTION, "claimIds": ["clm-x"]}
            ).status_code
            == 422
        )
        # Revising a section may keep its own paragraph reference.
        keep_own = client.put(
            "/api/report-sections/rpt-acft",
            json={
                "title": "Aircraft Systems and Automation",
                "paragraphRef": "ACFT-1",
                "text": "The autoflight system operated as designed throughout the approach sequence.",
                "claimIds": ["clm-a4"],
                "expectedVersion": 1,
            },
        )
        assert keep_own.status_code == 200

    def test_concurrent_drafts_serialize_reference_and_position_allocation(self, monkeypatch):
        from sqlalchemy import select

        from atlas_argus import services
        from atlas_argus.db.models import ReportSection, Reviewer
        from atlas_argus.db.session import SessionLocal
        from atlas_argus.domain.types import ValidationFailure

        gate = Barrier(2)
        original_lock = services._transaction_advisory_lock

        def synchronized_lock(session, *, chain, scope):
            if chain == "report_section_catalog":
                gate.wait(timeout=5)
            return original_lock(session, chain=chain, scope=scope)

        monkeypatch.setattr(services, "_transaction_advisory_lock", synchronized_lock)

        def draft(title: str) -> str:
            try:
                with SessionLocal() as session, session.begin():
                    reviewer = session.get(Reviewer, "rev-okafor")
                    assert reviewer is not None
                    services.save_section_op(
                        session,
                        reviewer,
                        title=title,
                        paragraph_ref="RACE-1",
                        text_=(
                            "Concurrent drafting must allocate one reference and one "
                            "stable display position without a server error."
                        ),
                        claim_ids=["clm-a4"],
                        case_id="case-3407",
                    )
                return "created"
            except ValidationFailure:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(draft, ["Concurrent draft A", "Concurrent draft B"]))
        assert sorted(results) == ["created", "duplicate"]

        with SessionLocal() as session:
            sections = list(
                session.execute(
                    select(ReportSection)
                    .where(ReportSection.case_id == "case-3407")
                    .order_by(ReportSection.position)
                ).scalars()
            )
        assert sum(section.paragraph_ref == "RACE-1" for section in sections) == 1
        positions = [section.position for section in sections]
        assert len(positions) == len(set(positions))


class TestReset:
    def test_reset_is_not_a_runtime_api(self, client):
        login_as(client)
        assert client.post("/api/admin/reset").status_code == 404
