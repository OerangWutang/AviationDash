"""Validation for creating a matter.

Kept separate from ``services.py`` so the rules are testable without a
database, matching ``domain/sources.py``.
"""

from __future__ import annotations

import re

from . import types as t

MAX_NAME_CHARS = 200
MAX_AIRCRAFT_CHARS = 200
MAX_LOCATION_CHARS = 200
MAX_DOCKET_REF_CHARS = 120

#: Free text in the existing schema, but these are the values the product
#: actually uses; anything else is almost certainly a client bug rather than a
#: novel matter type worth silently storing.
MATTER_TYPES = frozenset(
    {
        "wrongful_death",
        "personal_injury",
        "property_damage",
        "subrogation",
        "regulatory_enforcement",
        "insurance_coverage",
    }
)

#: A new matter always starts open. Later status changes are a separate
#: operation with their own audit trail, so intake does not accept one.
INITIAL_STATUS = "open"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_text(value: str | None, *, field: str, maximum: int) -> str:
    text = (value or "").strip()
    if not text:
        raise t.ValidationFailure(f"{field} is required.")
    if len(text) > maximum:
        raise t.ValidationFailure(f"{field} must be {maximum} characters or fewer.")
    return text


def validate_new_matter(
    *,
    name: str | None,
    aircraft: str | None,
    accident_date: str | None,
    location: str | None,
    matter_type: str | None,
    docket_ref: str | None,
) -> dict[str, str]:
    if matter_type not in MATTER_TYPES:
        raise t.ValidationFailure("Unknown matter type.")

    date = (accident_date or "").strip()
    if not _ISO_DATE.match(date):
        raise t.ValidationFailure("Accident date must be an ISO date (YYYY-MM-DD).")
    # Stored as text in this schema, so the format check above is not enough on
    # its own — reject a well-shaped but impossible date rather than carrying
    # it into every downstream display and export.
    year, month, day = (int(part) for part in date.split("-"))
    try:
        from datetime import date as _date

        _date(year, month, day)
    except ValueError as exc:
        raise t.ValidationFailure("Accident date is not a real date.") from exc

    return {
        "name": _require_text(name, field="Matter name", maximum=MAX_NAME_CHARS),
        "aircraft": _require_text(aircraft, field="Aircraft", maximum=MAX_AIRCRAFT_CHARS),
        "accidentDate": date,
        "location": _require_text(location, field="Location", maximum=MAX_LOCATION_CHARS),
        "matterType": matter_type,
        "status": INITIAL_STATUS,
        "docketRef": _require_text(
            docket_ref, field="Docket reference", maximum=MAX_DOCKET_REF_CHARS
        ),
    }
