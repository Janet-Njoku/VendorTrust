# Screenshots

Four screenshots are committed and shown in the main [README](../../README.md#screenshots). They were taken
from the running application with one sample vendor ("Demo Vendor"). An automated test,
`apps/api/tests/test_docs.py`, fails if a document links to an image that does not exist, or if images exist
that the README does not show.

| File | Screen |
|---|---|
| `dashboard.png` | Dashboard: totals, risk distribution, high-risk vendors, progress, recent activity |
| `assessment-results.png` | Scored assessment: risk gauge, score by category, weighted breakdown |
| `vendor-register.png` | Vendor register with search and status and risk filters |
| `questionnaire.png` | A published, read-only questionnaire version with weights and risk thresholds |

## Still to capture

| Suggested file | Screen | How to capture |
|---|---|---|
| `mfa-setup.png` | Two-factor setup screen for an internal role | Real local stack ([local-e2e.md](../local-e2e.md)), after bootstrap |
| `no-access.png` | "No access yet" screen for a newly registered user | Real local stack, step 3 of the guide |

## Capturing more

Demo mode (`NEXT_PUBLIC_DEMO_MODE=true pnpm web:dev`) serves fictional data with an amber `DEMO MODE` banner;
keep the banner in demo-mode shots. The MFA and no-access screens need the real local stack, because demo mode
has no MFA or provisioning.

Suggestions: a 1440 x 900 window or larger, PNG, no browser chrome, other tabs and bookmarks hidden. Before
committing an image, check it for real email addresses, project URLs or identifiers, tokens, QR codes and TOTP
secrets, and for personal data in the sample content. Local test accounts such as `you@example.com` are fine.

When you add an image, link it from the README's Screenshots section and add it to the table above.
