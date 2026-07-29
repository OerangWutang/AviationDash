"""Report-section usability. Mirrors ``src/domain/report.ts``.

Priority: an unresolved conflict blocks outright; privileged material must be
withheld; anything unreviewed/excluded needs editorial attention; preserved
conflicts are usable only with the conflict disclosed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CitedClaim:
    """The slice of a claim that section impact depends on."""

    id: str
    status: str
    report_eligibility: str
    unresolved_conflict_summaries: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectionImpact:
    status: str
    offending_claim_ids: list[str] = field(default_factory=list)
    note: str = ""


def compute_section_impact(paragraph_ref: str, cited: list[CitedClaim]) -> SectionImpact:
    blocked = [c for c in cited if c.report_eligibility == "blocked_by_conflict"]
    if blocked:
        conflict_names: list[str] = []
        for claim in blocked:
            for summary in claim.unresolved_conflict_summaries:
                if summary not in conflict_names:
                    conflict_names.append(summary)
        what = (
            "a disputed claim" if len(blocked) == 1 else f"{len(blocked)} disputed claims"
        )
        return SectionImpact(
            status="blocked",
            offending_claim_ids=[c.id for c in blocked],
            note=(
                f"Report blocked: paragraph {paragraph_ref} references {what} "
                f"({'; '.join(conflict_names)})."
            ),
        )

    privileged = [c for c in cited if c.report_eligibility == "privileged"]
    if privileged:
        return SectionImpact(
            status="privileged_material",
            offending_claim_ids=[c.id for c in privileged],
            note=(
                f"Paragraph {paragraph_ref} cites privileged material that must be "
                "withheld or replaced with a non-privileged source before production."
            ),
        )

    attention = [
        c for c in cited if c.report_eligibility in ("needs_review", "excluded")
    ]
    if attention:
        excluded = [c for c in attention if c.report_eligibility == "excluded"]
        if excluded:
            note = (
                f"Paragraph {paragraph_ref} cites {len(excluded)} excluded claim(s); "
                "the citation must be removed or replaced, and remaining support "
                "re-reviewed."
            )
        else:
            note = (
                f"Paragraph {paragraph_ref} cites claims that still need review "
                "before the section can be used."
            )
        return SectionImpact(
            status="needs_review",
            offending_claim_ids=[c.id for c in attention],
            note=note,
        )

    preserved = [c for c in cited if c.status == "preserved"]
    if preserved:
        return SectionImpact(
            status="eligible_with_disclosure",
            offending_claim_ids=[c.id for c in preserved],
            note=(
                f"Paragraph {paragraph_ref} is usable only if the preserved conflict "
                "is disclosed alongside both claims."
            ),
        )

    return SectionImpact(
        status="eligible",
        note="All cited claims are report-eligible with source citations intact.",
    )
