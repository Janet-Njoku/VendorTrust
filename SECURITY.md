# Security Policy

## About this project

VendorTrust is a **portfolio MVP**, not a production security service. It is not deployed, it has had no
independent security review, and it is maintained by one person on a best-effort basis. Its known gaps are
listed in [docs/threat-model.md](docs/threat-model.md) and
[docs/security-controls.md](docs/security-controls.md). Please do not rely on it to protect real vendor or
customer data.

## Reporting a suspected vulnerability

Please report privately, not in a public issue or pull request.

1. Use GitHub's **private vulnerability reporting**: open the repository's **Security** tab and choose
   **Report a vulnerability** (this works only if the maintainer has enabled it).
2. If that option is not shown, open a public issue that says only that you have a security report and ask
   for a private channel. Do not include details, proof-of-concept code, credentials or personal data.

Helpful details: the affected file or endpoint, what you expected and what happened, and steps to reproduce
using fake data.

## Coordinated disclosure

Please do not disclose a vulnerability publicly until it has been discussed with the maintainer and had a
reasonable chance to be addressed or documented.

There is no security email address, response-time commitment or bug bounty. Reports will be looked at as time
allows.

## Out of scope

Issues already listed as known limitations in the documents above, and anything that requires access to a
maintainer's own machine or accounts.

## If you find a secret

If you find what looks like a real credential in this repository or its history, report it privately as
above and do not use it.
