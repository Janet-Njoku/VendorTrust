# Database bootstrap

`bootstrap.sql` creates the two database roles and the `vt` schema that the whole security
model rests on (see [docs/trust-boundary.md](../../docs/trust-boundary.md)). It is applied
**once per database by an administrator, with psql**, never by the application, CI or Alembic.

**To run it on Supabase, follow the step-by-step [RUNBOOK.md](RUNBOOK.md).**

| File | Changes data? | Purpose |
|---|---|---|
| `preflight.sql` | No (`BEGIN READ ONLY`) | Confirms the target is the intended Supabase project and the admin can run the bootstrap |
| `bootstrap.sql` | Only with `-v commit=1` (one transaction) | Creates `vt_migrator`, `vt_app`, schema `vt`. **Rolls back by default**; commits only with exactly `-v commit=1` |
| `verify_bootstrap.sql` | No | Read-only catalog checks; every row must show `ok = t` |
| `dev/supabase-sim.sql` | Local only | Fake `anon`/`authenticated`/`service_role` and a non-superuser admin; refuses to run on a Supabase database |

All are run as `psql -X -v ON_ERROR_STOP=1 -f <file>`. The scripts also set `ON_ERROR_STOP`
themselves, so an error always aborts and rolls back.

## Safe by default

`bootstrap.sql` ends in `ROLLBACK` unless given exactly `-v commit=1`, re-checked at the moment
of commit. A misspelled option name (`comit`, `COMMIT`, ...) is just an unused variable, so the
script rolls back and says so. A `commit` value other than `1`, or the retired `dry_run` option,
is rejected before a transaction is opened.

## Roles

| Role | Used by | Can | Cannot |
|---|---|---|---|
| `vt_migrator` | Alembic only | Own and alter objects in `vt` | Superuser, bypass RLS, create roles/databases, touch `public` |
| `vt_app` | The running API | `USAGE` on `vt`; DML on tables a migration explicitly grants | Own objects, run DDL, bypass RLS, assume any role |

Table privileges are **never granted by default**: each migration grants exactly what each
table needs, so a new table is unreachable by the API until reviewed.

## What the bootstrap refuses to do

It validates the starting state first (changing nothing) and aborts if:

- schema `vt` exists with a different owner, contains objects not owned by `vt_migrator`, or
  grants privileges to roles other than `vt_migrator`, `vt_app` (and `anon`/`authenticated`,
  which it revokes). It never takes over an existing schema: there is no `ALTER ... OWNER`;
- `vt_app` or `vt_migrator` already exist but are not exactly `LOGIN` without SUPERUSER,
  CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS; are members of other roles; have members
  other than the admin; own objects they should not; or the admin has no `ADMIN OPTION` on them;
- the admin lacks CREATEROLE (or superuser) or CREATE on the database, or the server is older
  than PostgreSQL 16.

## Role membership handling

Creating a schema for `vt_migrator`, and `SET ROLE` to it, needs the admin to hold the SET
option on it. If the admin already has it (for example via `createrole_self_grant`), the script
changes nothing. Otherwise it grants SET **granted by the admin itself**, uses it, and reverts
exactly that grant (deleting the row it created, or removing only the SET option from a row that
existed before). Section 7 asserts the admin's SET option ends as it began. A pre-existing
membership is never revoked. Tests cover default, self-grant, pre-existing-row and superuser
cases, and prove a blanket `REVOKE` would be caught.

## What the admin role needs

PostgreSQL 16+, `CREATEROLE` (or superuser), `CREATE` on the database; for roles that already
exist, `ADMIN OPTION` on them. `preflight.sql` reports each. `dev/supabase-sim.sql` creates a
**non-superuser** admin with only `CREATEROLE` and `CREATEDB` (the Supabase `postgres` role is
not a superuser either), and the tests apply the bootstrap as that role. Only a dry run
(the default, no `commit` option) against the real project proves the Supabase role itself, with no
trace left.

## Verified locally before touching Supabase

```bash
scripts/dev-db.sh init          # PostgreSQL 17 on a private unix socket, no TCP listener
cd apps/api && uv run pytest    # includes tests/db/test_bootstrap*.py and test_preflight.py
```

The scenario tests run in a separate throwaway cluster so they can start from "no roles" or
from deliberately unsafe states without touching the shared dev cluster.

## Trust root

The administrative role (`postgres`) keeps `ADMIN OPTION` on `vt_app` and `vt_migrator` and can
therefore assume them. That is expected: its credential is the trust root. Keep it out of every
`.env` file, CI secret and process the application runs in.

## Teardown (destructive; dev projects only)

```sql
REASSIGN OWNED BY vt_migrator TO postgres;  -- or DROP SCHEMA vt CASCADE first
DROP OWNED BY vt_app;
DROP OWNED BY vt_migrator;
DROP ROLE vt_app;
DROP ROLE vt_migrator;
```
