"""Verify connectivity and role safety for one of the two database roles.

Read-only: it runs SELECTs against catalogs and changes nothing. Credentials come from
`app.credentials`: the process environment, or the files kept OUTSIDE the repository
(~/.config/vendortrust/api.env and migrations.env). The password and the URL are never printed.
Run it yourself, in your own Terminal, so credentials stay on your machine:

    cd apps/api
    uv run python -m scripts.check_db_connection --kind app
    uv run python -m scripts.check_db_connection --kind migrator
    uv run python -m scripts.check_db_connection --kind app --env-file /path/to/other/api.env

Exit status: 0 all checks passed, 1 a check failed, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

from app.credentials import CredentialError, load_database_credential
from app.dburl import (
    TLS_MODES,
    DatabaseUrlError,
    describe,
    is_local,
    normalize_url,
    parse_target,
    redact,
)

__all__ = [
    "Check",
    "describe",
    "is_local",
    "main",
    "normalize_url",
    "parse_target",
    "redact",
    "run",
]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def tls_policy(target: dict[str, str]) -> Check:
    """Remote connections must require TLS; local socket/loopback connections may not."""
    if is_local(target):
        return Check("transport", True, "local connection (TLS not required)")
    mode = target.get("sslmode", "prefer")
    return Check(
        "remote connection requires TLS (sslmode)",
        mode in TLS_MODES,
        f"sslmode={mode}; use one of {sorted(TLS_MODES)}",
    )


def role_checks(conn: psycopg.Connection[tuple[object, ...]], kind: str) -> list[Check]:
    checks: list[Check] = []
    row = conn.execute(
        "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
        "FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if row is None:
        return [Check("current role is visible in pg_roles", False)]
    name, superuser, bypassrls, createrole, createdb, replication = row
    checks.append(Check(f"connected as role '{name}'", True))
    checks.append(Check("not a superuser", superuser is False))
    checks.append(
        Check(
            "does not bypass row level security",
            bypassrls is False,
            "a BYPASSRLS role (e.g. Supabase service_role/postgres) ignores every policy",
        )
    )
    checks.append(
        Check(
            "no CREATEROLE / CREATEDB / REPLICATION",
            not (createrole or createdb or replication),
        )
    )
    memberships = conn.execute(
        "SELECT count(*) FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles "
        "WHERE rolname = current_user)"
    ).fetchone()
    checks.append(Check("member of no other role", memberships == (0,)))

    schema_exists = conn.execute("SELECT to_regnamespace('vt') IS NOT NULL").fetchone() == (True,)
    checks.append(
        Check("schema vt exists", schema_exists, "run infra/db/bootstrap.sql if this fails")
    )
    if not schema_exists:
        return checks

    can_create = conn.execute(
        "SELECT has_schema_privilege(current_user, 'vt', 'CREATE')"
    ).fetchone()
    owns_schema = conn.execute(
        "SELECT nspowner = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
        "FROM pg_namespace WHERE nspname = 'vt'"
    ).fetchone()
    if kind == "app":
        checks.append(Check("cannot create objects in schema vt", can_create == (False,)))
        owned = conn.execute(
            "SELECT count(*) FROM pg_class WHERE relowner = (SELECT oid FROM pg_roles "
            "WHERE rolname = current_user)"
        ).fetchone()
        checks.append(Check("owns no tables or other relations", owned == (0,)))
    elif kind == "migrator":
        checks.append(Check("owns schema vt", owns_schema == (True,)))
    return checks


def run(url: str, kind: str) -> list[Check]:
    target = parse_target(url)
    checks = [tls_policy(target)]
    if not checks[0].ok:
        return checks  # never open a non-TLS connection to a remote host
    started = time.perf_counter()
    with psycopg.connect(normalize_url(url), connect_timeout=10, autocommit=True) as conn:
        conn.execute("SELECT 1")
        elapsed_ms = (time.perf_counter() - started) * 1000
        version = conn.execute("SHOW server_version").fetchone()
        ssl = conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()
        checks.append(Check("connected and SELECT 1 succeeded", True, f"{elapsed_ms:.0f} ms"))
        checks.append(Check("server version", True, str(version[0]) if version else "?"))
        in_tls = bool(ssl and ssl[0])
        checks.append(
            Check(
                "connection is encrypted",
                in_tls or is_local(target),
                "TLS in use" if in_tls else "no TLS",
            )
        )
        checks.extend(role_checks(conn, kind))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--kind", choices=["app", "migrator"], default="app")
    parser.add_argument(
        "--env-file",
        help="credential file to use instead of the standard location outside the repository",
    )
    args = parser.parse_args(argv)

    env_file = Path(os.path.abspath(os.path.expanduser(args.env_file))) if args.env_file else None
    try:
        credential = load_database_credential(args.kind, env_file=env_file)
    except CredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for warning in credential.warnings:
        print(f"WARNING: {warning}")
    url = credential.url.get_secret_value()
    try:
        print(f"Source: {credential.source}")
        print(f"Target: {describe(parse_target(url))}  (role kind: {args.kind})")
    except DatabaseUrlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        checks = run(url, args.kind)
    except psycopg.Error as exc:
        print(f"FAIL  connection error: {type(exc).__name__}: {redact(str(exc), url).strip()}")
        return 1

    for check in checks:
        suffix = f"  ({check.detail})" if check.detail else ""
        print(f"{'PASS' if check.ok else 'FAIL'}  {check.name}{suffix}")
    failed = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
