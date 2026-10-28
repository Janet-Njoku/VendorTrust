# API reference (Phase 1: vendors, questionnaires, assessments)

Interactive docs are served at `/docs` outside production. All vendor endpoints require a Supabase
access token: `Authorization: Bearer <jwt>`.

## Authentication

The API verifies the token against the project's published JWKS:

- Asymmetric signatures only (`ES256`, `RS256`); HS256 and `none` are refused, and the token must
  carry a `kid`.
- `iss` must equal the project issuer, `aud` the configured audience (`authenticated`), `exp` and
  `sub` are required, `nbf` is honoured, with 30 seconds of clock leeway. Anonymous sessions are
  refused. `sub` must be a UUID.
- Configuration (process environment, none of it secret): `VT_SUPABASE_URL` is enough. The JWKS
  URL, issuer and audience derive from it; override with `VT_SUPABASE_JWKS_URL`,
  `VT_SUPABASE_JWT_ISSUER`, `VT_SUPABASE_JWT_AUDIENCE`. URLs must be `https` (plain `http` only
  for localhost). These are read from the process environment, not from `api.env`.

The token proves **who** the caller is. It never decides what they may do.

## The signed-in user: `GET /me`

The web app's first call after sign-in. Any user with a valid token may call it, whether or not they
have access to anything:

- It **provisions** the caller's `app_users` row if missing (their own, active row only, enforced by the
  database). It never creates a membership, so a newly registered user still gets `403 No active
  membership` from every data endpoint until an administrator, or the one-time bootstrap, gives them a
  role.
- It returns `{user_id, email, status, memberships, active, has_access, needs_organization, mfa}`: the
  user's active memberships (organization ids and roles), the one resolved for this request (`X-Org-Id`
  selects among them), and the MFA state `{required, satisfied, level}`.
- It is **not** subject to the MFA requirement, so a user who has not completed MFA can learn they must.
- An account whose token has no email gets `403 Your account has no email address`.

## Multi-factor authentication (aal2)

Supabase access tokens carry the authenticator assurance level in the signed `aal` claim. For the internal
roles (`org_admin`, `analyst`, `reviewer`, `auditor`) every data endpoint requires exactly `aal2` (a
completed MFA challenge); otherwise it answers `403` with `Multi-factor authentication required` and
`WWW-Authenticate: Bearer error="insufficient_user_authentication"`. The role comes from the database
membership and the level from the verified token (a forged claim fails signature checking). `vendor_user`
accounts are exempt. Enforcement is on by default (`VT_ENFORCE_MFA_INTERNAL`) and **cannot be disabled when
`VT_ENVIRONMENT=production`**, which also refuses plain-http authentication URLs. The check comes after the
membership check, so it reveals nothing about vendors or questionnaires.

## Organization, role and vendor scope

After authentication the API calls `vt.begin_request(user_id, org_id)`. The database returns the
caller's own live membership and derives **organization, role and vendor scope from that row**;
JWT claims such as `role` or `app_metadata`, and any role or organization in a body, are ignored.
A user in several organizations chooses one with the `X-Org-Id` header; only an organization they
belong to is accepted. Everything then runs in one transaction under row level security, rolled
back unless the endpoint commits, so the context cannot reach another request.

## Endpoints

| Method and path | Roles | Notes |
|---|---|---|
| `POST /vendors` | `org_admin`, `analyst` | Body `{name, status?}`; `201` with the vendor. The organization comes from the membership, never the body |
| `GET /vendors` | all roles | Query `limit` (1 to 100, default 50), `offset`, `status`; ordered by name. A `vendor_user` sees only their vendor |
| `GET /vendors/{id}` | all roles | `404` if not visible to the caller |
| `PATCH /vendors/{id}` | `org_admin`, `analyst` | Body with at least one of `name`, `status` |

Vendor: `{id, org_id, name, status (active|archived), created_at, updated_at}`. `name` is trimmed,
1 to 200 characters, no control characters. Unknown fields are rejected (`422`). Creates and
updates write an `audit_log` event in the same transaction.

## Questionnaires (configurable, versioned)

Read: `org_admin`, `analyst`, `reviewer`, `auditor`. Write: `org_admin`, `analyst`. A `vendor_user`
gets `403` on all of these. Methodology and versioning rules: [scoring.md](scoring.md).

