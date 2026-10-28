"""Executable checks of the PostgreSQL behaviours the Phase 1 design depends on.

The design (docs/trust-boundary.md) relies on: FORCE ROW LEVEL SECURITY binding table owners,
fail-closed policies when no context is set, transaction-local settings not leaking between
requests, and BYPASSRLS roles ignoring policies. If any of these ever stops being true on a
future Postgres version these tests fail, instead of the assumption failing silently.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from tests.db.conftest import Conn, connect

pytestmark = pytest.mark.db

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())

# NULLIF matters: after a transaction-local setting is reset, current_setting(..., true)
# returns '' (not NULL) on the same session, and ''::uuid would raise.
POLICY_PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


@pytest.fixture
def items_table(migrator_conn: Conn) -> Iterator[str]:
    """A migrator-owned table with rows for two orgs and a forced-RLS policy."""
    migrator_conn.execute("CREATE TABLE vt.assume_items (id serial PRIMARY KEY, org_id uuid)")
    migrator_conn.execute(
        "INSERT INTO vt.assume_items (org_id) VALUES (%s), (%s), (%s)", (ORG_A, ORG_A, ORG_B)
    )
    migrator_conn.execute("ALTER TABLE vt.assume_items ENABLE ROW LEVEL SECURITY")
    migrator_conn.execute("ALTER TABLE vt.assume_items FORCE ROW LEVEL SECURITY")
    migrator_conn.execute(
        f"CREATE POLICY tenant ON vt.assume_items FOR ALL "
        f"USING ({POLICY_PREDICATE}) WITH CHECK ({POLICY_PREDICATE})"
    )
    migrator_conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON vt.assume_items TO vt_app")
    migrator_conn.execute("GRANT USAGE ON SEQUENCE vt.assume_items_id_seq TO vt_app")
    yield "vt.assume_items"
    migrator_conn.execute("DROP TABLE vt.assume_items")


def _count(conn: Conn) -> Any:
    return conn.execute("SELECT count(*) FROM vt.assume_items").fetchone()


def test_owner_bypasses_rls_unless_forced(migrator_conn: Conn) -> None:
    migrator_conn.execute("CREATE TABLE vt.assume_owner (id int, org_id uuid)")
    try:
        migrator_conn.execute("INSERT INTO vt.assume_owner VALUES (1, %s), (2, %s)", (ORG_A, ORG_B))
        migrator_conn.execute("ALTER TABLE vt.assume_owner ENABLE ROW LEVEL SECURITY")
        migrator_conn.execute(f"CREATE POLICY tenant ON vt.assume_owner USING ({POLICY_PREDICATE})")
        count = "SELECT count(*) FROM vt.assume_owner"
        assert migrator_conn.execute(count).fetchone() == (2,), "owner bypasses un-forced RLS"
        migrator_conn.execute("ALTER TABLE vt.assume_owner FORCE ROW LEVEL SECURITY")
        assert migrator_conn.execute(count).fetchone() == (0,), "FORCE must bind the owner"
    finally:
        migrator_conn.execute("DROP TABLE vt.assume_owner")


def test_no_context_fails_closed(items_table: str, app_conn: Conn) -> None:
    assert _count(app_conn) == (0,)


def test_context_limits_rows_to_one_org(items_table: str, app_conn: Conn) -> None:
    with app_conn.transaction():
        app_conn.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
        assert _count(app_conn) == (2,)
    with app_conn.transaction():
        app_conn.execute("SELECT set_config('app.org_id', %s, true)", (ORG_B,))
        assert _count(app_conn) == (1,)


def _insert_as_org(conn: Conn, *, context_org: str, row_org: str) -> None:
    with conn.transaction():
        conn.execute("SELECT set_config('app.org_id', %s, true)", (context_org,))
        conn.execute("INSERT INTO vt.assume_items (org_id) VALUES (%s)", (row_org,))


def test_cross_org_write_and_reference_by_id_denied(items_table: str, app_conn: Conn) -> None:
    with app_conn.transaction():
        app_conn.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
        # org B's rows are invisible even when targeted explicitly
        assert app_conn.execute(
            "SELECT count(*) FROM vt.assume_items WHERE org_id = %s", (ORG_B,)
        ).fetchone() == (0,)
        assert (
            app_conn.execute("DELETE FROM vt.assume_items WHERE org_id = %s", (ORG_B,)).rowcount
            == 0
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _insert_as_org(app_conn, context_org=ORG_A, row_org=ORG_B)


@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_transaction_local_context_resets_on_same_connection(
    items_table: str, app_conn: Conn, finish: str
) -> None:
    """The next request on a reused pooled connection must not inherit the previous context."""
    app_conn.autocommit = False
    try:
        app_conn.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
        assert _count(app_conn) == (2,)
        getattr(app_conn, finish)()

        assert app_conn.execute("SELECT current_setting('app.org_id', true)").fetchone() in (
            ("",),
            (None,),
        )
        assert _count(app_conn) == (0,)
        app_conn.rollback()
    finally:
        app_conn.autocommit = True


def test_session_level_setting_would_leak(items_table: str, app_conn: Conn) -> None:
    """Negative control: is_local=false persists, which is why the API must always use true."""
    app_conn.execute("SELECT set_config('app.org_id', %s, false)", (ORG_A,))
    assert _count(app_conn) == (2,)  # still in effect on the next statement: a leak hazard
    app_conn.execute("RESET app.org_id")


def test_context_is_not_visible_to_other_connections(
    items_table: str, app_conn: Conn, test_db: str
) -> None:
    with app_conn.transaction():
        app_conn.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
        with connect("vt_app", test_db) as other:
            assert _count(other) == (0,)


def test_bypassrls_role_ignores_policies(
    items_table: str, superuser_conn: Conn, migrator_conn: Conn
) -> None:
    """Why the API must never connect as service_role/postgres: every policy is skipped."""
    # BYPASSRLS skips policies, NOT ordinary grants: service_role needs explicit privileges,
    # which bootstrap never gives it. We grant them here to demonstrate the policy bypass.
    migrator_conn.execute("GRANT USAGE ON SCHEMA vt TO service_role")
    migrator_conn.execute("GRANT SELECT ON vt.assume_items TO service_role")
    superuser_conn.execute("SET ROLE service_role")
    try:
        assert _count(superuser_conn) == (3,)
    finally:
        superuser_conn.execute("RESET ROLE")
        migrator_conn.execute("REVOKE USAGE ON SCHEMA vt FROM service_role")


def test_privileged_role_guard_query(
    app_conn: Conn, migrator_conn: Conn, superuser_conn: Conn
) -> None:
    """The startup guard (Phase 1.2) refuses roles that can bypass RLS."""
    guard = "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
    assert app_conn.execute(guard).fetchone() == (False,)
    assert migrator_conn.execute(guard).fetchone() == (False,)
    superuser_conn.execute("SET ROLE service_role")
    try:
        assert superuser_conn.execute(guard).fetchone() == (True,)
    finally:
        superuser_conn.execute("RESET ROLE")
