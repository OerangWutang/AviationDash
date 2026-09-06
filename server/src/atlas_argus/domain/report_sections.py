"""Report-section drafting rules. Mirrors ``src/domain/reportSections.ts``.

- No report text without source references: a section cannot be saved without
  citing at least one claim.
- Paragraph references are unique (case-insensitive).
- Sections are never deleted; drafting and every revision are audited.
"""

from __future__ import annotations

MIN_SECTION_TITLE_LENGTH = 3
MIN_SECTION_TEXT_LENGTH = 40


def validate_section(
    title: str,
    paragraph_ref: str,
    text: str,
    claim_ids: list[str],
    other_sections: list[tuple[str, str, str]],
    known_claim_ids: frozenset[str] | set[str],
    editing_section_id: str | None = None,
) -> str | None:
    """Validate a section draft/revision.

    ``other_sections`` is a list of ``(section_id, paragraph_ref, title)`` for
    every existing section in the case.
    """
    if len(title.strip()) < MIN_SECTION_TITLE_LENGTH:
        return (
            f"A section title of at least {MIN_SECTION_TITLE_LENGTH} characters is "
            "required."
        )
    ref = paragraph_ref.strip()
    if len(ref) == 0:
        return "A paragraph reference (e.g. PC-2) is required."
    for section_id, existing_ref, existing_title in other_sections:
        if section_id != editing_section_id and existing_ref.lower() == ref.lower():
            return f"Paragraph reference “{ref}” is already used by “{existing_title}”."
    if len(text.strip()) < MIN_SECTION_TEXT_LENGTH:
        return (
            f"Section text of at least {MIN_SECTION_TEXT_LENGTH} characters is required."
        )
    if len(claim_ids) == 0:
        return "No report text without source references — cite at least one claim."
    if len(set(claim_ids)) != len(claim_ids):
        return "Each claim may be cited only once per report section."
    for claim_id in claim_ids:
        if claim_id not in known_claim_ids:
            return f"Cited claim not found: {claim_id}."
    return None


def revision_changes(
    old_title: str,
    old_ref: str,
    old_text: str,
    old_claim_ids: list[str],
    new_title: str,
    new_ref: str,
    new_text: str,
    new_claim_ids: list[str],
) -> str:
    """Human-readable citation/text diff for the revision audit event."""
    added = [c for c in new_claim_ids if c not in old_claim_ids]
    removed = [c for c in old_claim_ids if c not in new_claim_ids]
    changes: list[str] = []
    if added:
        changes.append(f"citations added: {', '.join(added)}")
    if removed:
        changes.append(f"citations removed: {', '.join(removed)}")
    if new_text != old_text:
        changes.append("text revised")
    if new_title != old_title:
        changes.append("title changed")
    if new_ref != old_ref:
        changes.append(f"paragraph reference {old_ref} → {new_ref}")
    return "; ".join(changes) if changes else "no substantive changes"
