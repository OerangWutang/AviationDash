"""Server-side packet doctrine: the disclosure artifact is built, redacted,
and hash-stamped where a tampered client cannot reach it."""

from __future__ import annotations

import hashlib
import threading

from conftest import login_as
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from atlas_argus import packet as packet_module
from atlas_argus import services
from atlas_argus.db.models import PacketArtifact, ReportSection, Reviewer
from atlas_argus.db.session import SessionLocal
from atlas_argus.integrity import canonical_sha256

# Content that exists only in privileged material / internal assessments.
MEMO_QUOTE = "materially thinner"
MEMO_CLAIM = "stall-recovery and tailplane-icing training"
DECISION_REASONING = "dispositive on autopilot state"


def _generate(client, packet_type):
    return client.post("/api/packets", json={"packetType": packet_type})


class TestProductionPacket:
    def test_redaction_manifest_and_privilege_log(self, client):
        login_as(client)
        response = _generate(client, "production")
        assert response.status_code == 201
        body = response.json()
        assert body["stats"] == {"included": 1, "excluded": 4, "withheld": 1}
        by_ref = {entry["paragraphRef"]: entry for entry in body["entries"]}
        assert by_ref["ACFT-1"]["disposition"] == "included"
        nonincluded = [entry for entry in body["entries"] if entry["disposition"] != "included"]
        assert len(nonincluded) == 5
        for entry in nonincluded:
            prefix = "WITHHELD-" if entry["disposition"] == "withheld" else "EXCLUDED-"
            assert entry["sectionId"].startswith(prefix)
            assert entry["paragraphRef"] == entry["sectionId"]
            assert entry["title"] in {"Withheld material", "Excluded material"}
            assert "revisionId" not in entry
            assert entry["reason"] in {
                "Withheld under privilege; see privilege log.",
                "Excluded from production.",
            }

        document = body["document"]
        assert "PRODUCTION COPY" in document
        assert "Privilege log" in document
        assert "Internal Litigation Strategy Memorandum" in document  # identity only
        assert MEMO_QUOTE not in document
        assert MEMO_CLAIM not in document
        assert DECISION_REASONING not in document
        assert "confidence:" not in document
        assert "Miriam Okafor" not in document
        assert "Senior Aviation Counsel" not in document
        for private_ref in ("PC-1", "WX-2", "TRN-2"):
            assert private_ref not in document

    def test_embedded_hash_matches_recomputed_body(self, client):
        login_as(client)
        body = _generate(client, "production").json()
        document = body["document"]
        start = document.index('<div class="packet">\n') + len('<div class="packet">\n')
        end = document.index("\n<footer>")
        recomputed = hashlib.sha256(document[start:end].encode()).hexdigest()
        assert recomputed == body["sha256"]
        assert body["sha256"] in document

    def test_disclosure_appears_after_preserving_a_conflict(self, client):
        login_as(client)
        client.post(
            "/api/conflicts/cf-1/decisions",
            json={
                "decisionType": "preserve_both",
                "reasoning": "Both accounts preserved pending expert reconciliation.",
                "expectedVersion": 1,
            },
        )
        document = _generate(client, "production").json()["document"]
        assert "Conflict disclosure" in document
        assert "warning horn a good while" in document  # counterpart via disclosure
        assert "neither has been suppressed" in document
        assert "expert reconciliation" not in document  # reasoning stays internal

    def test_reserved_for_counsel_and_validated(self, client):
        login_as(client, "pnatarajan")
        assert _generate(client, "production").status_code == 403
        assert _generate(client, "leak").status_code == 422
        assert _generate(client, "internal").status_code == 403

    def test_generation_is_audited(self, client):
        login_as(client)
        body = _generate(client, "production").json()
        assert body["auditEvent"]["action"] == "evidence packet generated"
        assert body["sha256"] in body["auditEvent"]["detail"]
        assert body["artifactSha256"] in body["auditEvent"]["detail"]
        assert body["documentSha256"] == hashlib.sha256(body["document"].encode()).hexdigest()
        state = client.get("/api/case").json()
        newest = state["auditEvents"][0]
        assert newest["subjectType"] == "export"
        assert newest["subjectId"] == body["packetId"]
        with SessionLocal() as s:
            artifact = s.get(PacketArtifact, body["packetId"])
            assert artifact is not None
            assert artifact.body_sha256 == body["sha256"]
            assert artifact.document == body["document"]
            included = next(
                entry
                for entry in artifact.manifest["entries"]
                if entry["disposition"] == "included"
            )
            assert included["revisionId"] is not None
            assert included["revisionContentSha256"] is not None
            for entry in artifact.manifest["entries"]:
                if entry["disposition"] == "included":
                    continue
                assert "revisionId" not in entry
                assert "revisionContentSha256" not in entry
                assert "claimIds" not in entry
            assert artifact.manifest["bodySha256"] == body["sha256"]
            assert artifact.manifest["documentSha256"] == body["documentSha256"]
            assert artifact.manifest["artifactSha256"] == body["artifactSha256"]
            manifest_without_hashes = dict(artifact.manifest)
            manifest_sha256 = manifest_without_hashes.pop("manifestSha256")
            artifact_sha256 = manifest_without_hashes.pop("artifactSha256")
            assert canonical_sha256(manifest_without_hashes) == manifest_sha256
            assert artifact_sha256 == canonical_sha256(
                {
                    "schema": "atlas_argus.packet_artifact.v1",
                    "documentSha256": body["documentSha256"],
                    "manifestSha256": manifest_sha256,
                }
            )
        with SessionLocal() as s, s.begin():
            try:
                s.execute(
                    text("UPDATE packet_artifact SET body_sha256 = 'tampered' WHERE id = :id"),
                    {"id": body["packetId"]},
                )
            except DBAPIError as exc:
                assert "packet_artifact is append-only" in str(exc)
            else:
                raise AssertionError("packet artifact mutation was not rejected")

    def test_approved_packet_binds_to_revision_not_mutated_section(self, client):
        # An approved production packet must render the APPROVED revision's
        # immutable snapshot — never the mutable section row. If the section is
        # changed out-of-band (bug, direct SQL, partial transaction) while
        # active_revision_id still points at the approved revision, the packet
        # must ignore the change: the manifest names a revision, so the body has
        # to be that revision's content.
        login_as(client)
        created = client.post(
            "/api/report-sections",
            json={
                "title": "Approved Autopilot Findings",
                "paragraphRef": "APV-1",
                "text": "The approved narrative describes autopilot disengagement fully.",
                "claimIds": ["clm-a4"],
            },
        )
        assert created.status_code == 201
        section = created.json()["section"]
        section_id = section["id"]
        assert (
            client.post(
                f"/api/report-sections/{section_id}/approve",
                params={"expectedVersion": section["version"]},
            ).status_code
            == 200
        )

        # Out-of-band tamper: rewrite the mutable section row directly, leaving
        # active_revision_id pointing at the approved revision. report_section is
        # not append-only guarded — this is exactly the path finding #2 defends.
        with SessionLocal() as s, s.begin():
            s.execute(
                text(
                    "UPDATE report_section "
                    "SET title = :t, text = :x, claim_ids = '[]'::jsonb WHERE id = :id"
                ),
                {
                    "t": "TAMPERED TITLE",
                    "x": "TAMPERED body substituted into the section after approval.",
                    "id": section_id,
                },
            )

        body = _generate(client, "production").json()
        entry = next(e for e in body["entries"] if e["sectionId"] == section_id)
        # Disposition and citations bind to the approved revision's claim_ids,
        # not the emptied section — the section is still included and cites clm-a4.
        assert entry["disposition"] == "included"
        assert entry["title"] == "Approved Autopilot Findings"

        document = body["document"]
        assert "Approved Autopilot Findings" in document
        assert "autopilot disengagement fully" in document
        assert "TAMPERED" not in document

    def test_generation_uses_one_committed_case_snapshot(self, monkeypatch):
        with SessionLocal() as session:
            section = session.get(ReportSection, "rpt-acft")
            assert section is not None
            original_revision_id = section.active_revision_id
            section_payload = {
                "title": section.title,
                "paragraph_ref": section.paragraph_ref,
                "text_": section.text + " A concurrent draft changes this wording.",
                "claim_ids": list(section.claim_ids),
                "section_id": section.id,
                "expected_version": section.version,
                "case_id": section.case_id,
            }

        packet_started_build = threading.Event()
        allow_packet_build = threading.Event()
        mutation_reached_audit_lock = threading.Event()
        results: dict[str, dict] = {}
        errors: list[Exception] = []
        original_build_packet = packet_module.build_packet
        original_audit_lock = services._lock_audit_chain

        def observed_build_packet(*args, **kwargs):
            packet_started_build.set()
            if not allow_packet_build.wait(timeout=10):
                raise TimeoutError("packet snapshot test did not release build")
            return original_build_packet(*args, **kwargs)

        def observed_audit_lock(session, case_id):
            if threading.current_thread().name == "case-mutation-worker":
                mutation_reached_audit_lock.set()
            return original_audit_lock(session, case_id)

        monkeypatch.setattr(packet_module, "build_packet", observed_build_packet)
        monkeypatch.setattr(services, "_lock_audit_chain", observed_audit_lock)

        def generate_worker() -> None:
            try:
                with SessionLocal() as session, session.begin():
                    reviewer = session.get(Reviewer, "rev-okafor")
                    assert reviewer is not None
                    results["packet"] = services.generate_packet_op(
                        session,
                        reviewer,
                        packet_type="production",
                        case_id="case-3407",
                    )
            except Exception as exc:  # pragma: no cover - re-raised below
                errors.append(exc)

        def mutate_worker() -> None:
            try:
                with SessionLocal() as session, session.begin():
                    reviewer = session.get(Reviewer, "rev-okafor")
                    assert reviewer is not None
                    results["mutation"] = services.save_section_op(
                        session, reviewer, **section_payload
                    )
            except Exception as exc:  # pragma: no cover - re-raised below
                errors.append(exc)

        packet_thread = threading.Thread(
            target=generate_worker, name="packet-generation-worker"
        )
        mutation_thread = threading.Thread(target=mutate_worker, name="case-mutation-worker")
        packet_thread.start()
        try:
            assert packet_started_build.wait(timeout=10)
            mutation_thread.start()
            # save_section_op has written its new revision and is now waiting
            # on the packet transaction's case-audit lock before it can commit.
            assert mutation_reached_audit_lock.wait(timeout=10)
        finally:
            allow_packet_build.set()
            packet_thread.join(timeout=10)
            if mutation_thread.ident is not None:
                mutation_thread.join(timeout=10)

        assert not packet_thread.is_alive()
        assert not mutation_thread.is_alive()
        if errors:
            raise errors[0]

        packet_entry = next(
            entry for entry in results["packet"]["entries"] if entry["sectionId"] == "rpt-acft"
        )
        assert packet_entry["revisionId"] == original_revision_id
        with SessionLocal() as session:
            revised_section = session.get(ReportSection, "rpt-acft")
            assert revised_section is not None
            assert revised_section.active_revision_id != original_revision_id


