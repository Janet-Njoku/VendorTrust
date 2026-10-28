"""app.db.context.begin_request through SQLAlchemy, plus unit tests of the migration helpers."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from app.credentials import CredentialError
from app.db.context import AccessDenied, OrganizationRequired, begin_request
from app.db.migrate import (
    MigrationError,
    MigrationOptions,
    assert_migration_role,
    engine_url,
    guard_downgrade,
    parse_options,
    redact_error,
)
from tests._creds import CANARY
from tests.db.alembic_tools import socket_url
from tests.db.rls_world import World, seed

pytestmark = pytest.mark.db


@pytest.fixture
def world(migrated_db: str) -> World:
    return seed(migrated_db)


def test_begin_request_resolves_and_the_context_is_transaction_local(world: World) -> None:
    engine = create_engine(socket_url("vt_app", world.db))
    try:
        with engine.connect() as conn:
            with conn.begin():
                begin_request(conn, uuid.UUID(world.a_admin))
                rows = conn.execute(text("SELECT count(*) FROM vt.vendors")).scalar_one()
                assert rows == 2
            with conn.begin():  # same pooled connection, next request, no context
                assert conn.execute(text("SELECT count(*) FROM vt.vendors")).scalar_one() == 0
    finally:
        engine.dispose()


def test_errors_are_mapped_without_detail(world: World) -> None:
    engine = create_engine(socket_url("vt_app", world.db))
    try:
        with engine.connect() as conn:
            with conn.begin(), pytest.raises(AccessDenied):
                begin_request(conn, uuid.UUID(world.outsider))
            with conn.begin(), pytest.raises(OrganizationRequired):
                begin_request(conn, uuid.UUID(world.multi))
            with conn.begin():
                begin_request(conn, uuid.UUID(world.multi), uuid.UUID(world.org_b))
                assert conn.execute(text("SELECT count(*) FROM vt.vendors")).scalar_one() == 2
    finally:
        engine.dispose()


def test_begin_request_refuses_to_run_outside_a_transaction(world: World) -> None:
    engine = create_engine(socket_url("vt_app", world.db))
    try:
        with engine.connect() as conn, pytest.raises(RuntimeError, match="inside a transaction"):
            begin_request(conn, uuid.UUID(world.a_admin))
    finally:
        engine.dispose()


# --- migration helpers (no database needed, but kept next to the migration tests) ---------------


def test_option_parsing_accepts_only_exact_flags() -> None:
    assert parse_options({}) == MigrationOptions(commit=False, allow_downgrade=False)
    assert parse_options({"commit": "1"}) == MigrationOptions(commit=True, allow_downgrade=False)
    assert parse_options({"allow_downgrade": "1"}).allow_downgrade is True
    for bad in ({"commit": "0"}, {"commit": "true"}, {"commit": ""}, {"commit": " 1"}):
        with pytest.raises(MigrationError, match="exactly 1"):
            parse_options(bad)
    with pytest.raises(MigrationError, match="unknown"):
        parse_options({"dry_run": "0"})


def test_downgrade_guard() -> None:
    guard_downgrade(False, MigrationOptions(False, False))
    guard_downgrade(True, MigrationOptions(False, True))
    with pytest.raises(MigrationError, match="refusing to downgrade"):
        guard_downgrade(True, MigrationOptions(True, False))


def test_driver_errors_are_redacted() -> None:
    url = f"postgresql+psycopg://vt_migrator:{CANARY}@db.example.invalid:5432/postgres"
    message = redact_error(RuntimeError(f"could not connect using {url} password {CANARY}"), url)
    assert CANARY not in message


@pytest.mark.parametrize(
    ("user", "outcome"),
    [
        ("vt_migrator", None),
        ("vt_app", "must run as vt_migrator"),
        ("vt_dev_admin", "must run as vt_migrator"),
    ],
)
def test_role_guard_accepts_only_the_plain_migration_role(
    migrated_db: str, user: str, outcome: str | None
) -> None:
    engine = create_engine(socket_url(user, migrated_db))
    try:
        with engine.connect() as conn:
            if outcome is None:
                assert_migration_role(conn)
            else:
                with pytest.raises(MigrationError, match=outcome):
                    assert_migration_role(conn)
    finally:
        engine.dispose()


def test_engine_url_comes_from_the_credential_loader(
    migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VT_MIGRATION_DATABASE_URL", socket_url("vt_migrator", migrated_db))
    url = engine_url()
    assert url.drivername == "postgresql+psycopg"
    assert url.username == "vt_migrator"
    monkeypatch.setenv("VT_MIGRATION_DATABASE_URL", socket_url("vt_app", migrated_db))
    with pytest.raises(CredentialError, match="role family"):
        engine_url()
