# Phase 1 Design: Platform Core (approved)

Authoritative record of the approved Phase 1 security design and decisions. Detailed security
reasoning lives in [trust-boundary.md](trust-boundary.md) and the DB procedure in
[../infra/db/README.md](../infra/db/README.md).

## Decisions

| Topic | Decision |
|---|---|
| Connection model | FastAPI connects directly to Supabase Postgres as `vt_app` (psycopg 3 + SQLAlchemy). No PostgREST/`supabase-py` for data, no secret key, no `postgres` role in the app |
| Isolation | Dedicated schema `vt`; RLS enabled **and forced**; `anon`/`authenticated` have no access |
| Dev connection | Supabase session-mode pooler (IPv4), `sslmode=require`; subject to connectivity testing |
| Registration | Email-confirmed signup allowed; **no active membership means no application access**; nobody is auto-assigned admin |
| First org and admin | One-time CLI bootstrap, with explicit verification of the authenticated user's identity |
| MFA | TOTP enrollment and `aal2` enforcement for internal users in Phase 1, with a secure recovery and enrollment process |
| Audit hash chain | Deferred to the reporting phase |
| Workflow | Feature branch `feature/phase-1-platform-core` and a pull request; no direct pushes to `main` |
| Local database | PostgreSQL 17 (Homebrew) in an isolated project-local cluster |

## Roles (Phase 1)

`org_admin`, `analyst`, `reviewer`, `auditor` (read-only), `vendor_user` (single vendor). The
`approver` role arrives with the approval workflow.

## Secrets

Never stored in the repo, logs, chat or browser: the Supabase secret key, the legacy
`service_role` key, the legacy JWT secret and the `postgres` password. The API holds only
`VT_DATABASE_URL` (`vt_app`). Migration credentials live in a separate file read only by
Alembic. Web gets only `NEXT_PUBLIC_` values.

Both database credentials are kept **outside the repository**, in `~/.config/vendortrust/`
(`api.env`, `migrations.env`), and are loaded on demand by `app.credentials` (never on import):
strict `NAME=value` format with no expansion, owner-only files, a folder not writable by others,
no symlinks, never inside the repo, a per-role allowlist of names, role-family validation of the
URL, and no automatic default folder in production. See [trust-boundary.md](trust-boundary.md).

## Steps

| Step | Scope | Status |
|---|---|---|
| 1.0 | Supabase preparation: hardened bootstrap, read-only preflight, verification SQL, runbook, JWKS and connectivity check scripts, env template corrections | Local work done and tested; **running anything against Supabase awaits explicit approval** |
| 1.1 | Local PostgreSQL 17, DB test infrastructure, role/RLS assumption tests | Done |
| 1.0b | Secure external credential loading (`app.envfile`, `app.dburl`, `app.credentials`), scripts, templates, runbook, tests | Done (Alembic integration deferred to 1.3) |
| 1.2 | Settings, startup guard against privileged DB roles | Next |
| 1.3 | Alembic and baseline schema (RLS, FORCE, policies, grants, append-only audit trigger), `vt.begin_request`, one narrow SECURITY DEFINER helper | Implemented and tested locally; **applying to Supabase awaits explicit approval** |
| 1.4 | Request-scoped DB transaction and context helper | Done (`app/db/session.py`, `app/deps.py`) |
| 1.5 | JWT verification via JWKS | Done (`app/auth.py`) |
| 1.6 | Membership resolution, permission registry, `aal2` enforcement | Membership resolution and `aal2` enforcement done ([api.md](api.md)); a permission registry pending |
| 1.7 | Vendor, membership, invitation endpoints; audit; cross-tenant tests; CLI bootstrap | Vendor endpoints with audit and cross-tenant tests done ([api.md](api.md)); CLI bootstrap (migration 0006) and first-login provisioning done ([local-e2e.md](local-e2e.md)); membership endpoints and invitations pending |
| 1.7b | Configurable, versioned questionnaires; assessments; deterministic risk scoring (migrations 0004 and 0005) | Implemented and tested locally ([scoring.md](scoring.md), [api.md](api.md)); **applying to Supabase awaits explicit approval** |
| 1.8 | Web: login, dashboard, vendors, questionnaires, assessments and results ([frontend.md](frontend.md)) | Implemented and tested locally, including TOTP enrollment and challenge; recovery pending |
| 1.9 | CI Postgres service, required security suite, docs, live smoke checks | CI PostgreSQL service and the required security suite are configured (first run happens on the first push); documentation done; live smoke checks against Supabase pending approval |
