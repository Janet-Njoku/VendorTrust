# Implementation Roadmap

Revised after architecture review. The first functional milestone (end of Phase 5) must
demonstrate real GRC automation: risk-tiered assessment, three-stage evidence validation,
reviewer-verified controls and explainable residual risk. External technical scanning, AI
evidence summarization and advanced integrations are deferred until the core workflow is
functional and tested.

| Phase | Scope | Exit criteria |
|---|---|---|
| **0. Foundation** | Monorepo, docs, tooling, CI, minimal FastAPI health endpoint, minimal Next.js app, env template, README | Lint, type checks and tests pass locally; no business features, no live Supabase |
| **1. Platform core** | Supabase Auth integration, organizations, memberships, RBAC, vendor/internal scoping, RLS connection-model verification, audit log, vendor CRUD, OpenAPI → TS client | Cross-tenant and cross-vendor isolation tests pass; RLS behavior verified and documented |
| **2. Onboarding and inherent risk** | Vendor profile intake, versioned methodology config, inherent scoring engine, tiering and overrides, assessment states 1–2, owner assignment | Scoring unit tests per [methodology §7](risk-methodology.md); recomputation reproduces stored scores |
| **3. Risk-based questionnaires** | Custom control library, framework mappings (separate), question applicability rules, tier-driven questionnaire assignment, vendor portal answering and submission | Onboarding → submitted assessment end to end; mappings carry no scoring logic |
| **4. Evidence validation and reviewer decisions** | Upload with ClamAV quarantine, stage 1–3 pipeline, context-aware validation, evidence-to-control links, reviewer verification, expiry | Malformed/mislabeled/EICAR corpus rejected; valid document alone never passes a control |
| **5. Residual risk, findings, remediation** | Control-specific residual engine, three-dimension control state, findings, remediation tracking, risk acceptance (no score reduction) with expiry, approval/conditional approval, reassessment triggers | Methodology tests green; closing a finding with verified evidence changes the score; acceptance does not |
| **6. Audit-ready reporting** | Per-vendor PDF/CSV from stored breakdowns, evidence hashes, mapping disclaimer, accepted-exposure section, auditor read-only role | Report reproduces stored scores and cites evidence hashes |
| **7. Dashboard and portal polish** | Portfolio dashboard (tier/residual distribution, overdue reviews, expiring evidence, open findings), vendor portal UX | Usability pass; accessibility basics |
| **8. Hardening and release** | Durable job queue for production, security review, load/abuse tests, demo data, deployment guide | Threat-model items closed or documented as accepted |
| **Deferred** | External technical scanning, AI evidence summarization, SSO/SAML, advanced integrations, public API | Revisit after Phase 8 |

The internal dashboard and vendor portal grow incrementally with Phases 1–6; Phase 7 is polish.