class TestInternalPacket:
    def test_contains_everything_marked(self, client):
        login_as(client)
        body = _generate(client, "internal").json()
        assert body["stats"]["included"] == 6
        document = body["document"]
        assert "ATTORNEY WORK PRODUCT" in document
        assert "DO NOT PRODUCE" in document
        assert MEMO_CLAIM in document
        assert DECISION_REASONING in document
        assert "confidence:" in document

    def test_evidence_content_is_html_escaped(self, client):
        login_as(client)
        claim = client.post(
            "/api/claims",
            json={
                "sourceDocumentId": "src-fdr",
                "text": "<script>alert('xss')</script> torque symmetry note",
                "pageRef": "p. 51",
                "quote": "Torque L/R traces track within 2% through the upset.",
                "confidence": 0.9,
                "evidenceQuality": "primary",
            },
        ).json()["claim"]
        client.post(
            "/api/report-sections",
            json={
                "title": "Escaping Probe",
                "paragraphRef": "ESC-1",
                "text": "A paragraph long enough to satisfy section validation rules.",
                "claimIds": [claim["id"]],
            },
        )
        document = _generate(client, "internal").json()["document"]
        assert "<script>" not in document
        assert "&lt;script&gt;" in document

    def test_draft_sections_are_opaque_until_approved_for_production(self, client):
        login_as(client)
        created = client.post(
            "/api/report-sections",
            json={
                "title": "Draft Probe",
                "paragraphRef": "DR-1",
                "text": "A public draft section long enough to satisfy validation.",
                "claimIds": ["clm-a4"],
            },
        )
        assert created.status_code == 201
        section_id = created.json()["section"]["id"]
        production = _generate(client, "production").json()
        assert all(entry["sectionId"] != section_id for entry in production["entries"])
        assert any(
            entry["disposition"] == "excluded"
            and entry["sectionId"].startswith("EXCLUDED-")
            and entry["reason"] == "Excluded from production."
            for entry in production["entries"]
        )

        approved = client.post(
            f"/api/report-sections/{section_id}/approve",
            params={"expectedVersion": created.json()["section"]["version"]},
        )
        assert approved.status_code == 200
        production = _generate(client, "production").json()
        entry = next(e for e in production["entries"] if e["sectionId"] == section_id)
        assert entry["disposition"] == "included"
