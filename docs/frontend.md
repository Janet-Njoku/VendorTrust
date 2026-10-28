# Frontend

A Next.js 16 / React 19 / Tailwind 4 app in `apps/web`. It is a client of the API in
[api.md](api.md) and adds no server of its own: every screen reads and writes through one typed
client, and the backend (with row level security underneath) decides what each user may see.

## Screens

| Route | What it does | API used |
|---|---|---|
| `/login` | Supabase email and password sign-in and registration | Supabase Auth REST (public key only) |
| `/` | Dashboard: vendor totals, risk distribution, high-risk vendors, assessment progress, recent activity | `GET /vendors`, `GET /assessments` |
| `/vendors` | Register: search, filter by status and risk, add vendor | same, `POST /vendors` |
| `/vendors/[id]` | View, edit, archive or restore a vendor; its assessments; start an assessment | `GET/PATCH /vendors/{id}`, `GET /assessments`, `POST /vendors/{id}/assessments` |
| `/questionnaires` | List templates, create one, or create from the 15-question starter | `GET/POST /questionnaires`, `POST /questionnaires/from-starter` |
| `/questionnaires/[id]` | Versions, rename, archive, start a new draft | `GET/PATCH /questionnaires/{id}`, `POST .../versions` |
| `/questionnaires/[id]/versions/[versionId]` | Edit a draft (questions, options, weights, thresholds, reorder, archive), publish, discard; view a frozen version and clone it | `/questionnaire-versions/*`, `/questions/*`, `/options/*` |
| `/assessments` | List and filter, start an assessment | `GET /assessments`, `POST /vendors/{id}/assessments` |
| `/assessments/[id]` | Answer questions (each choice saved immediately), finalize and score; then the result with a weighted breakdown | `GET/PUT/POST /assessments/*` |

Loading skeletons, empty states, field validation (which mirrors the API's rules, with the API as the
authority), and error states are built in. Two API outcomes get dedicated screens instead of an error:
`403 No active membership` explains that an administrator must grant access, and `400` asking for an
organization shows a chooser (sent as `X-Org-Id`, which only selects among the user's own memberships).

## Access states, MFA and first login

After sign-in every screen sits behind an access check (`GET /me`, which also provisions the user's row).
It shows exactly one of: **set up or enter a two-factor code** (internal roles need `aal2`), **choose an
organization** (listing the user's own memberships), **no access yet** (registered with no membership;
signing in never grants one), **account disabled**, or the real pages. Enrollment uses Supabase's TOTP
factor endpoints with the user's own token and the publishable key: enroll, show the setup key and
`otpauth://` link (and the QR image only if Supabase returns an image data URL, the one kind rendered), then
challenge and verify; the response is a new `aal2` session that replaces the old one. An `MFA required`
answer from any page turns into the same screen.

## Things the UI does not know, on purpose

- **What each button will be allowed to do.** `/me` gives the role, but screens still show every action
  and explain a `403` inline; the server enforces roles twice (API and row level security).
- **An audit-log feed.** The API writes the audit log but does not expose it, so "recent activity" on the
  dashboard is derived from vendor and assessment timestamps and says so.
- **A dashboard endpoint.** Figures are computed from the list endpoints (`src/lib/dashboard.ts`).

## Authentication and secrets

Sign-in calls Supabase Auth's REST API with the project URL and the **publishable key only** (no
`@supabase/supabase-js` dependency was needed). The access token is sent as `Authorization: Bearer`,
refreshed shortly before it expires, and verified by the API against the project's JWKS. The session is
kept in `localStorage`, as the official client does, so rendering untrusted HTML must never be added
without a content security policy.

`src/lib/config.ts` refuses a secret or service-role key placed in `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
(`sb_secret_...`, or a JWT whose role is `service_role`): it is ignored, a configuration error is shown on
the login page, and the key is never rendered or logged. No database credential exists anywhere in the web
app. Responses are served with `nosniff`, `frame DENY`, a strict referrer policy and a restrictive
permissions policy (`next.config.ts`).

## Environment

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable key>
```

Copy `apps/web/.env.example` to `apps/web/.env.local`. Real use also needs the API running with its
`VT_SUPABASE_URL` set, and a user who has been given a membership (see below).

## Demo mode (screenshots only)

`NEXT_PUBLIC_DEMO_MODE=true pnpm web:dev` runs the interface against `src/lib/demo`, an **in-memory,
explicitly labelled** stand-in for the API with fictional vendors, a published starter questionnaire and
scored and in-progress assessments. It implements the same typed client interface and mirrors the
backend's workflow rules and messages, but it:

- makes no network request and needs no account (a test asserts `fetch` is never called);
- is only selected when the variable is exactly `true`; the real client is the default;
- shows a banner on every screen and on the login page;
- keeps data in memory, so a reload resets it.

`src/lib/demo/client.test.ts` also compares the demo's starter questions with
`apps/api/app/questionnaire_starter.py`, so the two cannot drift apart unnoticed.

## Running it for real

[local-e2e.md](local-e2e.md) walks the whole journey against the real API and database with a local
stand-in for Supabase Auth: register, see no access, bootstrap the first organization, complete MFA, add a
vendor, publish the starter questionnaire, run and score an assessment. Against a real Supabase project the
same steps apply once migrations 0001 to 0006 have been applied (still awaiting approval) and the operator
has run the bootstrap command ([infra/db/RUNBOOK.md](../infra/db/RUNBOOK.md) Step 12).

## Tests

`pnpm web:test` runs Vitest with Testing Library: the API client contract (every endpoint's method, path,
query and body, headers, error mapping), config and the secret-key guard, Supabase auth and token refresh,
dashboard calculations, validation, the demo client's rules, and user flows (dashboard, vendor register,
questionnaire editing and publishing, answering and scoring an assessment, navigation, login).