| Method and path | Purpose |
|---|---|
| `POST /questionnaires` | Create a template (with an empty draft version 1) |
| `POST /questionnaires/from-starter` | Copy the built-in 15-question starter as an editable draft |
| `GET /questionnaires` | List templates (`include_archived`, `limit`, `offset`) |
| `GET /questionnaires/{id}` | Template with its versions and question counts |
| `PATCH /questionnaires/{id}` | Rename, change description, or `status: archived` |
| `POST /questionnaires/{id}/versions` | New draft cloned from the latest published/retired version |
| `GET /questionnaire-versions/{id}` | Version with questions and options (`include_archived`) |
| `PATCH /questionnaire-versions/{id}` | Set `low_max`, `medium_max`, `high_max` (draft only) |
| `POST /questionnaire-versions/{id}/questions` | Add a question: `category` (slug), `prompt`, `weight` 1 to 10, `guidance?`, `options` (2 to 12, each `label` and `risk_points` 0 to 100 or `null` for not applicable) |
| `PUT /questionnaire-versions/{id}/order` | Reorder: every active question id exactly once |
| `PATCH /questions/{id}` | Edit, or `status: archived` (draft only) |
| `POST /questions/{id}/options`, `PATCH /options/{id}` | Add, edit or archive an option (draft only) |
| `POST /questionnaire-versions/{id}/publish` | Freeze a draft (needs questions, each with two active options) |
| `POST /questionnaire-versions/{id}/retire` | Stop using a version for new assessments |

Editing anything in a published or retired version returns `409`.

## Assessments

Read: `org_admin`, `analyst`, `reviewer`, `auditor`. Write: `org_admin`, `analyst`. A `vendor_user`
gets `403`.

| Method and path | Purpose |
|---|---|
| `POST /vendors/{vendor_id}/assessments` | Start one. Body: exactly one of `version_id` (a published version) or `questionnaire_id` (its latest published version). `201` |
| `GET /assessments` | List (`vendor_id`, `status`, `limit`, `offset`) |
| `GET /assessments/{id}` | Questions and options of its own version, current responses, `progress {answered, total}` |
| `PUT /assessments/{id}/responses/{question_id}` | Record or change an answer: `option_id`, `comment?` (while in progress) |
| `POST /assessments/{id}/score` | Calculate the score and finalize. `409` with `unanswered_question_ids` if incomplete |
| `GET /assessments/{id}/result` | `score` 0 to 100, `risk_level` (`low`, `medium`, `high`, `critical`) and the full `breakdown`; `409` if not scored yet |

Workflow: `in_progress` (answers can change) then `scored` (final, never changes again). Creation,
every response change (with before and after) and the scoring decision are audited
(`assessment.create`, `response.create`, `response.update`, `assessment.score`, and
`questionnaire.*`, `question.*`, `option.*` for configuration changes).

## Errors

Bodies are always `{"detail": "..."}` and never contain SQL, database messages, identifiers from
the database or token content.

| Status | Meaning |
|---|---|
| `400` | Invalid `X-Org-Id`, or the user has several organizations and sent none |
| `401` | Missing or invalid token (`WWW-Authenticate: Bearer`); the reason is never revealed |
| `403` | No active membership (including suspended, revoked, disabled user or suspended organization), `Multi-factor authentication required` for an internal role below `aal2`, or the role may not write |
| `404` | The resource does not exist **or belongs to another organization or vendor scope** (never `403`, so existence is not revealed) |
| `409` | A rule of the workflow refused it: editing a published questionnaire version, publishing one that is not ready, starting an assessment on an unpublished version, scoring an incomplete assessment, changing a scored one. The message is fixed text, never database text |
| `422` | Validation failure, or a response that does not belong to the assessment's own questions and options |
| `500` | Unexpected or database error, generic message only |
| `503` | Authentication not configured, or the JWKS could not be fetched |

## Defence in depth

Role checks in the API give clear errors; row level security enforces the same rules and the
organization boundary underneath. The list and get queries contain **no organization filter at
all**: tenant isolation comes from the database, and the tests prove it
(`tests/db/test_vendors_api.py`, including a test that disables the API's role check and shows RLS
still refuses the write). See [trust-boundary.md](trust-boundary.md) for the limits.

## Not available through the API (on purpose)

The first organization is created by an operator command, not an endpoint ([local-e2e.md](local-e2e.md),
[infra/db/RUNBOOK.md](../infra/db/RUNBOOK.md) Step 12). Invitations and membership endpoints do not exist
yet: until they do, an administrator grants memberships directly in the database. Delete is deliberately
absent; vendors are archived.
