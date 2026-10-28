"""The one-time first-organization bootstrap: authorization, one-time-ness, atomicity, the CLI."""

from __future__ import annotations

import io
import threading
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from sqlalchemy import Engine, create_engine, pool

from app.auth import verifier_from_settings
from app.bootstrap import (
    BootstrapError,
    VerifiedAdmin,
    bootstrap_first_organization,
    verify_admin_identity,
)
from app.config import Settings
from scripts.bootstrap_org import main
from tests._creds import CANARY
from tests.db.alembic_tools import socket_url
from tests.db.conftest import SUPERUSER_ROLE, connect

pytestmark = pytest.mark.db


@pytest.fixture
def fresh(make_db: Callable[..., str]) -> Iterator[tuple[str, Engine]]:
    """A newly migrated database with no organization, and an engine for the migration role."""
    db = make_db()
    engine = create_engine(socket_url("vt_migrator", db), poolclass=pool.NullPool)
    yield db, engine
    engine.dispose()


def admin(email: str = "root@example.invalid") -> VerifiedAdmin:
    return VerifiedAdmin(user_id=uuid.uuid4(), email=email)


def counts(db: str) -> tuple[int, int, int, int]:
    with connect(SUPERUSER_ROLE, db) as su:
        row = su.execute(
            "SELECT (SELECT count(*) FROM vt.organizations), (SELECT count(*) FROM vt.app_users), "
            "(SELECT count(*) FROM vt.memberships), (SELECT count(*) FROM vt.audit_log)"
        ).fetchone()
    assert row is not None
    return (int(row[0]), int(row[1]), int(row[2]), int(row[3]))


def sqlstate(conn: Any, sql: str, *params: object) -> str | None:
    try:
        conn.execute(sql, params)
    except psycopg.Error as exc:
        return exc.sqlstate
    return None


# --- the transaction ------------------------------------------------------------------------------


def test_a_dry_run_creates_everything_and_rolls_it_all_back(fresh: tuple[str, Engine]) -> None:
    db, engine = fresh
    result = bootstrap_first_organization(engine, admin(), "Acme Ltd", commit=False)
    assert result.committed is False
    assert counts(db) == (0, 0, 0, 0)


def test_commit_creates_the_organization_user_membership_and_an_audit_event(
    fresh: tuple[str, Engine],
) -> None:
    db, engine = fresh
    who = admin()
    result = bootstrap_first_organization(engine, who, "  Acme Ltd  ", commit=True)
    assert result.committed and not result.user_existed
    assert counts(db) == (1, 1, 1, 1)
    with connect(SUPERUSER_ROLE, db) as su:
        assert su.execute("SELECT name, status FROM vt.organizations").fetchone() == (
            "Acme Ltd",
            "active",
        )
        assert su.execute(
            "SELECT m.role, m.status, m.vendor_id, u.email FROM vt.memberships m JOIN vt.app_users u ON u.id = m.user_id"
        ).fetchone() == ("org_admin", "active", None, "root@example.invalid")
        audit = su.execute(
            "SELECT action, actor_role, actor_user_id::text, entity_type FROM vt.audit_log"
        ).fetchone()
        assert audit == (
            "bootstrap.first_organization",
            "org_admin",
            str(who.user_id),
            "organization",
        )


def test_it_keeps_a_user_row_that_first_login_already_created(fresh: tuple[str, Engine]) -> None:
    db, engine = fresh
    who = admin()
    with connect(SUPERUSER_ROLE, db) as su:
        su.execute(
            "INSERT INTO vt.app_users (id, email) VALUES (%s, 'provisioned@example.invalid')",
            (who.user_id,),
        )
    result = bootstrap_first_organization(engine, who, "Acme", commit=True)
    assert result.user_existed
    assert counts(db) == (1, 1, 1, 1)
    with connect(SUPERUSER_ROLE, db) as su:
        assert su.execute("SELECT email FROM vt.app_users").fetchone() == (
            "provisioned@example.invalid",
        )


def test_a_disabled_user_cannot_be_made_administrator(fresh: tuple[str, Engine]) -> None:
    db, engine = fresh
    who = admin()
    with connect(SUPERUSER_ROLE, db) as su:
        su.execute(
            "INSERT INTO vt.app_users (id, email, status) VALUES (%s, 'x@example.invalid', 'disabled')",
            (who.user_id,),
        )
    with pytest.raises(BootstrapError, match="disabled"):
        bootstrap_first_organization(engine, who, "Acme", commit=True)
    assert counts(db)[0] == 0


