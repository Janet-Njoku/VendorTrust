"""Fixtures for tests that need a real PostgreSQL server.

Locally these run against the cluster from `scripts/dev-db.sh init` (unix socket, throwaway
data). In CI they run against a Postgres service container: set VT_TEST_PG_HOST/PORT, the
admin and superuser role names, and PGPASSWORD (libpq reads it for every role).

Each test session creates a fresh database, applies infra/db/bootstrap.sql exactly as it
would be applied to Supabase, and drops the database afterwards.

If the server is unreachable the tests SKIP locally (with a visible reason) but FAIL when
CI=true or VT_TEST_REQUIRE_DB=1, so security tests can never be silently skipped in CI.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from tests.db.pgtools import Cluster, new_scratch_base, run_psql, tool

REPO_ROOT = Path(__file__).resolve().parents[4]
BOOTSTRAP_SQL = REPO_ROOT / "infra" / "db" / "bootstrap.sql"
VERIFY_SQL = REPO_ROOT / "infra" / "db" / "verify_bootstrap.sql"
PREFLIGHT_SQL = REPO_ROOT / "infra" / "db" / "preflight.sql"

Conn = psycopg.Connection[Any]


def conninfo(user: str, dbname: str) -> str:
    return make_conninfo(
        host=os.environ.get("VT_TEST_PG_HOST", str(REPO_ROOT / ".local-data" / "pgsock")),
        port=os.environ.get("VT_TEST_PG_PORT", "54329"),
        user=user,
        dbname=dbname,
        connect_timeout="5",
    )


ADMIN_ROLE = os.environ.get("VT_TEST_ADMIN_ROLE", "sb_admin_sim")
SUPERUSER_ROLE = os.environ.get("VT_TEST_SUPERUSER_ROLE", "vt_dev_admin")


def _require_db() -> bool:
    return os.environ.get("CI") == "true" or os.environ.get("VT_TEST_REQUIRE_DB") == "1"


def connect(user: str, dbname: str) -> Conn:
    return psycopg.connect(conninfo(user, dbname), autocommit=True)


def host_port() -> tuple[str, str]:
    return (
        os.environ.get("VT_TEST_PG_HOST", str(REPO_ROOT / ".local-data" / "pgsock")),
        os.environ.get("VT_TEST_PG_PORT", "54329"),
    )


def bootstrap(dbname: str, **variables: str) -> subprocess.CompletedProcess[str]:
    """Apply bootstrap.sql for real, like runbook Step 6: psql -X -v ON_ERROR_STOP=1 -v commit=1.

    Raises if the script exits 0 without printing the COMMITTED marker, so a silent rollback
    (the script's default) can never be mistaken for success.
    """
    host, port = host_port()
    done = run_psql(
        host=host,
        port=port,
        user=ADMIN_ROLE,
        dbname=dbname,
        file=BOOTSTRAP_SQL,
        variables={"commit": "1", **variables},
    )
    if done.returncode == 0 and "Bootstrap COMMITTED" not in done.stdout:
        raise AssertionError(f"bootstrap exited 0 but did not commit:\n{done.stdout}")
    return done


@pytest.fixture(scope="session")
def test_db() -> Iterator[str]:
    """A fresh database with infra/db/bootstrap.sql applied by a NON-superuser admin."""
    try:
        admin = connect(ADMIN_ROLE, "postgres")
    except psycopg.OperationalError as exc:
        message = (
            "PostgreSQL test server unreachable. Start it with `scripts/dev-db.sh init` "
            f"({type(exc).__name__})"
        )
        if _require_db():
            pytest.fail(message)
        pytest.skip(message)

    if tool("psql") is None:
        message = "psql not found: install PostgreSQL client tools or set VT_TEST_PG_BIN"
        if _require_db():
            pytest.fail(message)
        pytest.skip(message)

    name = f"vt_test_{uuid.uuid4().hex[:12]}"
    with admin:
        is_super = admin.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert is_super == (False,), "bootstrap must be proven to work without superuser rights"
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        applied = bootstrap(name)
        assert applied.returncode == 0, applied.stderr
        yield name
    finally:
        with connect(ADMIN_ROLE, "postgres") as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def admin_conn(test_db: str) -> Iterator[Conn]:
    with connect(ADMIN_ROLE, test_db) as conn:
        yield conn


@pytest.fixture
def app_conn(test_db: str) -> Iterator[Conn]:
    with connect("vt_app", test_db) as conn:
        yield conn


@pytest.fixture
def migrator_conn(test_db: str) -> Iterator[Conn]:
    with connect("vt_migrator", test_db) as conn:
        yield conn


@pytest.fixture
def superuser_conn(test_db: str) -> Iterator[Conn]:
    """Used only to impersonate NOLOGIN Supabase roles (anon, service_role) in tests."""
    try:
        conn = connect(SUPERUSER_ROLE, test_db)
    except psycopg.OperationalError:
        if _require_db():
            raise
        pytest.skip("superuser role unavailable")
    with conn:
        yield conn


# --- Scratch cluster: destructive scenarios that need "no roles, no schema" ------------------
# vt_app/vt_migrator are cluster-global, so scenarios that start from scratch (or from a
# deliberately unsafe pre-existing state) run in their own throwaway cluster and never touch
# the shared dev cluster used by the other DB tests.


class Scratch:
    """One fresh database in the scratch cluster, plus helpers to run the real SQL files."""

    def __init__(self, cluster: Cluster, dbname: str) -> None:
        self.cluster = cluster
        self.db = dbname
        self.ref = cluster.ref

    def su(self, sql: str) -> str:
        """Run SQL as the cluster superuser in this database."""
        return self.cluster.sql(sql, dbname=self.db)

    def as_role(self, user: str, sql: str) -> str:
        return self.cluster.sql(sql, dbname=self.db, user=user)

    def bootstrap(
        self,
        user: str = ADMIN_ROLE,
        *,
        commit: bool = True,
        variables: Mapping[str, str] | None = None,
        on_error_stop: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run bootstrap.sql. By default this APPLIES it (`-v commit=1`, like runbook Step 6).

        commit=False runs it with no options (runbook Step 5: rolls back). Passing `variables`
        sends exactly those psql variables instead, to test mistyped or invalid options.
        """
        if variables is not None:
            chosen: Mapping[str, str] = variables
        else:
            chosen = {"commit": "1"} if commit else {}
        return self.cluster.psql(
            user,
            self.db,
            file=BOOTSTRAP_SQL,
            variables=chosen,
            on_error_stop=on_error_stop,
        )

    def preflight(
        self, ref: str | None, user: str = ADMIN_ROLE
    ) -> subprocess.CompletedProcess[str]:
        variables = {} if ref is None else {"expected_ref": ref}
        return self.cluster.psql(user, self.db, file=PREFLIGHT_SQL, variables=variables)

    def verify_failures(self, user: str = ADMIN_ROLE) -> list[str]:
        done = self.cluster.psql(user, self.db, file=VERIFY_SQL, extra=["-A", "-t", "-F|"])
        assert done.returncode == 0, done.stderr
        rows = [line.split("|") for line in done.stdout.splitlines() if "|" in line]
        assert len(rows) >= 15
        return [name for name, ok in rows if ok != "t"]

    def members(self) -> str:
        return self.su(
            "SELECT coalesce(string_agg(x, E'\\n' ORDER BY x), '') FROM ("
            " SELECT format('%s<-%s by %s admin=%s inherit=%s set=%s', r.rolname, m.rolname,"
            "  g.rolname, am.admin_option, am.inherit_option, am.set_option) AS x"
            " FROM pg_auth_members am JOIN pg_roles r ON r.oid = am.roleid"
            " JOIN pg_roles m ON m.oid = am.member JOIN pg_roles g ON g.oid = am.grantor"
            " WHERE r.rolname IN ('vt_app', 'vt_migrator')"
            "    OR m.rolname IN ('vt_app', 'vt_migrator')"
            ") t"
        )

    def state(self) -> str:
        """Everything the bootstrap could change, as comparable text."""
        return self.su(
            "SELECT coalesce(string_agg(x, E'\\n' ORDER BY x), '') FROM ("
            " SELECT format('role %s %s%s%s%s%s%s', rolname, rolsuper, rolcreaterole,"
            "  rolcreatedb, rolreplication, rolbypassrls, rolcanlogin) AS x"
            "  FROM pg_roles WHERE rolname !~ '^pg_'"
            " UNION ALL SELECT format('member %s<-%s by %s %s%s%s',"
            "  r.rolname, m.rolname, g.rolname,"
            "  am.admin_option, am.inherit_option, am.set_option) FROM pg_auth_members am"
            "  JOIN pg_roles r ON r.oid = am.roleid JOIN pg_roles m ON m.oid = am.member"
            "  JOIN pg_roles g ON g.oid = am.grantor"
            " UNION ALL SELECT format('schema %s %s %s', nspname, nspowner::regrole, nspacl)"
            "  FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'"
            " UNION ALL SELECT format('config %s %s', coalesce(r.rolname, '*'), s.setconfig)"
            "  FROM pg_db_role_setting s LEFT JOIN pg_roles r ON r.oid = s.setrole"
            ") t"
        )


def _reset_cluster(cluster: Cluster) -> None:
    for dbname in cluster.sql(
        "SELECT datname FROM pg_database WHERE datname LIKE 'vt\\_s\\_%'"
    ).splitlines():
        cluster.sql(f'DROP DATABASE "{dbname}" WITH (FORCE)')
    for role in cluster.sql(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('vt_app', 'vt_migrator', 'supabase_admin') "
        "OR rolname LIKE 'vt\\_t\\_%'"
    ).splitlines():
        cluster.sql(f'DROP ROLE "{role}"')
    cluster.sql("ALTER ROLE sb_admin_sim RESET ALL")


@pytest.fixture(scope="session")
def scratch_cluster() -> Iterator[Cluster]:
    if tool("initdb") is None or tool("psql") is None or tool("pg_ctl") is None:
        message = "PostgreSQL server/client binaries not found (set VT_TEST_PG_BIN)"
        if _require_db():
            pytest.fail(message)
        pytest.skip(message)
    cluster = Cluster(new_scratch_base())
    try:
        cluster.start()
        yield cluster
    finally:
        cluster.stop()


@pytest.fixture
def scratch(scratch_cluster: Cluster) -> Scratch:
    _reset_cluster(scratch_cluster)
    name = f"vt_s_{uuid.uuid4().hex[:10]}"
    scratch_cluster.sql(f'CREATE DATABASE "{name}" OWNER sb_admin_sim')
    return Scratch(scratch_cluster, name)


# --- Migrated databases (Phase 1.3) ---------------------------------------------------------


@pytest.fixture
def make_db(test_db: str) -> Iterator[Callable[..., str]]:
    """Factory for fresh databases: bootstrap.sql applied, then (optionally) alembic head.

    Depends on test_db so it SKIPS/FAILS in exactly the same way when no server is reachable.
    """
    from tests.db.alembic_tools import upgrade

    created: list[str] = []

    def factory(*, migrate: bool = True) -> str:
        name = f"vt_test_{uuid.uuid4().hex[:12]}"
        with connect(ADMIN_ROLE, "postgres") as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        applied = bootstrap(name)
        assert applied.returncode == 0, applied.stderr
        if migrate:
            upgrade(name)
        return name

    yield factory
    with connect(ADMIN_ROLE, "postgres") as conn:
        for name in created:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def migrated_db(test_db: str) -> Iterator[str]:
    """One migrated database shared by the read-mostly RLS tests (each test seeds its own data)."""
    from tests.db.alembic_tools import upgrade

    name = f"vt_test_{uuid.uuid4().hex[:12]}"
    with connect(ADMIN_ROLE, "postgres") as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        applied = bootstrap(name)
        assert applied.returncode == 0, applied.stderr
        upgrade(name)
        yield name
    finally:
        with connect(ADMIN_ROLE, "postgres") as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# --- API fixtures (shared by the API test modules) -------------------------------------------


@pytest.fixture(scope="module")
def engine(migrated_db: str) -> Iterator[Any]:
    """An application engine (restricted vt_app role) on the migrated test database."""
    from app.db.session import build_engine
    from tests.db.alembic_tools import socket_url

    built = build_engine(socket_url("vt_app", migrated_db))
    yield built
    built.dispose()


@pytest.fixture
def world(migrated_db: str) -> Any:
    from tests.db.rls_world import seed

    return seed(migrated_db)


@pytest.fixture
def api(engine: Any) -> Any:
    """A test client with a mocked JWT verifier (local key) and the test database engine."""
    from fastapi.testclient import TestClient

    from app.auth import JwtVerifier
    from app.config import Settings
    from app.db.session import get_engine
    from app.deps import get_verifier
    from app.main import create_app
    from tests.db.api_support import AUDIENCE, ISSUER, PRIVATE_KEY

    app = create_app(Settings(environment="test"))
    verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _token: PRIVATE_KEY.public_key()
    )
    app.dependency_overrides[get_verifier] = lambda: verifier
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def dev_auth(tmp_path: Any) -> Iterator[tuple[str, Any]]:
    """The local Supabase Auth stand-in, serving real HTTP and a real JWKS on a free local port."""
    import threading

    from cryptography.hazmat.primitives.asymmetric import ec

    from scripts.dev_auth import DevAuth, create_server

    auth = DevAuth("pending", ec.generate_private_key(ec.SECP256R1()), tmp_path / "state.json")
    httpd = create_server(auth, "127.0.0.1", 0, "http://localhost:3000")
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    auth.issuer = f"{base}/auth/v1"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield base, auth
    httpd.shutdown()
    httpd.server_close()
