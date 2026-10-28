# Trust Boundary and Row Level Security Context

How tenant context reaches PostgreSQL, what is trusted, and what this design does **not**
protect against. Every claim below is backed by a test (see the last section).

## 1. Context model

Policies read transaction-local settings that the API sets on each request's database
transaction. They are **claims made by the API process, not authenticated identity**: the database
cannot verify a JWT, so the names say so (`vt.claim_*`).

| Setting | Meaning | Source (and only source) |
|---|---|---|
| `vt.claim_user_id` | The user the API says it authenticated | `sub` of a JWT verified against our configured JWKS |
| `vt.claim_org_id` | Active organization | A row in `memberships` that the **database** returned for that user |
| `vt.claim_role` | Role in that organization | The same membership row |
| `vt.claim_vendor_id` | Vendor scope (vendor users only) | The same membership row |

**Never trusted from the client:** any organization, role, vendor ID or permission in a request
body, query string, header value, or JWT claim (`app_metadata`/`user_metadata`/`role` are
ignored). The `X-Org-Id` header only *selects among* the user's own database-resolved
memberships; an ID that is not one of them is rejected.

The API never writes the settings itself. It calls `vt.begin_request(user_id, org_id)` (Python:
`app.db.context.begin_request`), which sets only the user claim, reads **that user's own**
membership rows (the `memberships_select_self` policy), derives org, role and vendor from the
chosen row, sets them with `set_config(name, value, true)`, and finally confirms them with
`vt.claim_matches_active_membership()`. It refuses with `VT001` (no live membership; identical for
"not a member" and "no such organization") or `VT002` (several organizations, choose one).

Every policy on tenant data also requires `claim_matches_active_membership()`, so a claim alone
grants nothing beyond the user's own rows (their own `memberships` and `app_users` row, which is
what makes the first resolution step possible).

### The one SECURITY DEFINER function

`vt.claim_matches_active_membership()` answers one question: *is the claimed (user, organization,
role, vendor) a live membership of an active user in an active organization?*

- **Owner:** `vt_migrator`, the role that runs the migrations. Not a superuser, no `BYPASSRLS`, and
  bound by `FORCE ROW LEVEL SECURITY` like any owner. No superuser-owned security helpers exist.
- **Narrow:** no arguments, boolean result, SQL language, no dynamic SQL, no writes, `search_path`
  pinned to `pg_catalog, pg_temp` with every object schema-qualified, `EXECUTE` revoked from
  `PUBLIC` and granted to `vt_app` only. A test pins the function's source hash.
- **Why there is no recursion:** inside the function the current role is `vt_migrator`, so only
  policies `TO vt_migrator` apply. There are exactly three, `USING (true)` for `SELECT` on
  `organizations`, `app_users` and `memberships`, and they call no function. `vt_app`'s policies,
  which do call the function, never apply inside it. The policy graph is acyclic by construction:
  `memberships` policies reference only claim readers and the function; `app_users` reads
  `memberships`, never the reverse. **Nothing depends on policy evaluation order or function
  cost**, and tests rename policies into reverse order and set every function's cost to 1 and
  to 1,000,000 and get identical results.
- **Security implications:** if the function were wrong or its owner compromised it would run with
  the owner's rights, which is why the owner has no extra attributes. A holder of the application
  credential can use it as a yes/no oracle for a chosen (user, organization, role) triple.
  `vt_migrator` can read the three membership tables through the `*_ctx_read` policies (it is the
  migration role, not used by the API).

## 2. Properties we rely on (and verify)

1. **`FORCE ROW LEVEL SECURITY` binds table owners.** Without it the owner bypasses policies.
2. **No context fails closed.** Claims are read by `vt.claimed_*()`, which return NULL for a
   missing, empty or malformed value (UUIDs must be lowercase and well formed; the role must be
   one of the five), so such a claim matches no rows and never raises.
3. **Transaction-local settings reset at commit/rollback.** Observed behaviour: on the same
   session the setting then reads as `''` (not NULL), hence the `NULLIF`. A session-level
   `set_config(..., false)` *persists* and leaks across requests on a pooled connection (a
   negative-control test shows it), so the API only ever sets context through `begin_request`.
4. **`BYPASSRLS` skips policies but not grants.** `service_role` and similar roles ignore every
   policy, which is why the API never connects as one. They still need explicit grants, and
   `bootstrap.sql` gives them none in `vt`.
5. **The Data API roles (`anon`, `authenticated`) cannot reach `vt`**, so a leaked publishable
   key reaches none of our data.

## 3. Limitations (read before relying on RLS)

- **The API process sets the context, so RLS is a backstop, not an independent authority.**
  It stops bugs in our queries (missing org filter, ID guessing, a forgotten `WHERE`). It does
  not stop a compromised API process, which can set any context. Limits on that blast radius:
  `vt_app` has no DDL, no `TRUNCATE`, no `UPDATE`/`DELETE` on the audit log, owns nothing, and
  cannot assume other roles.
