"""row-level security for case-scoped evidence

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CASE_SCOPED_TABLES = (
    ("case_file", "id"),
    ("source_document", "case_id"),
    ("claim", "case_id"),
    ("conflict", "case_id"),
    ("audit_event", "case_id"),
    ("report_section", "case_id"),
    ("report_section_revision", "case_id"),
    ("packet_artifact", "case_id"),
)


def _policy_name(table: str) -> str:
    return f"rls_{table}_active_case_member"


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION atlas_argus_active_case_member(target_case_id text)
        RETURNS boolean AS $$
            SELECT EXISTS (
                SELECT 1
                  FROM case_member cm
                 WHERE cm.case_id = target_case_id
                   AND cm.reviewer_id = current_setting('atlas_argus.reviewer_id', true)
                   AND cm.is_active
            )
        $$ LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public;
        """
    )
    op.execute("ALTER TABLE case_member ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY rls_case_member_current_reviewer
            ON case_member
            USING (atlas_argus_active_case_member(case_id))
            WITH CHECK (atlas_argus_active_case_member(case_id))
        """
    )
    for table, case_expr in CASE_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {_policy_name(table)}
                ON {table}
                USING (atlas_argus_active_case_member({case_expr}))
                WITH CHECK (atlas_argus_active_case_member({case_expr}))
            """
        )
    op.execute("ALTER TABLE review_decision ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY rls_review_decision_active_case_member
            ON review_decision
            USING (
                EXISTS (
                    SELECT 1
                      FROM conflict c
                     WHERE c.id = review_decision.conflict_id
                       AND atlas_argus_active_case_member(c.case_id)
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1
                      FROM conflict c
                     WHERE c.id = review_decision.conflict_id
                       AND atlas_argus_active_case_member(c.case_id)
                )
            )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS rls_review_decision_active_case_member ON review_decision")
    op.execute("ALTER TABLE review_decision DISABLE ROW LEVEL SECURITY")
    for table, _case_expr in reversed(CASE_SCOPED_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS rls_case_member_current_reviewer ON case_member")
    op.execute("ALTER TABLE case_member DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS atlas_argus_active_case_member(text)")
