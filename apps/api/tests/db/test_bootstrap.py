"""Verifies infra/db/bootstrap.sql: the role and schema model the whole design rests on."""

from __future__ import annotations

import re

import psycopg
import pytest

from tests.db.conftest import BOOTSTRAP_SQL, VERIFY_SQL, Conn, bootstrap

pytestmark = pytest.mark.db


def _verify(conn: Conn) -> list[tuple[str, bool]]:
    rows = conn.execute(VERIFY_SQL.read_text()).fetchall()
    return [(str(name), bool(ok)) for name, ok in rows]


def test_verify_script_passes_after_bootstrap(admin_conn: Conn) -> None:
    results = _verify(admin_conn)
    assert len(results) >= 15
    assert [name for name, ok in results if not ok] == []


def test_bootstrap_is_idempotent(admin_conn: Conn, test_db: str) -> None:
    for _ in range(2):
        done = bootstrap(test_db)
        assert done.returncode == 0, done.stderr
    assert [name for name, ok in _verify(admin_conn) if not ok] == []


def test_verify_script_detects_unsafe_state(admin_conn: Conn, migrator_conn: Conn) -> None:
    """The verifier must fail loudly when the model is violated (not just pass by default)."""
    migrator_conn.execute("GRANT CREATE ON SCHEMA vt TO vt_app")
    try:
        failed = [name for name, ok in _verify(admin_conn) if not ok]
        assert "vt_app cannot create in schema vt" in failed
    finally:
        migrator_conn.execute("REVOKE CREATE ON SCHEMA vt FROM vt_app")
    assert [name for name, ok in _verify(admin_conn) if not ok] == []


def test_bootstrap_contains_no_credentials() -> None:
    sql = BOOTSTRAP_SQL.read_text()
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert not re.search(r"\bPASSWORD\b", code, re.IGNORECASE)
    assert not re.search(r"(sb_secret_|sb_publishable_|eyJ[A-Za-z0-9_-]{20,})", sql)


def test_roles_have_no_dangerous_attributes(admin_conn: Conn) -> None:
    rows = admin_conn.execute(
        "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication, "
        "rolcanlogin FROM pg_roles WHERE rolname IN ('vt_app', 'vt_migrator') ORDER BY 1"
    ).fetchall()
    assert rows == [
        ("vt_app", False, False, False, False, False, True),
        ("vt_migrator", False, False, False, False, False, True),
    ]


def test_bootstrap_sets_no_password(superuser_conn: Conn) -> None:
    """The roles are unusable over password auth until the operator sets a password
    interactively (psql \\password), so no password ever appears in SQL files or history."""
    rows = superuser_conn.execute(
        "SELECT rolname, rolpassword IS NULL FROM pg_authid "
        "WHERE rolname IN ('vt_app', 'vt_migrator')"
    ).fetchall()
    assert sorted(rows) == [("vt_app", True), ("vt_migrator", True)]


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE vt.scratch (id int)",
        "CREATE TABLE public.scratch (id int)",
        "CREATE SCHEMA scratch",
        "CREATE ROLE scratch_role",
        "CREATE DATABASE scratch_db",
        "ALTER SCHEMA vt OWNER TO vt_app",
    ],
)
def test_app_role_cannot_perform_ddl(app_conn: Conn, statement: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(statement)


def test_app_role_cannot_assume_other_roles(app_conn: Conn) -> None:
    for role in ("vt_migrator", "sb_admin_sim", "service_role", "anon"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(f'SET ROLE "{role}"')


def test_migrator_can_create_in_vt_but_not_public(migrator_conn: Conn) -> None:
    migrator_conn.execute("CREATE TABLE vt.bootstrap_probe (id int)")
    migrator_conn.execute("DROP TABLE vt.bootstrap_probe")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator_conn.execute("CREATE TABLE public.bootstrap_probe (id int)")


def test_new_tables_are_not_readable_by_app_role_until_granted(
    app_conn: Conn, migrator_conn: Conn
) -> None:
    """No default privileges: every migration must grant explicitly."""
    migrator_conn.execute("CREATE TABLE vt.grant_probe (id int)")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute("SELECT * FROM vt.grant_probe")
        migrator_conn.execute("GRANT SELECT ON vt.grant_probe TO vt_app")
        assert app_conn.execute("SELECT count(*) FROM vt.grant_probe").fetchone() == (0,)
    finally:
        migrator_conn.execute("DROP TABLE vt.grant_probe")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_supabase_data_api_roles_cannot_reach_vt(
    superuser_conn: Conn, migrator_conn: Conn, role: str
) -> None:
    """A leaked publishable key maps to anon/authenticated: it must see nothing in `vt`."""
    migrator_conn.execute("CREATE TABLE vt.data_api_probe (id int)")
    try:
        migrator_conn.execute("GRANT SELECT ON vt.data_api_probe TO vt_app")
        superuser_conn.execute(f'SET ROLE "{role}"')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            superuser_conn.execute("SELECT * FROM vt.data_api_probe")
    finally:
        superuser_conn.execute("RESET ROLE")
        migrator_conn.execute("DROP TABLE vt.data_api_probe")
