# Threat Model

A working threat model for VendorTrust as built. It is the author's own analysis, **not** an independent
review or penetration test. Every mitigation names the code and the tests that exercise it; anything
without a reference is not claimed. For the mechanisms see [trust-boundary.md](trust-boundary.md) and
[security-architecture.md](security-architecture.md); for the status of every control see
[security-controls.md](security-controls.md).

## Scope, assets and actors

**Assets:** vendor records, questionnaire content, assessment answers and scores, the audit log, user
identities, and the two database credentials (`vt_app`, `vt_migrator`).

**Trust zones:** the browser and any network client are untrusted; the API process is trusted to set the
database context; PostgreSQL enforces the tenant boundary; the identity provider (Supabase Auth) is trusted
to authenticate and sign tokens; the operator holding the migration credential is trusted.

**Actors considered:** an authenticated user of another organization, a user with too low a role, an
anonymous caller, a holder of a stolen or forged token, an attacker who finds an application bug, a holder of
the application database credential, and the operator.

**Out of scope for this version:** denial of service and rate limiting, supply chain attacks beyond
lockfiles and audits, physical access, a compromised identity provider, and anything about evidence upload
(not built).

## 1. Tenant isolation

| Threat | Mitigation (implemented) | Evidence | Residual risk |
|---|---|---|---|
| A user reads or changes another organization's data through a query bug, a missing `WHERE`, or a guessed ID | Row level security with `FORCE` on every table; queries carry **no** organization filter, so a forgotten filter cannot matter. Composite foreign keys keep related rows in one organization | `tests/db/test_rls_isolation.py`, `test_schema_guard.py` (fails CI for any table without RLS, FORCE or policies), cross-tenant 404 tests in `test_*_api.py` | RLS is a backstop to the API, not an independent authority: it does not stop a compromised API process |
| The tenant context leaks across pooled connections | Context is transaction-local (`set_config(..., true)`) and set only by `vt.begin_request`; a session-level setting is shown by a negative-control test to leak | `tests/db/test_pg_assumptions.py`, `test_rls_context.py` (reuse, savepoints, threads, prepared statements) | Must be re-verified on whichever pooler mode a deployment uses |
| Forged or missing context grants access | Claims are validated (`vt.claimed_*()` return NULL when malformed) and every tenant policy also requires `vt.claim_matches_active_membership()`; no context fails closed | `test_rls_context.py`, `test_rls_recursion.py` | A holder of the application credential can claim any user id they know (see section 4) |
| Foreign keys reveal that an ID exists | Accepted | `trust-boundary.md` §3 | Existence oracle for user IDs by an `org_admin` |

## 2. Broken access control

| Threat | Mitigation (implemented) | Evidence | Residual risk |
|---|---|---|---|
| The client chooses its own organization or role | Organization, role and vendor scope come from the user's own membership row in the database; request bodies, headers and token claims (`role`, `app_metadata`) are ignored; `X-Org-Id` only selects among the user's own memberships | `app/deps.py`, `app/db/context.py`; `tests/db/test_vendors_api.py`, `test_rls_context.py` | None known |
| A low-privilege role performs a privileged action | Role matrix enforced in the API and, for the read/write class, by policy | role-matrix tests in `test_rls_isolation.py` and each `test_*_api.py` | RLS is coarse: action-level rules (who may verify or approve) live only in the API. There is no approval workflow or separation of duties yet |
| Registering with the identity provider grants access | `GET /me` creates only the caller's own `app_users` row; access needs a membership an administrator grants | `tests/db/test_me_and_mfa.py` | Anyone able to register can create one user row (an email address) |
| The first administrator is created by an open endpoint | There is no HTTP surface: an operator CLI creates the first organization, and migration 0006 lets the database permit it exactly once, even for a modified script | `tests/db/test_bootstrap_org.py` (including two concurrent runs) | The operator holding the migration credential can always act as database owner |
| Internal accounts are taken over with a password alone | `aal2` (MFA) is required on every data route for internal roles; exactly `aal2` passes; cannot be disabled in production | `tests/db/test_me_and_mfa.py`, `test_auth_settings.py` | No TOTP recovery codes; vendor users are exempt by design |
| Membership management and invitations | Not built; an administrator edits memberships directly in the database | | Operational risk, no API attack surface |

## 3. Token misuse