@pytest.mark.parametrize("name", ["", "   ", "x" * 201, "bad\x00name", "line\nbreak"])
def test_organization_names_are_validated(fresh: tuple[str, Engine], name: str) -> None:
    db, engine = fresh
    with pytest.raises(BootstrapError, match="organization name"):
        bootstrap_first_organization(engine, admin(), name, commit=True)
    assert counts(db) == (0, 0, 0, 0)


def test_a_failure_part_way_leaves_nothing_behind(
    fresh: tuple[str, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, engine = fresh

    def explode(*_args: object) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr("app.bootstrap._audit", explode)
    with pytest.raises(RuntimeError):
        bootstrap_first_organization(engine, admin(), "Acme", commit=True)
    assert counts(db) == (0, 0, 0, 0)


# --- one-time, enforced by the database ----------------------------------------------------------------


def test_a_second_bootstrap_is_refused_and_changes_nothing(fresh: tuple[str, Engine]) -> None:
    db, engine = fresh
    bootstrap_first_organization(engine, admin(), "First", commit=True)
    with pytest.raises(BootstrapError, match="already bootstrapped"):
        bootstrap_first_organization(engine, admin("other@example.invalid"), "Second", commit=True)
    assert counts(db) == (1, 1, 1, 1)


def test_even_a_modified_script_cannot_create_another_organization_afterwards(
    fresh: tuple[str, Engine],
) -> None:
    """The migration credential is the operator's, but PostgreSQL itself closes the window."""
    db, engine = fresh
    who = admin()
    bootstrap_first_organization(engine, who, "First", commit=True)
    org = None
    with connect("vt_migrator", db) as conn:
        org = conn.execute("SELECT id FROM vt.organizations").fetchone()
        assert org is not None
        assert sqlstate(conn, "INSERT INTO vt.organizations (name) VALUES ('Second')") == "42501"
        assert (
            sqlstate(
                conn,
                "INSERT INTO vt.app_users (id, email) VALUES (%s, 'e@example.invalid')",
                uuid.uuid4(),
            )
            == "42501"
        )
        assert (
            sqlstate(
                conn,
                "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'org_admin')",
                org[0],
                who.user_id,
            )
            == "42501"
        )
        assert (
            sqlstate(
                conn,
                "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type) "
                "VALUES (%s, %s, 'org_admin', 'bootstrap.first_organization', 'organization')",
                org[0],
                who.user_id,
            )
            == "42501"
        )
        assert conn.execute(
            "SELECT vt.bootstrap_is_open(), vt.bootstrap_membership_open(), vt.bootstrap_audit_open()"
        ).fetchone() == (False, False, False)
    assert counts(db) == (1, 1, 1, 1)


def test_before_bootstrap_the_policies_only_allow_the_intended_rows(
    fresh: tuple[str, Engine],
) -> None:
    db, _ = fresh
    with connect("vt_migrator", db) as conn:
        assert (
            sqlstate(
                conn, "INSERT INTO vt.organizations (name, status) VALUES ('Acme', 'suspended')"
            )
            == "42501"
        )
        assert (
            sqlstate(
                conn,
                "INSERT INTO vt.app_users (id, email, status) VALUES (%s, 'x@example.invalid', 'disabled')",
                uuid.uuid4(),
            )
            == "42501"
        )
        # no membership without an organization, and never a non-admin one
        assert (
            sqlstate(
                conn,
                "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'org_admin')",
                uuid.uuid4(),
                uuid.uuid4(),
            )
            == "42501"
        )
        assert (
            sqlstate(
                conn,
                "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type) VALUES (%s, %s, 'org_admin', 'other.action', 'x')",
                uuid.uuid4(),
                uuid.uuid4(),
            )
            == "42501"
        )


def test_inside_one_transaction_only_a_single_organization_can_be_created(
    fresh: tuple[str, Engine],
) -> None:
    db, _ = fresh
    with connect("vt_migrator", db) as conn:
        conn.autocommit = False
        conn.execute("INSERT INTO vt.organizations (name) VALUES ('One')")
        assert sqlstate(conn, "INSERT INTO vt.organizations (name) VALUES ('Two')") == "42501"
        conn.rollback()
    assert counts(db)[0] == 0


def test_the_migration_role_cannot_add_other_roles_or_vendor_scoped_memberships(
    fresh: tuple[str, Engine],
) -> None:
    db, _ = fresh
    user = uuid.uuid4()
    with connect("vt_migrator", db) as conn:
        conn.autocommit = False
        conn.execute(
            "INSERT INTO vt.app_users (id, email) VALUES (%s, 'a@example.invalid')", (user,)
        )
        org = conn.execute(
            "INSERT INTO vt.organizations (name) VALUES ('Acme') RETURNING id"
        ).fetchone()
        assert org is not None
        for role in ("analyst", "reviewer", "auditor", "vendor_user"):
            conn.execute("SAVEPOINT attempt")
            state = sqlstate(
                conn,
                "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
                org[0],
                user,
                role,
            )
            conn.execute("ROLLBACK TO SAVEPOINT attempt")
            assert state in {"42501", "23514"}, role
        conn.rollback()


def test_the_application_role_still_cannot_create_organizations_or_call_the_helpers(
    fresh: tuple[str, Engine],
) -> None:
    db, _ = fresh
    with connect("vt_app", db) as conn:
        assert sqlstate(conn, "INSERT INTO vt.organizations (name) VALUES ('Mine')") == "42501"
        assert sqlstate(conn, "SELECT vt.bootstrap_is_open()") == "42501"
        assert sqlstate(conn, "SELECT vt.bootstrap_membership_open()") == "42501"


def test_two_simultaneous_bootstraps_produce_exactly_one_organization(
    fresh: tuple[str, Engine],
) -> None:
    db, _ = fresh
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def run(email: str) -> None:
        engine = create_engine(socket_url("vt_migrator", db), poolclass=pool.NullPool)
        try:
            barrier.wait()
            bootstrap_first_organization(engine, admin(email), f"Org for {email}", commit=True)
            outcomes.append("ok")
        except BootstrapError:
            outcomes.append("refused")
        finally:
            engine.dispose()

    threads = [threading.Thread(target=run, args=(f"p{i}@example.invalid",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ok", "refused"]
    assert counts(db) == (1, 1, 1, 1)


@pytest.mark.parametrize("user", ["vt_app", "vt_dev_admin"])
def test_only_the_plain_migration_role_may_run_it(fresh: tuple[str, Engine], user: str) -> None:
    db, _ = fresh
    engine = create_engine(socket_url(user, db), poolclass=pool.NullPool)
    try:
        with pytest.raises(BootstrapError, match="vt_migrator"):
            bootstrap_first_organization(engine, admin(), "Acme", commit=True)
    finally:
        engine.dispose()
    assert counts(db) == (0, 0, 0, 0)


# --- proving the administrator's identity -----------------------------------------------------------------


def test_the_administrator_is_proven_by_signing_in_and_a_verified_token(
    dev_auth: tuple[str, Any],
) -> None:
    base, auth = dev_auth
    user = auth.sign_up("root@example.invalid", "correct horse")
    verifier = verifier_from_settings(Settings(supabase_url=base))
    who = verify_admin_identity(base, "pub", verifier, " Root@Example.invalid ", "correct horse")
    assert (str(who.user_id), who.email) == (user["id"], "root@example.invalid")


def test_bad_credentials_unknown_accounts_and_missing_configuration_are_refused(
    dev_auth: tuple[str, Any],
) -> None:
    base, auth = dev_auth
    auth.sign_up("root@example.invalid", "correct horse")
    verifier = verifier_from_settings(Settings(supabase_url=base))
    with pytest.raises(BootstrapError, match="could not sign in"):
        verify_admin_identity(base, "pub", verifier, "root@example.invalid", "wrong password")
    with pytest.raises(BootstrapError, match="could not sign in"):
        verify_admin_identity(base, "pub", verifier, "ghost@example.invalid", "correct horse")
    with pytest.raises(BootstrapError, match="VT_SUPABASE_URL"):
        verify_admin_identity(None, "pub", verifier, "root@example.invalid", "correct horse")
    with pytest.raises(BootstrapError, match="PUBLISHABLE_KEY"):
        verify_admin_identity(base, None, verifier, "root@example.invalid", "correct horse")
    with pytest.raises(BootstrapError, match="reach"):
        verify_admin_identity("http://127.0.0.1:9", "pub", verifier, "root@example.invalid", "x")


def test_a_token_from_an_untrusted_issuer_does_not_verify(
    dev_auth: tuple[str, Any], tmp_path: Any
) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    from scripts.dev_auth import DevAuth

    base, auth = dev_auth
    auth.sign_up("root@example.invalid", "correct horse")
    other = DevAuth("pending", ec.generate_private_key(ec.SECP256R1()))
    # the API trusts a different issuer's keys than the one that signs this administrator's token
    wrong_base = base  # same URL, but the verifier below is pointed at a server with other keys
    with pytest.raises(BootstrapError):
        verify_admin_identity(
            wrong_base, "pub", _verifier_for_keys(other), "root@example.invalid", "correct horse"
        )


def _verifier_for_keys(other: Any) -> Any:
    from app.auth import JwtVerifier

    return JwtVerifier(
        issuer=other.issuer,
        audience="authenticated",
        key_resolver=lambda _t: other._key.public_key(),
    )


# --- the command line ----------------------------------------------------------------------------------------


@pytest.fixture
def cli_env(
    fresh: tuple[str, Engine], dev_auth: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> tuple[str, Any]:
    db, _ = fresh
    base, auth = dev_auth
    auth.sign_up("root@example.invalid", CANARY)
    monkeypatch.setenv("VT_SUPABASE_URL", base)
    monkeypatch.setenv("VT_SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    monkeypatch.setenv("VT_MIGRATION_DATABASE_URL", socket_url("vt_migrator", db))
    return db, auth


def run_cli(monkeypatch: pytest.MonkeyPatch, *extra: str, password: str = CANARY) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    return main(
        [
            "--org-name",
            "Acme Ltd",
            "--admin-email",
            "root@example.invalid",
            "--password-stdin",
            *extra,
        ]
    )


def test_the_cli_is_a_dry_run_unless_told_to_commit(
    cli_env: tuple[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _ = cli_env
    assert run_cli(monkeypatch) == 0
    out = capsys.readouterr().out
    assert "DRY RUN OK" in out and "NOTHING WAS CHANGED" in out
    assert counts(db) == (0, 0, 0, 0)
    assert run_cli(monkeypatch, "--commit") == 0
    assert "Bootstrap COMMITTED" in capsys.readouterr().out
    assert counts(db) == (1, 1, 1, 1)


def test_the_cli_refuses_a_second_run_and_a_wrong_password(
    cli_env: tuple[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _ = cli_env
    assert run_cli(monkeypatch, "--commit", password="not the password") == 1
    assert "could not sign in" in capsys.readouterr().err
    assert counts(db) == (0, 0, 0, 0)
    assert run_cli(monkeypatch, "--commit") == 0
    capsys.readouterr()
    assert run_cli(monkeypatch, "--commit") == 1
    assert "already bootstrapped" in capsys.readouterr().err
    assert counts(db) == (1, 1, 1, 1)


def test_the_cli_never_prints_the_password_or_a_connection_url(
    cli_env: tuple[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_cli(monkeypatch, "--commit")
    run_cli(monkeypatch, "--commit")
    run_cli(monkeypatch, "--commit", password="wrong")
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err
    assert "postgresql" not in captured.out + captured.err


def test_the_cli_needs_a_password_and_the_migration_credential(
    cli_env: tuple[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(monkeypatch, password="") == 2
    monkeypatch.delenv("VT_MIGRATION_DATABASE_URL")
    monkeypatch.setenv("HOME", "/nonexistent")
    assert run_cli(monkeypatch) == 2
    assert "credentials are not configured" in capsys.readouterr().err
    monkeypatch.setenv("VT_MIGRATION_DATABASE_URL", socket_url("vt_app", cli_env[0]))
    assert run_cli(monkeypatch) == 2  # the application role's URL is refused by the loader
    assert "role family" in capsys.readouterr().err


def test_the_migration_role_can_only_ever_write_the_one_bootstrap_audit_event(
    fresh: tuple[str, Engine],
) -> None:
    """With exactly one organization and no bootstrap event, only that event may be written, once."""
    db, _ = fresh
    user = uuid.uuid4()
    with connect(SUPERUSER_ROLE, db) as su:
        su.execute("INSERT INTO vt.app_users (id, email) VALUES (%s, 'u@example.invalid')", (user,))
        org = su.execute(
            "INSERT INTO vt.organizations (name) VALUES ('Acme') RETURNING id"
        ).fetchone()
        assert org is not None
    insert = (
        "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type) "
        "VALUES (%s, %s, %s, %s, 'organization')"
    )
    with connect("vt_migrator", db) as conn:
        assert sqlstate(conn, insert, org[0], user, "org_admin", "vendor.create") == "42501"
        assert (
            sqlstate(conn, insert, org[0], user, "analyst", "bootstrap.first_organization")
            == "42501"
        )
        assert (
            sqlstate(conn, insert, org[0], user, "org_admin", "bootstrap.first_organization")
            is None
        )
        assert (
            sqlstate(conn, insert, org[0], user, "org_admin", "bootstrap.first_organization")
            == "42501"
        )
        # and it can read back only that event, nothing else in the log
        assert conn.execute("SELECT count(*) FROM vt.audit_log").fetchone() == (1,)
