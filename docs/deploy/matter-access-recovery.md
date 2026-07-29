# Matter access recovery (break glass)

Use this only when a matter has become unreachable for everyone. It restores an
active Senior Aviation Counsel membership so ordinary administration can resume,
and it refuses to run against a matter anybody can still reach.

## Why this is an operator command and not an API route

Matter administration is deliberately confined to the matter. Every membership
route requires the acting reviewer's own active membership, so a global account
administrator cannot quietly add themselves to a matter they were walled off
from. That wall is the product's ethical-screen guarantee, and it is why there
is no in-app escape hatch.

Row-level security enforces the same boundary at the database. To the runtime
role, a matter it holds no active membership on does not exist:

```
case_file rows visible  : 0
case_member rows visible: 0
INSERT INTO case_member : blocked by row-level security policy
```

An API endpoint therefore could not even determine whether a matter is stranded
— it cannot distinguish "nobody can reach this" from "somebody else's matter" —
and could not write the membership row if it tried. Granting the runtime role a
privileged function that writes `case_member` rows would hand the same power to
anyone holding the runtime credential, which
[gap 3](../production-readiness.md) explicitly does not claim to contain, and
would let that holder strand a matter first in order to claim it.

The migration-owner credential is the correct trust boundary: it already
bypasses RLS, it is held by the operator rather than the running service, and
using it requires deliberate access to the deployment host.

## When a matter becomes stranded

A matter is stranded when it has no active membership backed by an active
reviewer account.

- **Reachable through ordinary administration.** Memberships are removed down to
  one administrator, and that administrator's reviewer account is later
  deactivated on offboarding. The memberships still read active, but nobody
  behind them can sign in. Reactivating the departed account would also clear
  the condition — this command exists so that recovery does not require
  re-enabling a former employee's credentials.
- **Not reachable through the API, but reachable operationally.** Every
  membership inactive. The membership routes cannot produce this (an
  administrator is refused when removing their own access), but a partial data
  import, a botched bulk edit, or a restore of a backup taken mid-change can.
  Nothing in the API can undo it.

## Run the restore

Supply the migration-owner connection, not the runtime one. The command asserts
that its connection really owns the application schema and refuses otherwise, so
a runtime-role connection cannot "prove" a matter stranded through its own RLS
blindfold.

```bash
ATLAS_ARGUS_MIGRATION_DATABASE_URL=postgresql+psycopg://atlas_owner:...@db:5432/atlas_argus \
  python -m atlas_argus.db.restore_matter_access \
    --case-id case-production-001 \
    --username mokafor
```

The named reviewer must be an active account whose **global** role is Senior
Aviation Counsel, because whoever regains the matter has to be able to re-admit
everyone else. They are granted — or returned to — an active Senior Aviation
Counsel membership on that matter alone. No other membership is changed.

Refusals are reported on stderr with exit status 1 and change nothing:

| Refusal | Meaning |
| --- | --- |
| `Matter … is not stranded` | Somebody can still reach it; use the administration UI |
| `Matter not found` | No such matter identifier |
| `Reviewer not found` | No such username |
| `Reviewer … is deactivated` | Reactivate the account first, or name a different Senior |
| `holds the global role …` | Not a global Senior Aviation Counsel |

## Afterwards

1. The restore appends a `matter access restored` event to that matter's
   append-only audit chain. The actor is recorded as the **database operator**
   with a null reviewer id, not as the reviewer who regained access —
   attributing an out-of-band act to a reviewer who did not perform it would
   corrupt the attribution the audit trail exists to prove. Verify the chain
   through `GET /api/cases/{case_id}/audit/verify` after signing in.
2. Have the restored reviewer re-admit the rest of the matter team through the
   normal administration UI, which writes the usual account-audit entries.
3. Record the incident, the operator identity, and the reason in the release or
   incident log. This is a privileged act outside the application's own
   authorization plane and should be reviewed like one.
