# Run VendorTrust end to end on your machine

The real FastAPI backend, the real PostgreSQL database (migrated by Alembic, roles from
`infra/db/bootstrap.sql`) and the real web app, with no Supabase project and no mock data. Only the
identity provider is a local stand-in: `apps/api/scripts/dev_auth.py` emulates the few Supabase Auth
endpoints the app uses (password sign-in, refresh, TOTP multi-factor) and publishes a JWKS, so the API
verifies tokens exactly as it does against Supabase.

Everything here is throwaway: a unix-socket-only PostgreSQL cluster in `.local-data/` (git-ignored, no
passwords, no TCP listener), a database called `vendortrust_dev`, and a fake identity provider that binds
only `127.0.0.1` and refuses to run with `VT_ENVIRONMENT=production`. **Never point real data at it.** This
workflow does not read or need any real credential file.

> This is **not** demo mode. Demo mode (`NEXT_PUBLIC_DEMO_MODE=true`) swaps the whole API for an in-memory
> stand-in with fictional data, for screenshots. Here every request goes to the real API and database.

## Prerequisites (once)

```bash
brew install postgresql@17 uv pnpm      # plus Node 24 (see .node-version)
scripts/dev-db.sh init                  # creates and starts the local cluster
cd apps/api && uv sync && cd ../..
pnpm install
```

`scripts/dev-db.sh start` / `stop` start and stop the cluster later.

## 1. Create the database

```bash
scripts/dev-e2e.sh setup
```

Creates `vendortrust_dev`, applies `infra/db/bootstrap.sql` (roles `vt_app`, `vt_migrator`, schema `vt`)
and runs every migration (`alembic -x commit=1 upgrade head`). Safe to re-run. `scripts/dev-e2e.sh env`
prints the commands and environment variables for the four terminals below.

## 2. Start the three servers (three terminals)

```bash
# Terminal 1: the stand-in identity provider
cd apps/api && uv run python -m scripts.dev_auth serve          # http://127.0.0.1:54330

# Terminal 2: the API. It holds ONLY the application role's URL.
cd apps/api
export VT_ENVIRONMENT=development
export VT_SUPABASE_URL=http://127.0.0.1:54330
export VT_DATABASE_URL="$(../../scripts/dev-db.sh url vt_app vendortrust_dev | sed 's/^postgresql:/postgresql+psycopg:/')"
uv run uvicorn app.main:app --port 8000

# Terminal 3: the web app
cd apps/web
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54330 \
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=local-dev-key \
pnpm dev                                                         # http://localhost:3000
```

(`scripts/dev-e2e.sh env` prints these with the right socket path filled in.)

## 3. Register, and see that registering grants nothing

Open http://localhost:3000/login, choose **Need an account? Register**, and create `you@example.com` with a
password of at least 8 characters, then sign in. The app calls `GET /me`, which **provisions** your user
row and reports the truth: *"You do not have access to an organization yet."* Registering and signing in
never grant membership, and every data endpoint answers `403 No active membership`.

## 4. Bootstrap the first organization (once, as the operator)

This is a command-line step with the **migration** credential; there is no endpoint and no button. Use a
terminal that holds *only* the migration role's URL (the credential loader refuses a process that holds
both roles):

```bash
cd apps/api
export VT_SUPABASE_URL=http://127.0.0.1:54330
export VT_SUPABASE_PUBLISHABLE_KEY=local-dev-key
export VT_MIGRATION_DATABASE_URL="$(../../scripts/dev-db.sh url vt_migrator vendortrust_dev | sed 's/^postgresql:/postgresql+psycopg:/')"

uv run python -m scripts.bootstrap_org --org-name "Acme Ltd" --admin-email you@example.com
#   hidden password prompt, then: DRY RUN OK ... NOTHING WAS CHANGED
uv run python -m scripts.bootstrap_org --org-name "Acme Ltd" --admin-email you@example.com --commit
#   Bootstrap COMMITTED: organization ..., administrator ... (their user row already existed from first login)
```

It proves who the administrator is by signing in as them, creates the organization and an active
`org_admin` membership in one transaction, and records a `bootstrap.first_organization` audit event. Run it
again and it refuses (`already bootstrapped`): PostgreSQL itself closes the window (migration 0006), so even
a modified script cannot create a second organization.

## 5. Complete MFA, then use the product

Reload the web app. Because an `org_admin` is an internal role, the API requires an `aal2` token, so the app
shows **Set up two-factor authentication**:

1. Click **Set up authenticator**. The setup key and `otpauth://` link are shown (the stand-in has no QR
   image; add the key to any authenticator app, or run `uv run python -m scripts.dev_auth totp you@example.com`
   in `apps/api` to print the current code).
2. Enter the 6-digit code. The session upgrades to `aal2` and the dashboard opens.

Then, as in a real deployment: **Vendors** (add one), **Questionnaires** (create from starter, publish),
start an **Assessment** from the vendor page, answer the 15 questions, **Finalize and score**, and read the
weighted result on the Assessments page.

## 6. A second person

A second user can register and sign in, and will see the no-access screen. There are no invitations yet, so
an administrator grants a role by hand (they must have signed in once so their user row exists):

```bash
scripts/dev-e2e.sh grant ana@example.com analyst      # org_admin | analyst | reviewer | auditor
```

On their next sign-in they are asked for MFA (internal roles), then work within their role. `vendor_user`
accounts are exempt from MFA and see only their vendor; creating one needs a vendor id and is left to SQL.

## Clean up

```bash
scripts/dev-e2e.sh reset            # drops vendortrust_dev (only that database)
rm -rf .local-data/dev-auth         # forgets the local test users and signing key
scripts/dev-db.sh stop
```

## The same journey as an automated test

`apps/api/tests/db/test_e2e_workflow.py` runs steps 3 to 6 with no browser: real FastAPI app, real
PostgreSQL, the real bootstrap command, tokens from the stand-in over real HTTP and verified through its
JWKS, MFA enrollment, a second user who stays locked out, a full vendor assessment scored, tenant
isolation against a second organization, and the audit trail. `pnpm web:test` covers the interface side
(access gate, MFA enrollment and challenge, organization chooser).

## What is local-only, and what changes against Supabase

| Local | Against Supabase |
|---|---|
| `scripts/dev_auth.py` signs tokens with a throwaway key | Supabase signs them; the API reads its published JWKS (`VT_SUPABASE_URL`) |
| Any email registers instantly | Registration needs email confirmation |
| MFA code from `dev_auth totp` or any TOTP app | Supabase TOTP enrollment (`/auth/v1/factors`), which also returns a QR image |
| Socket cluster, no passwords | Credentials in `~/.config/vendortrust/*.env` (see the RUNBOOK) |
| Plain `http://127.0.0.1` auth URL | The API **refuses plain http** auth URLs in production |

Bootstrapping a real Supabase project follows the same command with `VT_SUPABASE_PUBLISHABLE_KEY` set to
the project's publishable key and the migration credential loaded from `~/.config/vendortrust/`
([infra/db/RUNBOOK.md](../infra/db/RUNBOOK.md), Step 12). Applying migrations 0001 to 0006 to Supabase
still needs its own explicit approval and the usual dry run, commit, verify sequence.
