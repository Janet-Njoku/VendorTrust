# Security Controls for Vendor Data and Evidence

Status: requirements baseline. Items are tagged with the phase that implements them. **Section 9 states
what the current code actually implements**; read it before relying on any item below.

## 1. Identity and authorization

- Supabase Auth issues JWTs; the API verifies signature, expiry, issuer and audience (Phase 1).
- Role, organization and vendor scope are read from our `memberships` table, not from token
  claims the client can influence.
- Deny-by-default RBAC with explicit per-endpoint permission checks. Vendor users reach only
  their own vendor's assessments and evidence.
- Separation of duties: assessment answers' author cannot verify the same controls; risk
  acceptance requires an authorized approver other than the requester.
- MFA required for internal users. Vendor invitations are single-use and expiring.

## 2. Tenant and vendor isolation

- *(Original plan: a scoped repository layer injects `org_id` into every query. The implementation instead
  lets row level security do this, and queries carry no organization filter; see section 9.)*
- Supabase RLS is **verified against the real connection model** before being relied on (see
  [architecture.md](architecture.md) section 4 and [trust-boundary.md](trust-boundary.md)). The API connects
  with a least-privilege role, never the service role.
- **Automated cross-tenant and cross-vendor tests** run in CI for every endpoint; a new endpoint
  without isolation tests fails review.

## 3. Evidence handling (highest-risk surface, Phase 4)

- Private storage bucket only. Never public buckets or unauthenticated URLs. Access via
  short-lived signed URLs (default 300 s) issued after an authorization check, and logged.
- Storage keys are generated (`org/vendor/uuid`); user-supplied filenames are metadata only.
- **Quarantine until scanned**: files are not downloadable or reviewable until ClamAV reports
  clean. Scan failure, timeout or unavailability leaves the file quarantined (fail closed).
- Allowlist by *detected* content type (magic bytes), not extension or client header. Size limit,
  no nested archives or archive bombs, macro-enabled and encrypted documents rejected.
- Served with `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`; never
  rendered inline from the application origin.
- SHA-256 recorded at receipt and re-verified when evidence is cited in a report.
- Per-vendor upload rate and quota limits.

## 4. Data protection

- TLS 1.2+ in transit; encryption at rest (managed Postgres/Storage).
- Data minimization: collect only what scoring and audit require. Retention and offboarding
  deletion are configurable per organization.
- Append-only `audit_log` for writes, permission changes, workflow transitions, reviewer
  decisions and evidence access. No UPDATE/DELETE grants on it.
- Logs are structured and redacted: no tokens, evidence content, or personal data.

## 5. Application hardening

- Pydantic validation on every input; parameterized queries only; output encoding in the UI.
- Explicit CORS origin allowlist (wildcard rejected in settings validation, already in Phase 0).
- API docs and OpenAPI schema disabled in production (already in Phase 0).
- Health endpoint exposes no version or configuration detail (already in Phase 0).
- Security headers and CSP on the web app; CSRF protection for cookie-authenticated mutations.
- Rate limiting on auth, invitations and upload endpoints.

## 6. Secrets and configuration

- No secrets in the repository. `.env` is git-ignored; `.env.example` holds placeholders only.
- The Supabase service-role key is server-side only and never given a `NEXT_PUBLIC_` name.
- Secret scanning in CI (Phase 0 workflow includes gitleaks).

## 7. Supply chain and delivery

- Lockfiles committed (`uv.lock`, `pnpm-lock.yaml`); dependency audit in CI.
- CI runs lint, type checks, tests, dependency audit and secret scanning on each PR.
- `main` should be protected with required checks (repository setting, to be configured by the
  owner). Workflow permissions default to read-only.

## 9. Implementation status

| Control | Status | Where |
|---|---|---|
| JWT verification: signature, issuer, audience, expiry, asymmetric algorithms only | Implemented | `app/auth.py`, [api.md](api.md) |
| Role, organization and vendor scope from the database, not from token claims | Implemented | `vt.begin_request`, [trust-boundary.md](trust-boundary.md) |
| Row level security, `FORCE`, least-privilege role, no `BYPASSRLS`, one narrow definer function | Implemented | migrations 0003 to 0005 |
| Tenant isolation by the database instead of a "scoped repository layer" | Implemented, **replaces** the repository-layer idea in section 2 | queries carry no organization filter by design |
| Cross-tenant and role tests on every endpoint | Implemented | `tests/db/test_*_api.py`, `test_rls_*` |
| MFA for internal users (`aal2`), including TOTP enrollment in the web app | Implemented; recovery codes are not | `app/deps.py`, [frontend.md](frontend.md) |
| Append-only audit log for the application role | Implemented for vendor, questionnaire, assessment and bootstrap events; **not immutable against a database owner or superuser**; no hash chain | `audit_log`, triggers |
| First organization created by an operator, once, enforced by the database | Implemented | migration 0006, `scripts/bootstrap_org.py` |
| Pydantic validation, parameterized queries, generic error bodies (no SQL or token echo) | Implemented | schemas, `app/errors.py` |
| CORS allowlist (wildcard rejected), docs off in production, minimal health endpoint | Implemented | `app/main.py`, `app/config.py` |
| Credentials outside the repository, strict loader, secrets never printed, secret-key refusal in the browser config | Implemented | `app/credentials.py`, `apps/web/src/lib/config.ts` |
| Secret scanning, dependency audits and lockfiles in CI | Configured; first run happens on the first push | `.github/workflows/` |
| Security headers on the web app | Partial: `nosniff`, `frame DENY`, referrer and permissions policy; **no Content Security Policy** | `apps/web/next.config.ts` |
| CSRF protection | Not applicable today: bearer tokens, no cookie authentication | |
| Rate limiting (sign-in is Supabase's; invitations and uploads do not exist) | **Not implemented** | |
| Separation of duties; risk acceptance by a separate approver | **Not implemented** (no approval workflow) | |
| Vendor invitations (single-use, expiring) and membership management endpoints | **Not implemented**; an administrator grants memberships directly in the database | |
| Evidence handling: private storage, ClamAV quarantine, content-type allowlist, hashing | **Not implemented** (Phase 4) | |
| Data retention and offboarding deletion per organization | **Not implemented** | |
| Structured, redacted logging | Minimal: database errors log only the error class; no request logging | `app/errors.py` |

## 8. Deferred (explicitly out of the first milestone)

External technical scanning of vendor domains, AI evidence summarization, SSO/SAML and advanced
integrations. When external scanning is added, it will require domain ownership verification,
SSRF protection and passive, rate-limited checks only.