- **A compromised application credential is not defended against.** Whoever holds the `vt_app`
  credential can call `begin_request` for any user id they know and act with that user's reach
  (tests: `test_limit_*`). What bounds it: only that user's own organization and role are
  reachable; user ids are random UUIDs and cannot be enumerated (an unknown id gets `VT001`); the
  role cannot change policies, triggers, grants, RLS settings or ownership, cannot `SET ROLE`, and
  has no `DELETE`/`TRUNCATE`. It *can* write audit events as the impersonated user, change its own
  role-level defaults (timeouts are defaults, not enforcement), and use the membership helper as
  an oracle. Reduce that risk elsewhere: protect the credential (above), restrict network access,
  rotate, monitor.
- **The audit log is append-only for the application role, not immutable.** `vt_app` has no
  `UPDATE`/`DELETE`/`TRUNCATE` and triggers block accidental changes, but the table owner and any
  superuser can disable triggers and rewrite rows (`test_audit_log_triggers_block_even_the_superuser...`).
  Tamper evidence (the hash chain) is deferred.
- **Revocation timing.** In `READ COMMITTED` (the default) a suspended or revoked membership stops
  working at the very next statement, even inside an open transaction. Under `REPEATABLE READ` the
  snapshot is stale until the transaction ends; the API uses the default.
- **Foreign keys reveal existence.** Referential-integrity checks bypass RLS, so an `org_admin`
  inserting a membership for a user id gets a different error when that id does not exist.
- **Custom settings are not secret or tamper-proof.** Any SQL executed on the connection can
  call `set_config`. Never accept SQL fragments or run untrusted queries on this connection.
- **Settings only protect what has policies.** A table without RLS and a policy is unprotected;
  a catalog guard test fails CI for any table in `vt` missing RLS, FORCE or policies.
- **Connection poolers.** Session mode is supported. Transaction mode also works for
  transaction-local settings, but breaks prepared statements and session state; we avoid it.
  The reset test (property 3) must pass on whichever mode production uses.
- **Role changes apply at the next request**, because memberships are re-read every request.
  A JWT stays valid until it expires (default one hour), but the membership lookup means a
  suspended or revoked user is denied immediately.
- **The administrative database role is the trust root.** It can assume `vt_app` and
  `vt_migrator`. Its credential stays out of application and CI environments.
- **RLS is coarse.** It enforces the organization and vendor boundary and a read/write class.
  Action-level permissions (who may verify a control, approve a risk) are enforced in the API.

## 4. Credential loading

The two database credentials (`vt_app`, `vt_migrator`) are the keys to the boundary above, so how
they reach the process is part of the trust boundary.

- **Outside the repository.** `~/.config/vendortrust/api.env` and `migrations.env`, never a file
  in the git tree. The loader refuses a path that resolves inside the repository.
- **On demand only.** `app.credentials.load_database_credential(role)` is called when database
  functionality needs a URL. Importing `app.main`, building `Settings` or calling `get_settings()`
  never reads a credential file; `Settings` reads only `VT_*` process variables and holds no URL.
- **Secure reads.** Folder owned by the current user and not writable by others (`700`
  recommended, `755` accepted with a warning); file owned by the current user with mode `600`;
  neither may be a symlink; opened with `O_NOFOLLOW` and inspected through the open descriptor;
  at most 64 KiB, UTF-8.
- **Strict format.** `NAME=value`, comments, optional quotes; no `${VAR}` or command expansion,
  no `export`, no duplicates; a per-role allowlist of names, so the migration URL cannot sit in
  `api.env` and nothing else can sit in `migrations.env`.
- **Role separation.** The application role's URL must use user `vt_app[.ref]`, the migration
  role's `vt_migrator[.ref]`. A process holding one role's URL in its environment refuses to load
  the other's. Remote hosts need `sslmode=require` or stronger.
- **Priority.** Explicit file argument, then process variable, then `VT_ENV_FILE` /
  `VT_MIGRATION_ENV_FILE`, then the default file. **Production never reads the default folder.**
  A missing default file is a safe "not configured" state; a missing explicit file is an error.
- **Safe output.** Errors name the file, a line number or a variable name, never a value or a
  URL. URLs are held as `SecretStr`; scripts print only `user@host:port/db`; driver errors are
  redacted. Nothing is logged.

Limits: the loader cannot see macOS ACLs (check with `ls -le`); a process that can read your
files as you can read the credentials (Claude Code's sandbox and credential guard exist for that
reason, see CLAUDE.md); and once loaded, the URL is in the process's memory.

## 4a. First organization, first login and MFA

- **Bootstrap is an operator action, not a feature.** `scripts/bootstrap_org.py` runs with the migration
  credential (outside the repo, loaded by the same hardened loader) and has no HTTP surface. The
  administrator is proven by signing in as them; the command is a dry run unless `--commit`.
