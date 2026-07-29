"""Report-eligibility rules. Mirrors ``src/domain/eligibility.ts``.

Rule order is doctrine, not style:
 1. Privilege withholds — attorney-client / work-product / restricted material
    can never become report-eligible, whatever the review outcome.
 2. Rejected or superseded claims are excluded from reports (but remain
    visible in the record).
 3. A quote nothing has confirmed cannot reach a report, whatever the review
    outcome — a reviewer approving a claim is not the same as the quote
    actually appearing in the evidence.
 4. Any unresolved conflict blocks report use.
 5. An escalated conflict parks the claim pending senior review.
 6. Only affirmatively supported or preserved claims are eligible.
 7. Everything else (unreviewed, disputed, escalated) still needs review.
"""

from __future__ import annotations

from collections.abc import Iterable

from .types import UNVERIFIED_QUOTE_STATES, WITHHOLDING_PRIVILEGE


def compute_claim_eligibility(
    claim_status: str,
    privilege_status: str,
    related_conflict_statuses: Iterable[str],
    quote_verification: str = "legacy_unverified",
) -> str:
    if privilege_status in WITHHOLDING_PRIVILEGE:
        return "privileged"
    if claim_status in ("rejected", "superseded"):
        return "excluded"
    # Legacy claims predate ingestion and are deliberately not blocked — doing
    # so would retroactively break every existing matter. They carry an honest
    # "unverified" label instead. Only a claim whose page was ingested and
    # could not confirm the quote is held back.
    if quote_verification in UNVERIFIED_QUOTE_STATES:
        return "needs_review"
    statuses = list(related_conflict_statuses)
    if "unresolved" in statuses:
        return "blocked_by_conflict"
    if "escalated" in statuses:
        return "needs_review"
    if claim_status in ("supported", "preserved"):
        return "eligible"
    return "needs_review"
