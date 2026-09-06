"""Database constraints reject impossible cross-case legal relationships."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def _insert_other_case(session) -> None:
    session.execute(
        text(
            """
            INSERT INTO case_file
              (id, name, aircraft, accident_date, location, matter_type, status, docket_ref)
            VALUES
              ('case-other', 'Other Matter', 'Q400', '2026-01-01', 'Other',
               'wrongful_death', 'open', 'OTHER')
            """
        )
    )


def _insert_other_source(session) -> None:
    session.execute(
        text(
            """
            INSERT INTO source_document
              (id, case_id, title, type, origin, custodian, docket_ref, ingested_at,
               sha256, privilege_status, custody)
            VALUES
              ('src-other', 'case-other', 'Other Source', 'official_report', 'Other',
               'Other', NULL, now(), repeat('a', 64), 'public', '[]'::jsonb)
            """
        )
    )


def _insert_other_claim(session) -> None:
    session.execute(
        text(
            """
            INSERT INTO claim
              (id, case_id, text, source_document_id, page_ref, quote, confidence,
               evidence_quality, status, privilege_status, report_eligibility,
               reviewer, updated_at)
            VALUES
              ('clm-other', 'case-other', 'Other claim long enough', 'src-other',
               'p. 1', 'Other quote long enough', 0.8, 'primary', 'unreviewed',
               'public', 'needs_review', NULL, now())
            """
        )
    )


def test_claim_source_must_belong_to_same_case(session):
    _insert_other_case(session)
    with pytest.raises(DBAPIError, match="fk_claim_source_same_case"):
        session.execute(
            text(
                """
                INSERT INTO claim
                  (id, case_id, text, source_document_id, page_ref, quote, confidence,
                   evidence_quality, status, privilege_status, report_eligibility,
                   reviewer, updated_at)
                VALUES
                  ('clm-cross-source', 'case-other', 'Cross-case source claim',
                   'src-fdr', 'p. 1', 'Quote long enough', 0.8, 'primary',
                   'unreviewed', 'public', 'needs_review', NULL, now())
                """
            )
        )


def test_conflict_claims_must_belong_to_same_case(session):
    _insert_other_case(session)
    _insert_other_source(session)
    _insert_other_claim(session)
    with pytest.raises(DBAPIError, match="fk_conflict_claim_b_same_case"):
        session.execute(
            text(
                """
                INSERT INTO conflict
                  (id, case_id, claim_a_id, claim_b_id, conflict_type, severity,
                   status, summary, created_at, version, claim_a_disposition,
                   claim_b_disposition)
                VALUES
                  ('cf-cross-case', 'case-3407', 'clm-a4', 'clm-other',
                   'factual', 'medium', 'unresolved',
                   'Cross-case conflict should be impossible.', now(), 1,
                   'disputed', 'disputed')
                """
            )
        )


def test_decision_selected_claim_must_belong_to_conflict(session):
    with pytest.raises(DBAPIError, match="selected_claim_id must be one of"):
        session.execute(
            text(
                """
                INSERT INTO review_decision
                  (id, conflict_id, decision_type, selected_claim_id, reasoning,
                   reviewer_name, reviewer_role, created_at, previous_status,
                   new_status, report_impact)
                VALUES
                  ('dec-cross-selected', 'cf-1', 'mark_source_unreliable',
                   'clm-a4', 'Invalid selected claim.', 'Miriam Okafor',
                   'Senior Aviation Counsel', now(), 'unresolved',
                   'source_unreliable', 'Invalid')
                """
            )
        )


def test_report_revision_must_belong_to_same_case_as_section(session):
    _insert_other_case(session)
    with pytest.raises(DBAPIError, match="fk_report_revision_section_same_case"):
        session.execute(
            text(
                """
                INSERT INTO report_section_revision
                  (id, section_id, case_id, title, paragraph_ref, text, claim_ids,
                   author_reviewer_id, author, author_role, created_at,
                   revision_reason, parent_revision_id, approval_state, content_sha256,
                   integrity_hash)
                VALUES
                  ('rsr-cross-case', 'rpt-acft', 'case-other', 'Bad Revision',
                   'BAD-1', 'Cross-case revision text should be impossible.',
                   '[]'::jsonb, 'rev-okafor', 'Miriam Okafor',
                   'Senior Aviation Counsel', now(), 'Invalid', NULL, 'draft', NULL,
                   'deadbeef')
                """
            )
        )
