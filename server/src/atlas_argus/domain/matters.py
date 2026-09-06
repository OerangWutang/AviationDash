"""Validation for creating a matter.

Kept separate from ``services.py`` so the rules are testable without a
database, matching ``domain/sources.py``.
"""

from __future__ import annotations

import re
from datetime import date as calendar_date

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

#: The implemented lifecycle currently has one state. Intake does not accept
#: a client-supplied status, and no close/reopen transition exists yet.
INITIAL_STATUS = "open"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_text(value: str | None, *, field: str, maximum: int) -> str:
    text = (value or "").strip()
    if not text:
        raise t.ValidationFailure(f"{field} is required.")
    if len(text) > maximum:
        raise t.ValidationFailure(f"{field} must be {maximum} characters or fewer.")
    return text


def validate_accident_date(value: str | None) -> str:
    date = (value or "").strip()
    if not _ISO_DATE.match(date):
        raise t.ValidationFailure("Accident date must be an ISO date (YYYY-MM-DD).")
    try:
        parsed = calendar_date.fromisoformat(date)
    except ValueError as exc:
        raise t.ValidationFailure("Accident date is not a real date.") from exc
    if parsed > calendar_date.today():
        raise t.ValidationFailure("Accident date must not be in the future.")
    return date


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

    date = validate_accident_date(accident_date)

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
