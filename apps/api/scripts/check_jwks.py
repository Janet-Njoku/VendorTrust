"""Check that a Supabase project publishes asymmetric signing keys at its JWKS endpoint.

The JWKS endpoint is public (no credentials involved). Run it yourself:

    cd apps/api
    VT_SUPABASE_URL=https://<project-ref>.supabase.co uv run python -m scripts.check_jwks

If it reports no keys, the project still uses the legacy shared JWT secret: enable asymmetric
signing keys in the Supabase dashboard (Auth > JWT signing keys) before Phase 1 continues.

Exit status: 0 all checks passed, 1 a check failed, 2 usage or configuration error.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

# Algorithms the API will accept (see docs/phase1-design.md). Everything else is rejected.
ACCEPTED_ALGS = frozenset({"ES256", "RS256"})
ASYMMETRIC_KTY = frozenset({"EC", "RSA", "OKP"})
PRIVATE_KEY_PARAMS = frozenset({"d", "p", "q", "dp", "dq", "qi", "k"})
MAX_BYTES = 64 * 1024
JWKS_PATH = "/auth/v1/.well-known/jwks.json"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def build_jwks_url(env: dict[str, str]) -> str | None:
    explicit = env.get("VT_SUPABASE_JWKS_URL", "").strip()
    if explicit:
        return explicit
    base = env.get("VT_SUPABASE_URL", "").strip().rstrip("/")
    return f"{base}{JWKS_PATH}" if base else None


def validate_url(url: str) -> Check:
    parts = urlsplit(url)
    if parts.scheme != "https":
        return Check("JWKS URL uses https", False, f"scheme is '{parts.scheme}'")
    if not parts.hostname:
        return Check("JWKS URL has a host", False)
    if parts.username or parts.password:
        return Check("JWKS URL has no embedded credentials", False)
    return Check("JWKS URL is https with no credentials", True, parts.hostname)


def fetch(url: str, timeout: float = 10.0) -> dict[str, Any]:
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    with opener.open(request, timeout=timeout) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("JWKS response is unexpectedly large")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("JWKS response is not a JSON object")
    return data


def assess(jwks: dict[str, Any]) -> list[Check]:
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not keys:
        return [
            Check(
                "JWKS contains at least one key",
                False,
                "no keys: project likely uses the legacy shared secret; enable asymmetric "
                "signing keys in the dashboard",
            )
        ]
    checks = [Check("JWKS contains at least one key", True, f"{len(keys)} key(s)")]
    entries = [k for k in keys if isinstance(k, dict)]
    checks.append(Check("every key is a JSON object", len(entries) == len(keys)))
    checks.append(
        Check(
            "every key is asymmetric (no 'oct' secrets)",
            all(k.get("kty") in ASYMMETRIC_KTY for k in entries),
        )
    )
    checks.append(
        Check(
            "no private key material published",
            not any(PRIVATE_KEY_PARAMS & k.keys() for k in entries),
        )
    )
    checks.append(Check("every key has a kid", all(bool(k.get("kid")) for k in entries)))
    algs = sorted({str(k.get("alg")) for k in entries if k.get("alg")})
    usable = [k for k in entries if k.get("alg") in ACCEPTED_ALGS]
    checks.append(
        Check(
            f"at least one key uses an accepted algorithm {sorted(ACCEPTED_ALGS)}",
            bool(usable),
            f"algorithms seen: {algs or 'none declared'}",
        )
    )
    return checks


def main() -> int:
    url = build_jwks_url(dict(os.environ))
    if not url:
        print("Set VT_SUPABASE_URL (or VT_SUPABASE_JWKS_URL) in the environment.", file=sys.stderr)
        return 2
    checks = [validate_url(url)]
    if checks[0].ok:
        try:
            checks.extend(assess(fetch(url)))
        except (urllib.error.URLError, ValueError, json.JSONDecodeError, OSError) as exc:
            checks.append(Check("JWKS endpoint reachable and valid", False, type(exc).__name__))
    for check in checks:
        suffix = f"  ({check.detail})" if check.detail else ""
        print(f"{'PASS' if check.ok else 'FAIL'}  {check.name}{suffix}")
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
