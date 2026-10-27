# Risk and Evidence Methodology

Status: design baseline, methodology version `v1` (to be implemented in Phase 2).
All numeric values in this document are **defaults held in versioned configuration**
(`methodology/v1.yaml`), not constants in code. Changing any weight, threshold or assumption
creates a new methodology version. Existing scores keep the version that produced them.

## 1. Principles

1. **Deterministic**: identical inputs and methodology version always give identical outputs.
   No randomness, no wall-clock reads inside the engine (the evaluation date is an input).
2. **Explainable**: every score stores a breakdown from inputs to result.
3. **Control-specific**: assurance is credited per control, never as blanket credit from a
   certificate or report.
4. **Separation of concerns**: implementation status, evidence confidence and verified
   effectiveness are distinct values with distinct sources.
5. **Humans decide control effectiveness**: automation validates documents; an authorized
   reviewer verifies controls.
6. **Acceptance is not mitigation**: accepting a risk records a decision; it does not lower the
   measured exposure.

## 2. Inherent risk (0–100)

Inherent risk estimates exposure *before* considering the vendor's controls, from the vendor
profile. Each factor `i` has a score `s_i` on an ordinal scale 1–5 and a weight `w_i > 0`.

```
normalized_i = (s_i - 1) / 4                      # 0.0 … 1.0
inherent     = 100 × Σ(w_i × normalized_i) / Σ(w_i)
```

Normalizing by `Σ(w_i)` guarantees 0 ≤ inherent ≤ 100 regardless of how weights are configured
(weights need not sum to 1 or 100). A vendor scoring 1 on every factor yields 0; 5 on every
factor yields 100.

Default factors (weights are relative):

| Factor | Default weight |
|---|---|
| Data sensitivity (public → regulated/special category) | 30 |
| Data volume | 15 |
| Business criticality / substitutability | 20 |
| Access (none → privileged/network) | 15 |
| Hosting and integration model, subprocessor depth | 10 |
| Regulatory / jurisdictional exposure | 10 |

**Tiering** (configurable thresholds): Critical ≥ 75, High ≥ 55, Medium ≥ 35, Low below 35.
**Override rules** (configurable, listed in the breakdown when they fire): e.g. regulated
payment-card or health data sets a minimum tier of High. An override changes the tier, never
the numeric inherent score, and the report shows both.

The tier selects the questionnaire scope (`applicability_rule` on questions) and which evidence
is mandatory.

## 3. Control-level assessment: three separate dimensions

For each applicable control in an assessment, three values are tracked independently:

| Dimension | Meaning | Source | Values |
|---|---|---|---|
| **Implementation status** | What the vendor *claims* | Vendor answer | `not_implemented`, `partially_implemented`, `implemented`, `not_applicable` (needs justification) |
| **Evidence confidence** | How well evidence *supports the claim* | Derived by the engine from evidence stages (§4) | `none`, `low`, `medium`, `high` |
| **Verified effectiveness** | Whether the control actually works for this scope | Authorized reviewer decision | `not_verified`, `ineffective`, `partially_effective`, `effective` |

Only **verified effectiveness** can grant full credit. A claim without verification earns capped,
configurable partial credit, so unverified attestation cannot drive a vendor to low residual risk.

### Evidence confidence derivation (default)

| Condition for the control | Confidence |
|---|---|
| No evidence received, or only attestation | `none` |
| Evidence received but not validated, or validation has failures | `low` |
| Evidence validated (§4 stage 2) but not yet reviewer-verified | `medium` |
| Evidence validated **and** reviewer-verified for this control | `high` |

Confidence is *derived*, never set by hand. It can drop when evidence expires or a new
validation fails.

## 4. Evidence: three distinct stages

A document moving through the stages is never automatically a passing control.

**Stage 1: Evidence received.** The vendor submitted a file. Recorded: uploader, time, hash,
size. The file is **quarantined** (not downloadable or reviewable) until malware scanning
succeeds. A failed or unavailable scan keeps it quarantined.

**Stage 2: Evidence validated (automated).** Checks that evidence is well-formed and applicable.
Each check stores `pass | fail | warn | not_applicable` plus details. Validation is evaluated
against **context**, not in isolation:

| Check | Context used |
|---|---|
| File integrity | Detected type vs. declared type, parseable, not encrypted/macro-enabled, hash recorded |
| Vendor match | Document names the vendor (fuzzy match with vendor legal name/aliases) |
| Assessment scope | Systems, products, locations covered by the document include those in the assessment scope |
| Reporting period | Period end within the freshness window; period covers the assessment date or is within the permitted gap |
| Control applicability | The evidence type is acceptable for the specific control it is linked to |
| Completeness | Required elements present (e.g. report type, period, opinion, exceptions section, signed/dated policy) |
| Exceptions | Noted exceptions or qualified opinions are surfaced as `warn` for reviewer attention |

