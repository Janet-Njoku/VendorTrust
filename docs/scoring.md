# Vendor risk scoring and questionnaires

How an assessment turns questionnaire answers into a risk score. The rules are deterministic and
live in one small module, `apps/api/app/scoring.py`, with tests in `tests/test_scoring.py`. There is
no randomness, no model and no hidden input: the same answers always give the same result.

Method name (stored with every result): `weighted_average_risk_v1`.

## The rules

1. Every **question** has an integer **weight** from 1 (minor control) to 10 (critical control).
2. Every **answer option** has **risk points** from 0 (best answer) to 100 (worst answer). An option
   with no risk points means **not applicable**: that question is left out of the score entirely.
3. **Overall score** = `sum(weight x risk points) / sum(weight)` over the applicable questions only,
   rounded **half-up** to one decimal. 0 is the lowest risk and 100 the highest.
4. A **category score** is the same formula over that category's applicable questions
   (`null` when all of them are not applicable).
5. **Risk level**, from the *rounded* score and the thresholds frozen with the questionnaire
   version (defaults 25 / 50 / 75). Each band includes its lower bound:

   | Score | Level |
   |---|---|
   | below `low_max` (25) | `low` |
   | `low_max` up to below `medium_max` (50) | `medium` |
   | `medium_max` up to below `high_max` (75) | `high` |
   | `high_max` and above | `critical` |

   So 24.9 is low, 25.0 is medium, 74.9 is high and 75.0 is critical.
6. An assessment must have **every active question answered** before it can be scored, and if every
   answer is "not applicable" there is nothing to score (the request is refused).

### Worked example

| Question | Weight | Answer | Risk points |
|---|---|---|---|
| MFA for admin access | 5 | Yes | 0 |
| Encryption at rest | 4 | Partially implemented | 50 |
| Incident response plan | 3 | Not implemented | 100 |
| Penetration testing | 2 | Partially implemented | 25 |

`(5x0 + 4x50 + 3x100 + 2x25) / (5 + 4 + 3 + 2) = 550 / 14 = 39.2857`, rounded to **39.3**: **medium**.
If the penetration test question were "not applicable", the weights become `5 + 4 + 3 = 12` and the
score `500 / 12 = 41.7`.

Exact `Decimal` arithmetic is used, so rounding never depends on floating point.

### What is stored

Scoring writes the result to the assessment together with a `breakdown` that is enough to re-derive
it: the method, the thresholds, the applicable weight, every question's category, weight and risk
points, the not-applicable questions and each category's score. The scoring decision (who, when,
score, level, thresholds, version) is also written to the audit log.

## Configurable questionnaires

Nothing is hardcoded in scoring or assessments. Organizations (`org_admin` and `analyst`) create
any number of **questionnaire templates**, and edit questions, categories, options, weights, order
and thresholds through the API. A starter questionnaire (15 questions across access control, MFA,
encryption, vulnerability management, incident response, business continuity, data protection and
vendor governance, in `app/questionnaire_starter.py`) can be copied into an organization with
`POST /questionnaires/from-starter`; the copy is an ordinary editable template.

### Versioning keeps history intact

```
template ──< version 1 (draft) ──publish──> version 1 (published, frozen)
          └< version 2 (draft, cloned from v1) ──publish──> version 2 ...    retired versions stay readable
```

- Only a **draft** version can be edited. **Publishing freezes it**: its questions, options, weights,
  order and thresholds can never change again. This is enforced by database triggers, not just by
  the API.
- Changing a published questionnaire means creating a **new draft version** (cloned from the latest
  published one) and publishing that. At most one draft exists per template.
- An assessment is bound to **one published version** when it starts. Editing the template, publishing
  version 2 or retiring version 1 never alters an assessment that already started: it keeps the
  questions, weights and thresholds it began with, and a scored assessment is final.
- Nothing is deleted. Questions and options are **archived** (hidden from new versions and
  assessments), versions are **retired**, templates **archived**. The application role has no
  `DELETE`.
- A version can be published only if it has at least one active question and every active question
  has at least two active options. New assessments need a *published* version of an *active*
  template and an *active* vendor.

### Limits to be aware of

- The score is computed by the API from rows it reads under row level security; the database checks
  that scoring is complete and final, but does not recompute the arithmetic. A compromised API
  process could store a different number (see [trust-boundary.md](trust-boundary.md)).
- Table owners and superusers can disable triggers; "immutable" means immutable for the application
  role and ordinary roles, not for a database administrator.
- Scores measure the answers given. They are self-reported and say nothing about whether the answers
  are true; evidence validation is a later phase.
