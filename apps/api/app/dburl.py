"""Validation and safe description of PostgreSQL connection URLs.

Used by the credential loader, the connectivity check script and (from Phase 1.3) Alembic.
Errors say WHICH rule failed and never repeat the URL, the user or the password.
"""

from __future__ import annotations

import re
from typing import Literal

import psycopg
from psycopg.conninfo import conninfo_to_dict

LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
TLS_MODES = frozenset({"require", "verify-ca", "verify-full"})
ROLE_PREFIX = {"app": "vt_app", "migrator": "vt_migrator"}
ROLE_LABEL = {"app": "application (vt_app)", "migrator": "migration (vt_migrator)"}
SCHEME = re.compile(r"postgresql(\+psycopg)?://")
USERINFO_PASSWORD = re.compile(r"://[^/@\s:]*:([^@\s]+)@")

Role = Literal["app", "migrator"]


class DatabaseUrlError(ValueError):
    """A database URL is unusable. The message never contains any part of the URL."""


def normalize_url(url: str) -> str:
    """Accept SQLAlchemy-style `postgresql+psycopg://` URLs."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url.strip())


def parse_target(url: str) -> dict[str, str]:
    try:
        parsed = conninfo_to_dict(normalize_url(url))
    except psycopg.ProgrammingError:
        raise DatabaseUrlError("the URL could not be parsed") from None
    return {key: str(value) for key, value in parsed.items() if value is not None}


def is_local(target: dict[str, str]) -> bool:
    host = target.get("host", "")
    return host.startswith("/") or host in LOCAL_HOSTS


def tls_acceptable(target: dict[str, str]) -> bool:
    """Remote connections must require TLS; local socket/loopback connections may not."""
    return is_local(target) or target.get("sslmode", "prefer") in TLS_MODES


def describe(target: dict[str, str]) -> str:
    """Connection summary that is safe to print: never includes the password."""
    return (
        f"{target.get('user', '?')}@{target.get('host', 'local-socket')}:"
        f"{target.get('port', '5432')}/{target.get('dbname', '?')}"
    )


def redact(message: str, url: str) -> str:
    """Remove the URL, password and userinfo from an error message before printing it."""
    cleaned = message
    secrets = {url, normalize_url(url)}
    try:
        password = parse_target(url).get("password") if "://" in url else None
    except DatabaseUrlError:
        password = None
    if password:
        secrets.add(password)
    # Fallback for URLs the driver cannot parse: take the password straight from the text.
    userinfo = USERINFO_PASSWORD.search(url)
    if userinfo:
        secrets.add(userinfo.group(1))
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


def validate_database_url(url: str, role: Role) -> None:
    """Check a URL is well formed and belongs to the right role family and transport.

    The application role may only use users `vt_app` / `vt_app.<project-ref>`, the migration
    role only `vt_migrator` / `vt_migrator.<project-ref>`, so the two can never be swapped.
    """
    text = url.strip()
    if not text or any(ord(ch) < 32 for ch in text):
        raise DatabaseUrlError("the URL is empty or contains control characters")
    if not SCHEME.match(text):
        raise DatabaseUrlError(
            "unsupported URL scheme (expected postgresql:// or postgresql+psycopg://)"
        )
    target = parse_target(text)
    user = target.get("user", "")
    prefix = ROLE_PREFIX[role]
    if not user:
        raise DatabaseUrlError("the URL has no user name")
    if user != prefix and not user.startswith(prefix + "."):
        raise DatabaseUrlError(
            f"the user is not in the {ROLE_LABEL[role]} role family "
            f"(expected '{prefix}' or '{prefix}.<project-ref>')"
        )
    if not tls_acceptable(target):
        raise DatabaseUrlError("remote hosts require sslmode=require, verify-ca or verify-full")
