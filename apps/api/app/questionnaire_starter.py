"""A starter security due diligence questionnaire, offered as DATA an organization can copy.

Nothing here is hardcoded into scoring or assessments: `POST /questionnaires/from-starter`
copies this content into the organization's own draft template, which can then be edited,
re-weighted, extended and published like any other. Categories are plain slugs, so
organizations are free to use their own.

Option answers use risk points: 0 is the best answer, 100 the worst, None is not applicable.
"""

from __future__ import annotations

from typing import Any

YES_NO: list[tuple[str, int | None]] = [("Yes", 0), ("No", 100), ("Not applicable", None)]
MATURITY: list[tuple[str, int | None]] = [
    ("Fully implemented", 0),
    ("Partially implemented", 50),
    ("Not implemented", 100),
    ("Not applicable", None),
]

STARTER_NAME = "Security due diligence (starter)"
STARTER_DESCRIPTION = (
    "A 15-question baseline covering access control, MFA, encryption, vulnerability "
    "management, incident response, business continuity, data protection and vendor governance."
)

# (category, prompt, weight, options)
STARTER_QUESTIONS: list[tuple[str, str, int, list[tuple[str, int | None]]]] = [
    (
        "access_control",
        "Is access to systems and customer data granted on a least-privilege, role-based basis?",
        4,
        MATURITY,
    ),
    (
        "access_control",
        "Are user access rights reviewed at least quarterly and removed promptly when roles change or people leave?",
        3,
        MATURITY,
    ),
    (
        "mfa",
        "Is multi-factor authentication required for all administrative and remote access?",
        5,
        YES_NO,
    ),
    (
        "mfa",
        "Is multi-factor authentication enforced for workforce access to systems that store or process customer data?",
        4,
        MATURITY,
    ),
    (
        "encryption",
        "Is customer data encrypted in transit using TLS 1.2 or higher?",
        4,
        YES_NO,
    ),
    (
        "encryption",
        "Is customer data encrypted at rest, with keys managed and rotated under a documented process?",
        4,
        MATURITY,
    ),
    (
        "vulnerability_management",
        "Are systems scanned for vulnerabilities at least monthly, with defined remediation timelines?",
        3,
        MATURITY,
    ),
    (
        "vulnerability_management",
        "Is an independent penetration test performed at least annually, with findings tracked to closure?",
        3,
        MATURITY,
    ),
    (
        "incident_response",
        "Is there a documented incident response plan that is tested at least annually?",
        4,
        MATURITY,
    ),
    (
        "incident_response",
        "Will customers be notified of a confirmed security breach within a contractually defined period?",
        4,
        YES_NO,
    ),
    (
        "business_continuity",
        "Are backups taken regularly and restoration tested at least annually, with defined recovery objectives?",
        3,
        MATURITY,
    ),
    (
        "business_continuity",
        "Is there a business continuity and disaster recovery plan that is tested at least annually?",
        3,
        MATURITY,
    ),
    (
        "data_protection",
        "Is there a data classification, retention and secure deletion policy that is followed in practice?",
        3,
        MATURITY,
    ),
    (
        "data_protection",
        "Are staff with access to customer data background-checked and trained in security at least annually?",
        2,
        MATURITY,
    ),
    (
        "vendor_governance",
        "Does the vendor assess and monitor the security of its own critical subprocessors?",
        3,
        MATURITY,
    ),
]


def starter_questions() -> list[dict[str, Any]]:
    return [
        {
            "category": category,
            "prompt": prompt,
            "weight": weight,
            "options": [{"label": label, "risk_points": points} for label, points in options],
        }
        for category, prompt, weight, options in STARTER_QUESTIONS
    ]
