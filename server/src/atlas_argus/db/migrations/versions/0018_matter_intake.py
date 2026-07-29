"""in-app matter creation

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-29

Creating a matter is the one operation the runtime role cannot perform through
ordinary SQL, and the reason is structural rather than incidental: row-level
security on ``case_file`` and ``case_member`` both require the acting reviewer
to already be an active member of the matter, and a brand-new matter has no
members. The bootstrap is genuinely circular, which is why the only way to
create a matter until now was the owner-credentialed one-shot initializer.

``atlas_argus_create_matter`` is a deliberately narrow way out. It is
``SECURITY DEFINER``, so it runs as the schema owner and is not subject to
those policies — which makes its constraints the entire security boundary:

- **It cannot accept a case id.** The id is generated inside the function from
  a sequence, so a caller cannot aim it at an existing matter. This is the
  property that keeps it from becoming a way to join a matter you are not in;
  everything else is defence in depth.
- It creates the caller's own active Senior Aviation Counsel membership in the
  same statement, so a matter can never be created stranded (see the
  stranded-matter recovery note in production-readiness).
- The actor comes from the transaction-local reviewer context, not a
  parameter, so it cannot create a matter attributed to someone else.
- It verifies the actor is an active, global Senior Aviation Counsel.
- ``search_path`` is pinned, so the function body cannot be redirected at
  attacker-controlled objects.

What it does *not* protect against is a stolen runtime credential, which can
already set the reviewer context arbitrarily (a documented, pre-existing
limitation of RLS here). The consequence is bounded: such a credential could
create new, empty, audited matters. It still cannot read, join, or alter any
existing matter.

The audit event is deliberately NOT written here. It is appended afterwards by
the ordinary Python path, which owns the hash-chain logic — duplicating that
in PL/pgSQL would put the integrity chain in two places that could drift.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CREATE_MATTER_SQL = """
CREATE OR REPLACE FUNCTION atlas_argus_create_matter(
    p_name text,
    p_aircraft text,
    p_accident_date text,
    p_location text,
    p_matter_type text,
    p_status text,
    p_docket_ref text
) RETURNS text AS $$
DECLARE
    v_actor text;
    v_case_id text;
BEGIN
    v_actor := nullif(current_setting('atlas_argus.reviewer_id', true), '');
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'no reviewer context is set for matter creation';
    END IF;

    -- Defence in depth. The service layer checks this too; repeating it here
    -- means a direct call cannot skip it.
    IF NOT EXISTS (
        SELECT 1 FROM reviewer r
         WHERE r.id = v_actor
           AND r.is_active
           AND r.role = 'Senior Aviation Counsel'
    ) THEN
        RAISE EXCEPTION 'matter creation requires an active global Senior Aviation Counsel';
    END IF;

    -- Generated, never supplied: this is what stops the function being usable
    -- against a matter that already exists.
    v_case_id := 'case-' || nextval('matter_id_seq')::text;

    INSERT INTO case_file (
        id, name, aircraft, accident_date, location, matter_type, status, docket_ref
    ) VALUES (
        v_case_id, p_name, p_aircraft, p_accident_date,
        p_location, p_matter_type, p_status, p_docket_ref
    );

    -- Same statement, so a matter cannot come into existence unreachable.
    INSERT INTO case_member (id, case_id, reviewer_id, role, is_active)
    VALUES (
        'cm-' || replace(gen_random_uuid()::text, '-', ''),
        v_case_id,
        v_actor,
        'Senior Aviation Counsel',
        true
    );

    RETURN v_case_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;
"""


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS matter_id_seq")
    # gen_random_uuid() is core since PostgreSQL 13; this is a no-op safeguard
    # for databases restored from an older baseline.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(CREATE_MATTER_SQL)
    # Keep the sequence ahead of any ids already present, so a database that
    # was initialized with a hand-written matter id cannot collide.
    op.execute(
        """
        SELECT setval(
            'matter_id_seq',
            GREATEST(
                (SELECT count(*) FROM case_file),
                coalesce(
                    (SELECT max(substring(id from '^case-([0-9]+)$')::bigint)
                       FROM case_file
                      WHERE id ~ '^case-[0-9]+$'),
                    0
                )
            ) + 1
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS atlas_argus_create_matter("
        "text, text, text, text, text, text, text)"
    )
    op.execute("DROP SEQUENCE IF EXISTS matter_id_seq")
