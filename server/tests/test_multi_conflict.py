"""Finding #4: a claim's global status is the aggregate of its per-conflict
dispositions. Deciding or flagging one conflict never erases what another
conflict already established about a shared claim."""

from __future__ import annotations

from conftest import login_as

from atlas_argus import services
from atlas_argus.domain.decisions import aggregate_claim_status


def _flag(client, claim_a, claim_b, summary, severity="medium"):
    return client.post(
        "/api/conflicts",
        json={
            "claimAId": claim_a,
            "claimBId": claim_b,
            "conflictType": "factual",
            "severity": severity,
            "summary": summary,
        },
    )


def _decide(client, conflict_id, decision_type, selected=None):
    conflict = next(
        row
        for row in client.get("/api/case").json()["conflicts"]
        if row["id"] == conflict_id
    )
    body = {
        "decisionType": decision_type,
        "reasoning": "Documented rationale for this decision.",
        "expectedVersion": conflict["version"],
    }
    if selected is not None:
        body["selectedClaimId"] = selected
    return client.post(f"/api/conflicts/{conflict_id}/decisions", json=body)


def _status(client, claim_id):
    state = client.get("/api/case").json()
    return next(c for c in state["claims"] if c["id"] == claim_id)


def test_affected_claim_rows_are_locked_in_sorted_order(session):
    locked = services._lock_claim_rows(session, ["clm-b1", "clm-a1", "clm-b1"])
    assert list(locked) == ["clm-a1", "clm-b1"]


class TestAggregatePrecedence:
    def test_empty_is_unreviewed(self):
        assert aggregate_claim_status([]) == "unreviewed"

    def test_out_dominates_open_and_positive(self):
        assert aggregate_claim_status(["disputed", "rejected", "supported"]) == "rejected"
        assert aggregate_claim_status(["superseded", "disputed", "preserved"]) == "superseded"

    def test_open_dominates_positive(self):
        assert aggregate_claim_status(["supported", "disputed"]) == "disputed"
        assert aggregate_claim_status(["preserved", "escalated"]) == "escalated"

    def test_preserved_outranks_supported(self):
        assert aggregate_claim_status(["supported", "preserved"]) == "preserved"

    def test_singleton_is_itself(self):
        for s in (
            "supported",
            "preserved",
            "disputed",
            "escalated",
            "rejected",
            "superseded",
            "unreviewed",
        ):
            assert aggregate_claim_status([s]) == s


class TestMultiConflictClaimStatus:
    def test_acceptance_survives_a_later_conflict_and_is_restored(self, client):
        # clm-a4 is accepted (supported) in cf-4. Flag it into a new open
        # conflict: it reads "disputed" globally WITHOUT erasing cf-4's
        # acceptance; resolve the new conflict in its favour and it is restored.
        login_as(client)
        assert _status(client, "clm-a4")["status"] == "supported"
        flag = _flag(client, "clm-a4", "clm-a1", "Autopilot vs stall-onset dispute.")
        assert flag.status_code == 201
        new_cf = flag.json()["conflict"]["id"]
        assert _status(client, "clm-a4")["status"] == "disputed"
        assert _decide(client, new_cf, "accept_claim_a").status_code == 201
        assert _status(client, "clm-a4")["status"] == "supported"

    def test_out_dominates_a_still_open_conflict(self, client):
        # Discredit clm-a4 in one conflict while it also sits in a second,
        # still-open conflict: rejected wins over the open dispute, so it is
        # excluded rather than merely blocked.
        login_as(client)
        first = _flag(client, "clm-a4", "clm-a1", "First dispute over the autopilot claim.")
        assert _flag(client, "clm-a4", "clm-b1", "Second, still-open dispute.").status_code == 201
        assert _decide(
            client, first.json()["conflict"]["id"], "mark_source_unreliable", selected="clm-a4"
        ).status_code == 201
        a4 = _status(client, "clm-a4")
        assert a4["status"] == "rejected"
        assert a4["reportEligibility"] == "excluded"

    def test_deciding_one_conflict_leaves_an_unrelated_pair_untouched(self, client):
        login_as(client)
        before = {c["id"]: c["status"] for c in client.get("/api/case").json()["claims"]}
        assert _decide(client, "cf-1", "accept_claim_a").status_code == 201
        after = {c["id"]: c["status"] for c in client.get("/api/case").json()["claims"]}
        assert (after["clm-a2"], after["clm-b2"]) == (before["clm-a2"], before["clm-b2"])
        assert (after["clm-a1"], after["clm-b1"]) == ("supported", "superseded")

    def test_redeciding_a_conflict_recomputes_both_claims(self, client):
        # Reversing a decision must re-derive status, not leave the earlier one.
        login_as(client)
        assert _decide(client, "cf-1", "accept_claim_a").status_code == 201
        assert _status(client, "clm-b1")["status"] == "superseded"
        assert _decide(client, "cf-1", "preserve_both").status_code == 201
        assert _status(client, "clm-a1")["status"] == "preserved"
        assert _status(client, "clm-b1")["status"] == "preserved"
