# Screenshots

**No screenshots have been captured yet.** This folder holds the plan and instructions; image files will
be added here, and only then will the main README link to them. (An automated test,
`apps/api/tests/test_docs.py`, fails if any document links to an image that does not exist, so a
screenshot cannot be claimed before it is committed.)

## Planned images

| File | Screen | How to capture |
|---|---|---|
| `01-dashboard.png` | Dashboard: totals, risk distribution, high-risk vendors, progress, activity | Demo mode, route `/` |
| `02-vendor-register.png` | Vendor register with a search and a risk filter applied | Demo mode, `/vendors` |
| `03-questionnaire-editor.png` | Editing a draft questionnaire version (weights, options, thresholds) | Demo mode, create from starter, open the draft |
| `04-assessment-answering.png` | Answering an assessment with progress and saved answers | Demo mode, open an in-progress assessment |
| `05-assessment-result.png` | Scored result: gauge, category bars, weighted breakdown | Demo mode, open a scored assessment |
| `06-mfa-setup.png` | Two-factor setup screen for an internal role | Real local stack ([local-e2e.md](../local-e2e.md)), after bootstrap |
| `07-no-access.png` | "No access yet" screen for a newly registered user | Real local stack, step 3 of the guide |

## How to capture them

Screens 01 to 05 use **demo mode**: fictional sample data held in the browser, with the amber
`DEMO MODE` banner visible. Keep the banner in the screenshot; it is accurate and it is what keeps demo
data from being mistaken for a real deployment.

```bash
NEXT_PUBLIC_DEMO_MODE=true pnpm web:dev      # http://localhost:3000 , no backend and no sign-in needed
```

Screens 06 and 07 need the real local stack, because demo mode has no MFA or provisioning:
follow [local-e2e.md](../local-e2e.md) up to the screen you want.

Suggestions: a 1440 x 900 window, light theme, PNG, no browser chrome, and crop out any personal
bookmarks, profile pictures or other tabs. Do not photograph real organization data or real tokens;
local test accounts such as `you@example.com` are fine.

When an image is added, link it from the README's "Screenshots" section and update the status here.
