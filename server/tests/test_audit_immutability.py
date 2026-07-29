"""The audit trail is append-only at the DATABASE level — no code path,
including raw SQL, can rewrite history."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def test_update_is_rejected(session):
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(
            text("UPDATE audit_event SET detail = 'rewritten' WHERE id = 'aud-001'")
        )


def test_delete_is_rejected(session):
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("DELETE FROM audit_event WHERE id = 'aud-001'"))


def test_report_revision_update_is_rejected(session):
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(
            text(
                "UPDATE report_section_revision SET text = 'rewritten' "
                "WHERE id = 'rpt-acft-rev-001'"
            )
        )


def test_review_decision_update_is_rejected(session):
    with pytest.raises(DBAPIError, match="review_decision is append-only"):
        session.execute(
            text(
                "UPDATE review_decision SET reasoning = 'rewritten' "
                "WHERE id = 'dec-cf4-1'"
            )
        )


def test_review_decision_delete_is_rejected(session):
    with pytest.raises(DBAPIError, match="review_decision is append-only"):
        session.execute(text("DELETE FROM review_decision WHERE id = 'dec-cf4-1'"))