Evidence is **linked to specific controls** (`evidence_control_links`) with the scope claimed. A
SOC 2 report validated for the vendor's hosting platform provides no confidence for controls
about a product or location it does not cover.

Stage 2 results are advisory signals plus gates: a `fail` on a required check prevents the
evidence from counting toward `medium`/`high` confidence.

**Stage 3: Control verified (human).** An authorized reviewer, who is not the author of the
vendor's answers, records for each control: `effective`, `partially_effective`, `ineffective` or
`needs_information`, with a mandatory rationale and the evidence relied on. The decision, actor
and timestamp are audited. Verification is per control and per assessment; it does not carry to
other controls or other assessments unless explicitly re-confirmed.

Freshness: each evidence type has a configurable validity window. On expiry the evidence stops
supporting confidence, dependent verifications are flagged for re-review, and reassessment may be
triggered.

## 5. Residual risk (0–100)

Residual risk estimates exposure *after* crediting verified control performance.

### 5.1 Credited effectiveness per control (default values, configurable)

```
credit(control) =
  verified_effectiveness == effective            → 1.00
  verified_effectiveness == partially_effective  → 0.50
  verified_effectiveness == ineffective          → 0.00
  verified_effectiveness == not_verified         → unverified_cap[implementation_status, confidence]
```

Default `unverified_cap` (deliberately low, and never above partial credit):

| Implementation \ Confidence | none | low | medium | high* |
|---|---|---|---|---|
| implemented | 0.10 | 0.20 | 0.35 | n/a |
| partially_implemented | 0.00 | 0.10 | 0.20 | n/a |
| not_implemented | 0.00 | 0.00 | 0.00 | n/a |

\* `high` requires verification, so it cannot coexist with `not_verified`.
`not_applicable` controls are excluded from both numerator and denominator, and require reviewer
acceptance of the justification; otherwise they are treated as `not_implemented`.

### 5.2 Aggregation

Each control has a weight `c_j` (control criticality × domain weight, configured per tier).

```
control_gap      = Σ(c_j × (1 − credit_j)) / Σ(c_j)          # 0.0 … 1.0 over applicable controls
residual_raw     = inherent × (1 − max_mitigation × (1 − control_gap))
```

`max_mitigation` (default 0.85) means even a perfect control set never reduces residual risk
below `inherent × 0.15`; inherent exposure from the nature of the relationship never reaches
zero. With `control_gap = 1` (nothing credited) residual equals inherent; residual never exceeds
inherent from controls alone.

### 5.3 Findings and floors

Open findings (ineffective or missing controls, failed validations) carry severities. Configured
**floors** set a minimum residual *rating*: e.g. any open `critical` finding implies rating no
lower than High, regardless of the numeric score. Floors are listed in the breakdown when they
apply.

Rating thresholds mirror tiering (configurable) and apply to the numeric residual after floors.

### 5.4 Risk acceptance

Accepting a finding records: approver (authorized role, not the requester), justification,
compensating controls (optional), approval date and **expiry**. It does **not** change
`control_gap` or the numeric residual. Reports show:

- residual risk (measured), and
- **accepted exposure**: the findings accepted, who approved, why, and when it expires.

Expired acceptances revert to open findings and notify the owner. Acceptances can be revoked.
Decision gating (e.g. conditional approval) may consult acceptance status; the *score* does not.

## 6. Outputs and explainability

Each `risk_scores` row (append-only) stores: kind (inherent/residual), score, rating,
methodology_version, evaluation date, and a `breakdown` JSON containing all factor inputs,
normalized values, per-control implementation status / confidence / verified effectiveness /
credit / weight, overrides and floors fired, and accepted-exposure references. Reports render
from this breakdown, and recomputing from stored inputs must reproduce the stored score exactly.

## 7. Required unit tests (Phase 2 acceptance)

- Normalization: all-1 → 0, all-5 → 100, scale-invariance of weights, bounds for random inputs.
- Weight/threshold edge values land in the documented tier; overrides change tier not score.
- Monotonicity: improving a control's credit never increases residual; worse factors never
  decrease inherent.
- Unverified claims cannot exceed their cap; certificates cannot lift unlinked controls.
- Residual ≤ inherent; residual ≥ inherent × (1 − max_mitigation).
- Risk acceptance leaves score unchanged; expiry reopens the finding.
- Floors fire and are reported; `not_applicable` handling; empty control set behavior defined.
- Determinism: same inputs and version, same output; golden files per methodology version.
- Evidence: wrong vendor, out-of-scope system, stale period, wrong control type, and failed scan
  each prevent confidence above `low`; a valid document alone never yields `effective`.

## 8. Limitations (stated in reports)

Scores are structured decision support derived from configurable assumptions and the evidence
supplied. They are not a prediction of incidents, a certification, or a compliance finding.