- **One-time, enforced by PostgreSQL** (migration 0006). `vt_migrator` is otherwise read-only on tenant
  tables (FORCE RLS). It gets exactly four INSERT policies, each open only in the bootstrap window: a user
  row and the organization (only while no organization exists), one active `org_admin` membership (only
  while exactly one organization and no membership exist), and one `bootstrap.first_organization` audit
  event (only while none exists). After the first run every condition is false, so even a modified script
  cannot create a second organization or grant anyone a role. The helpers are plain SECURITY INVOKER
  functions executable by `vt_migrator` only: no new definer surface. Concurrent runs are serialized by an
  advisory lock, and a test runs two at once.
- **Limit:** whoever holds the migration credential (the operator) can do this once, and can always act as
  the database owner in other ways. That credential stays outside the repo and out of the API's process.
- **First login provisions a user row, never access.** `GET /me` inserts only the caller's own active
  `app_users` row (policy `app_users_insert_self`, columns `id` and `email`). A membership needs an
  administrator. Anyone who can register with the identity provider can therefore create a user row (an
  email address); that is accepted and bounded to one row per account.
- **MFA.** Internal roles need an `aal2` token on every data endpoint. The `aal` claim is covered by the JWT
  signature, and any value other than exactly `aal2` counts as `aal1`. Enforcement cannot be turned off in
  production. The local identity stand-in (`scripts/dev_auth.py`) is loopback-only and refuses production,
  and the API refuses plain-http authentication URLs in production, so it cannot be used to forge `aal2`
  against a deployed API.

## 5. Test map

| Claim | Test |
|---|---|
| Roles and schema as designed; verifier detects violations | `tests/db/test_bootstrap.py` |
| Properties 1 to 4 | `tests/db/test_pg_assumptions.py` |
| Property 5 | `test_supabase_data_api_roles_cannot_reach_vt` |
| Env templates hold no secrets or forbidden variables, and parse with the loader's parser | `tests/test_env_templates.py` |
| Strict parser; owner, mode, symlink, folder and in-repo checks; no value in any message | `tests/test_envfile.py` |
| URL role family and TLS rules; redaction | `tests/test_dburl.py` |
| Sources, precedence, production, role separation, output hygiene | `tests/test_credentials.py` |
| No credential file is read on import or by `Settings` | `tests/test_settings_no_credentials.py` |
| The check script uses the loader and never prints a secret | `tests/test_check_db_script.py` |
| Tests cannot see the real HOME or inherited credential variables | `tests/test_isolation.py`, `tests/conftest.py` |
| Migrations: dry run by default, `commit=1` exact, role guard, downgrade guard, credential loader, offline SQL, models match the database | `tests/db/test_migrations.py`, `tests/db/test_context_helper.py` |
| Tenant isolation, role matrix, composite FKs, audit append-only, last-admin guard | `tests/db/test_rls_isolation.py` |
| Forged and malformed claims, suspended/revoked/disabled, `begin_request`, revocation timing, context cleanup on reuse, savepoints, autocommit, threads, prepared statements | `tests/db/test_rls_context.py` |
| No recursion by construction; independence from policy order and function cost; negative controls | `tests/db/test_rls_recursion.py` |
| RLS+FORCE on every table, exact grants, the single definer function (owner, search_path, ACL, pinned source), shadowing, limits of a compromised credential | `tests/db/test_schema_guard.py` |
| JWT checks (signature, algorithm, issuer, audience, expiry, `sub`), membership resolution, role matrix and tenant isolation through the API, errors without database detail, pooled-connection hygiene, restricted-role engine guard | `tests/db/test_vendors_api.py`, `tests/test_auth_settings.py` |
| Questionnaires and assessments: version immutability, one-way transitions, publishing readiness, assessment preconditions, final scoring, response integrity (triggers); composite foreign keys; RLS on the six new tables, role matrix, forged claims, no `DELETE` | `tests/db/test_questionnaire_db.py` |
| Questionnaire and assessment API: configuration, versioning, workflow, cross-tenant 404s, role matrix, RLS backstop, audit, historical integrity | `tests/db/test_questionnaire_api.py`, `tests/db/test_assessment_api.py` |
| Scoring rules, boundaries, rounding, N/A handling, determinism, starter content | `tests/test_scoring.py` |
| Bootstrap: the transaction, one-time policies (also against a modified script), concurrency, identity proof, the CLI, no secrets in output | `tests/db/test_bootstrap_org.py` |
| First-login provisioning without access, `/me`, MFA (`aal2`) on every data route, vendor exemption, forged and malformed `aal`, production guards | `tests/db/test_me_and_mfa.py`, `tests/test_auth_settings.py` |
| The whole product on the real stack: register, bootstrap, MFA, a second user locked out, a scored assessment, tenant isolation, audit | `tests/db/test_e2e_workflow.py` |
| The local identity stand-in (real HTTP, JWKS verified by the API's verifier, TOTP, loopback and production refusal) | `tests/test_dev_auth.py` |
| Invitations | Not implemented |
