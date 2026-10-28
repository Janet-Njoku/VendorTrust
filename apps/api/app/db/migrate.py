"""Helpers for alembic/env.py, kept here so they are typed and unit tested."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from sqlalchemy import Connection, text
from sqlalchemy.engine import URL, make_url

from app.credentials import migration_database_url
from app.dburl import redact

MIGRATION_ROLE = "vt_migrator"
KNOWN_OPTIONS = frozenset({"commit", "allow_downgrade"})


class MigrationError(Exception):
    """A migration run was refused or failed. The message never contains a URL or password."""


class MigrationOptions(NamedTuple):
    commit: bool
    allow_downgrade: bool


def parse_options(x_args: Mapping[str, str]) -> MigrationOptions:
    """Only `commit=1` and `allow_downgrade=1` exist, and only the exact value `1` counts."""
    unknown = sorted(set(x_args) - KNOWN_OPTIONS)
    if unknown:
        raise MigrationError(
            f"unknown -x option(s): {', '.join(unknown)} (allowed: commit=1, allow_downgrade=1)"
        )
    for name, value in x_args.items():
        if value != "1":
            raise MigrationError(f"invalid value for -x {name}: it must be exactly 1")
    return MigrationOptions(commit="commit" in x_args, allow_downgrade="allow_downgrade" in x_args)


def guard_downgrade(is_downgrade: bool, options: MigrationOptions) -> None:
    if is_downgrade and not options.allow_downgrade:
        raise MigrationError(
            "refusing to downgrade: downgrades drop tables and policies. "
            "Pass -x allow_downgrade=1 if you really mean it (development databases only)."
        )


def engine_url() -> URL:
    """The vt_migrator URL from the external credential loader, as a psycopg 3 SQLAlchemy URL."""
    secret = migration_database_url().get_secret_value()
    return make_url(secret).set(drivername="postgresql+psycopg")


def redact_error(exc: Exception, url: str) -> str:
    return redact(f"{type(exc).__name__}: {exc}", url)


def assert_migration_role(connection: Connection) -> None:
    """Refuse to migrate as anything but the plain vt_migrator role."""
    row: Any = connection.execute(
        text(
            "SELECT current_user::text, r.rolsuper, r.rolbypassrls "
            "FROM pg_roles r WHERE r.rolname = current_user"
        )
    ).one()
    user, superuser, bypass = row
    if user != MIGRATION_ROLE:
        raise MigrationError(
            f"migrations must run as {MIGRATION_ROLE}, but this connection is {user}"
        )
    if superuser or bypass:
        raise MigrationError(f"{MIGRATION_ROLE} must not be a superuser or have BYPASSRLS")
