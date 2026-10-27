# VendorTrust Architecture

Status: approved with refinements (Phase 0). Sections marked *(Phase N)* are design only until that phase.

## 1. Goals and scope

VendorTrust automates third-party security due diligence: onboarding, risk-based
questionnaires, evidence validation, control verification, residual risk, remediation and
audit-ready reporting. It demonstrates GRC automation, not formal certification: mapping a
control to ISO/IEC 27001:2022, SOC 2 or NIST CSF 2.0 never implies compliance or certification
(see [Framework mapping](#6-framework-mapping)).

## 2. Components

```
Browser (Next.js App Router, TypeScript, Tailwind)
   │  Supabase Auth session (httpOnly cookies via @supabase/ssr)  (Phase 1)
   ├──► Next.js server: presentation + session handling only; no business logic
   │         │  forwards user JWT
   ▼         ▼
FastAPI (Python 3.12, Pydantic v2, SQLAlchemy 2, Alembic)
   │  verifies JWT, resolves org + role + vendor scope, enforces authorization
   ├──► PostgreSQL (Supabase-hosted)
   ├──► Supabase Storage (private bucket, short-lived signed URLs)  (Phase 4)
   ├──► ClamAV (Docker in dev) for evidence scanning                (Phase 4)
   └──► Job runner: in-process initially behind a durable-job interface
```

| Component | Responsibility |
|---|---|
| `apps/web` | Internal dashboard and vendor portal UI. Never decides risk or access. |
| `apps/api` | All business rules: workflow, scoring, evidence stages, authorization. |
| `apps/api/app/risk` *(Phase 2)* | Pure, deterministic scoring engine with no I/O. |
| Postgres | System of record. RLS is defense in depth, not the primary control. |
| Storage | Private evidence bucket only. No public buckets, no unauthenticated URLs. |

## 3. Key decisions

1. **FastAPI owns business logic.** Scoring and workflow are testable with pytest and no UI.
2. **Authorization is enforced at the API layer.** Role and vendor scope come from our
   `memberships` table, never from client-controllable JWT claims.
3. **RLS is verified against the real connection model.** The API connects as a least-privilege
   application role. Supabase RLS keys off `auth.uid()` from the PostgREST/JWT context, which is
   *not* set on a direct SQLAlchemy connection. In Phase 1 we will decide and test one of:
   (a) API sets a per-transaction `app.org_id`/`app.user_id` setting that RLS policies read, or
   (b) RLS acts as a deny-all backstop for non-API roles while the API enforces scoping.
   Either way, cross-tenant and cross-vendor isolation tests run in CI. We do not assume RLS
   protects API queries until a test proves it.
4. **Methodology is configuration, not code.** Weights, thresholds and assumptions are versioned
   config; every stored score records the methodology version and a full breakdown.
5. **Durable processing by design.** Jobs go through an interface (`enqueue(job, payload)`) with
   an in-process implementation first; production swaps in a durable queue with retries and
   idempotency keys without changing callers.
6. **Typed contract.** FastAPI's OpenAPI schema generates the TypeScript client *(Phase 1)*.

## 4. Repository layout

```
apps/api/          FastAPI service (app/, tests/, later alembic/)
apps/web/          Next.js app
docs/              architecture, methodology, workflow, security, roadmap
.github/workflows/ CI for api and web
docker-compose.yml local dev services (ClamAV)
.env.example       documented placeholders; real .env is git-ignored
```

## 5. Assessment lifecycle and workflow

Onboarding → Inherent risk assessment → Questionnaire assignment → Vendor submission →
Evidence and control review → Residual risk assessment → Approval / conditional approval →
Remediation and reassessment.

Assessment states and permitted transitions (enforced in the API; each transition writes an
audit record with actor, timestamp, reason and before/after state):

| State | Entered when | Owner | Next |
|---|---|---|---|
| `onboarding` | Vendor record created | Assessment owner (analyst) | `inherent_assessed` |
| `inherent_assessed` | Inherent score and tier computed | Analyst | `questionnaire_assigned` |
| `questionnaire_assigned` | Tier-appropriate questionnaire issued to vendor | Analyst | `vendor_in_progress` |
| `vendor_in_progress` | Vendor opened the portal | Vendor | `submitted` |
| `submitted` | Vendor submits answers and evidence | Vendor | `in_review` |
| `in_review` | Reviewer assigned | Reviewer | `residual_assessed`, or `vendor_in_progress` (info requested) |
| `residual_assessed` | Residual score computed from verified controls | Reviewer | `approved`, `conditionally_approved`, `rejected` |
| `approved` / `conditionally_approved` | Authorized approver decision with rationale | Approver | `remediation`, `reassessment_due` |
| `remediation` | Open findings with owners and due dates | Analyst / vendor | `reassessment_due` |
| `reassessment_due` | Scheduled interval, material change, or evidence expiry | Analyst | `questionnaire_assigned` |

Rules: the assessment owner is recorded and changes are audited; a reviewer cannot approve an
assessment they authored answers for; a vendor user can only act on their own vendor's items;
every reviewer decision carries a mandatory note.

## 6. Framework mapping

- The **primary structure is a custom vendor security control library** (`controls`), each with
  an objective, a test procedure, and acceptable evidence types.
- **Mappings are separate data** (`framework_mappings`: control ↔ framework reference) targeting
  ISO/IEC 27001:2022 Annex A, SOC 2 Trust Services Criteria and NIST CSF 2.0. Mapping rows never
  appear in scoring code paths; scoring reads only control-level results.
- Reports state: "Controls are mapped to these frameworks for reference. This assessment does not
  constitute certification, attestation or a compliance determination."
- Framework reference text is paraphrased identifiers and titles only; no copyrighted standard
  text is reproduced.

## 7. Data model (summary, built across phases)

Tenancy and identity: `organizations`, `users`, `memberships(role, vendor_id?)`.
Vendors: `vendors`, `vendor_profiles`, `vendor_contacts`.
Controls: `controls`, `framework_mappings`, `questionnaire_templates`, `questions`
(`applicability_rule`), `question_control_links`.
Assessments: `assessments` (state, owner, methodology_version), `assessment_answers`,
`assessment_scope` (systems, products, locations in scope).
Evidence (three stages): `evidence_files` (received), `evidence_validations` (automated results,
per check), `control_verifications` (reviewer decision per control, per assessment).
Control assessment: `assessment_controls` holding `implementation_status`,
`evidence_confidence` (derived) and `verified_effectiveness` (reviewer-set), kept as separate
columns.
Risk: `risk_scores` (append-only, with breakdown JSON and methodology_version), `findings`,
`remediation_actions`, `risk_acceptances` (approver, justification, expiry, assessed exposure).
Governance: `audit_log` (append-only; no UPDATE/DELETE grants), `reports`.

All tenant tables carry `org_id`; vendor-scoped tables carry `vendor_id`.

## 8. Testing strategy

- Scoring engine: pure-function unit tests, property tests for monotonicity and bounds,
  golden-file tests against fixed methodology versions.
- Authorization: automated cross-tenant and cross-vendor isolation tests on every endpoint.
- Evidence pipeline: corpus of malformed, mislabeled, oversized, archive-bomb and EICAR files.
- CI gates: ruff, mypy (strict), pytest with coverage, ESLint, TypeScript check, dependency audit.