| Threat | Mitigation (implemented) | Evidence | Residual risk |
|---|---|---|---|
| Forged, unsigned or algorithm-confused tokens | Signature checked against the configured JWKS; only ES256 and RS256; `alg: none` and HS-confusion rejected | `app/auth.py`; `test_vendors_api.py`, `test_auth_settings.py` | Trusts the identity provider's keys |
| A token for another audience or issuer, or an expired one | `iss`, `aud`, `exp` and a UUID `sub` are required and checked | same tests | A stolen valid token works until it expires (default one hour) |
| A revoked or suspended user keeps access | Membership is re-read on every request, so access ends at the next request even though the JWT is still valid | `test_rls_context.py` (revocation timing) | None for data routes |
| A privileged claim smuggled in the token | `role`, `app_metadata` and `user_metadata` are ignored; only `sub`, `email` and `aal` are used | `tests/db/test_vendors_api.py` | None known |
| The development identity stand-in is used to forge MFA | Loopback only, refuses production; the API refuses plain-http auth URLs in production | `tests/test_dev_auth.py`, `test_auth_settings.py` | Not exercised against a hostile network |
| Tokens stolen from the browser | Session is held in `localStorage` (no cookies, so no CSRF) | `docs/frontend.md` | **XSS would expose it, and there is no Content Security Policy yet** |

## 4. Privileged database operations

| Threat | Mitigation (implemented) | Evidence | Residual risk |
|---|---|---|---|
| The application role bypasses RLS or alters schema | `vt_app` owns nothing, has no `BYPASSRLS`, no DDL, no `TRUNCATE`, no `UPDATE`/`DELETE` on the audit log, and cannot `SET ROLE`; the API refuses to start as a privileged role | `test_schema_guard.py` (exact grants), `test_bootstrap.py` | A holder of the `vt_app` credential can act as any user whose ID they know, write audit events as them, and use the membership helper as a yes/no oracle |
| The one privileged helper is abused | A single `SECURITY DEFINER` function: owned by a non-superuser without `BYPASSRLS`, no arguments, no dynamic SQL, pinned `search_path`, `EXECUTE` revoked from `PUBLIC`; its source hash is pinned by a test | `test_schema_guard.py`, `test_rls_recursion.py` | Wrong logic would run with the owner's rights |
| A migration or bootstrap is run by mistake | Dry run by default, explicit commit, role guard, downgrade guard | `tests/db/test_migrations.py`, `test_bootstrap_org.py` | Operator error against the wrong database is mitigated by a preflight, not prevented |
| Supabase's Data API reaches tenant data | `anon` and `authenticated` have no access to schema `vt`; the web app refuses secret or service-role keys | `test_supabase_data_api_roles_cannot_reach_vt`, `apps/web/src/lib/config.ts` tests | None known |

## 5. Secrets exposure

| Threat | Mitigation (implemented) | Evidence | Residual risk |
|---|---|---|---|
| Database credentials committed or logged | Stored outside the repo, owner-only files, strict loader, never printed (errors name a variable, never a value), `SecretStr` throughout; `Settings` holds no URL | `tests/test_credentials.py`, `test_envfile.py`, `test_dburl.py`, `test_settings_no_credentials.py`, `test_env_templates.py` | A process able to read your files as you can read them; loaded URLs sit in process memory |
| Secrets in git history | Ignore rules for real env files and keys; gitleaks in CI with an exact-value allowlist for one public test vector | `.gitignore`, `.gitleaks.toml`, `.github/workflows/security.yml` | Allowlist entries must stay exact |
| An AI coding agent reads credentials | A PreToolUse hook, deny rules and a sandbox, **only if registered locally** (see CLAUDE.md) | `tests/test_credential_guard.py` (proves the script only) | The hook is a text tripwire: paths built at run time are not seen. The sandbox is the real control |
| Vulnerable dependencies | Lockfiles; `pip-audit` and `pnpm audit --prod` in CI and weekly | `.github/workflows/security.yml` | No Dependabot yet; the audits cover known CVEs only |

## 6. Audit integrity

| Threat | Mitigation (implemented) | Evidence | Residual risk |
|---|---|---|---|
| The application edits or deletes audit history | `vt_app` has `INSERT`/`SELECT` only; triggers block `UPDATE`, `DELETE` and `TRUNCATE` | `test_rls_isolation.py`, `test_schema_guard.py` | A table owner or superuser can disable triggers and rewrite rows (shown by a test) |
| Published questionnaires or scored assessments are rewritten | Published versions are immutable and scored assessments final, by database triggers | `tests/db/test_questionnaire_db.py`, `test_assessment_api.py` | The same owner/superuser limit |
| Tampering cannot be detected | **Not implemented:** no hash chain or external log shipping | | Open |
| Events are missing | Audit rows are written for vendor, questionnaire, assessment and bootstrap actions; sign-in and read events are not logged, and there is no request logging | `app/audit.py` | Open |

## Highest remaining risks

1. No Content Security Policy while the session token lives in `localStorage`.
2. No rate limiting on the API.
3. The audit log is not tamper-evident.
4. Everything rests on protecting the `vt_app` and migration credentials.
5. No independent review, and the CI results above have only run on this repository's own pipeline.
