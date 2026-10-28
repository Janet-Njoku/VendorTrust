# Portfolio Demo Guide

A route through VendorTrust for a reviewer with 5 to 15 minutes. Nothing here needs a Supabase account or any
real credential.

## 5 minutes: read

| Look at | To see |
|---|---|
| [README](../README.md) | The problem, the capabilities, the threat-to-control table |
| [security-architecture.md](security-architecture.md) | The request path and the role model on one page |
| [threat-model.md](threat-model.md) | Each threat, its mitigation, the test that proves it, and what remains |
| `apps/api/alembic/versions/0003_rls_grants_triggers.py` | RLS, `FORCE`, grants and audit triggers as code |
| `apps/api/app/deps.py`, `apps/api/app/auth.py` | JWT verification, MFA gate, membership resolution |
| `apps/api/tests/db/test_rls_isolation.py` | Tenant isolation and the role matrix against a real PostgreSQL |

## 5 minutes: see the interface

Demo mode serves fictional data held in the browser, with an amber `DEMO MODE` banner on every screen. It
contacts no server and is not evidence that the backend works.

```bash
pnpm install
NEXT_PUBLIC_DEMO_MODE=true pnpm web:dev      # http://localhost:3000 , then "Enter the demo"
```

Suggested path: dashboard, vendor register (search and filter), create a questionnaire from the starter,
open an in-progress assessment, open a scored assessment and its weighted breakdown.

## 5 minutes: run the proof

```bash
scripts/dev-db.sh init      # once, from the repository root: a throwaway local PostgreSQL 17
cd apps/api
uv run pytest tests/test_scoring.py tests/test_credential_guard.py -q     # no database needed
uv run pytest tests/db/test_rls_isolation.py tests/db/test_e2e_workflow.py -q
```

Without a local server the database tests skip locally and **fail** in CI. The full real-stack walkthrough
(first-organization bootstrap, MFA, a locked-out second user, a scored assessment) is
[local-e2e.md](local-e2e.md).

## Things worth asking about

- Why queries carry no `WHERE organization_id = ...` and what protects them instead.
- The one `SECURITY DEFINER` function: why it is safe, why it cannot recurse, how a test pins it.
- What RLS does **not** protect against ([trust-boundary.md](trust-boundary.md) section 3).
- How the first administrator is created with no endpoint, and why it works only once.

## Status, stated plainly

The migrations, bootstrap, authentication and MFA flow have been exercised by the author against a real
Supabase project (administrator onboarding, MFA, vendor creation, questionnaire publishing). The application is
not deployed, and the full assessment flow has not yet been validated there; the automated end-to-end test runs
against a local PostgreSQL with a local identity stand-in. Gaps are in the threat model.

## Screenshot checklist

No images have been captured yet, so nothing in the repository links to one. Capture them from your own
machine; the full plan and file names are in [screenshots/README.md](screenshots/README.md).

- [ ] Start demo mode and keep the `DEMO MODE` banner visible in every demo screenshot.
- [ ] 1440 x 900 window, light theme, PNG, no browser chrome, other tabs and bookmarks hidden.
- [ ] `01-dashboard.png`: totals, risk distribution, high-risk vendors.
- [ ] `02-vendor-register.png`: with a search and a risk filter applied.
- [ ] `03-questionnaire-editor.png`: a draft with weights and thresholds.
- [ ] `04-assessment-answering.png`: progress and saved answers.
- [ ] `05-assessment-result.png`: gauge, category bars, weighted breakdown.
- [ ] `06-mfa-setup.png` and `07-no-access.png`: from the local stack, using `you@example.com`-style accounts.
- [ ] Check each image for real emails, project URLs, tokens, QR codes or TOTP secrets, and retake it if any show.
- [ ] Save the files in `docs/screenshots/`, then add the images to the README's Screenshots section and
  update `docs/screenshots/README.md`. Do not link an image before it is committed; `tests/test_docs.py` fails if
  you do.
