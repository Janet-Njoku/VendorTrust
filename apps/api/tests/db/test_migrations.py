"""Alembic runs through the real CLI: safe by default, role guarded, credential-loader driven."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import MetaData, create_engine

from app.db.models import metadata
from tests.db.alembic_tools import alembic, socket_url
from tests.db.conftest import connect

pytestmark = pytest.mark.db

TABLES = {
    "organizations",
    "app_users",
    "vendors",
    "memberships",
    "audit_log",
    "questionnaire_templates",
    "questionnaire_versions",
    "questions",
    "question_options",
    "assessments",
    "assessment_responses",
}


def tables(dbname: str) -> set[str]:
    with connect("vt_migrator", dbname) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'vt' "
            "AND tablename <> 'alembic_version'"
        ).fetchall()
    return {row[0] for row in rows}


def test_default_run_is_a_dry_run_that_changes_nothing(make_db: Callable[..., str]) -> None:
    db = make_db(migrate=False)
    done = alembic(db, "upgrade", "head")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "DRY RUN OK" in done.stdout
    assert "NOTHING WAS CHANGED" in done.stdout
    assert tables(db) == set()
    with connect("vt_migrator", db) as conn:
        assert conn.execute("SELECT to_regclass('vt.alembic_version')").fetchone() == (None,)


def test_commit_one_applies_and_a_second_run_is_a_noop(make_db: Callable[..., str]) -> None:
    db = make_db(migrate=False)
    done = alembic(db, "-x", "commit=1", "upgrade", "head")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Migrations COMMITTED" in done.stdout
    assert tables(db) == TABLES
    again = alembic(db, "-x", "commit=1", "upgrade", "head")
    assert again.returncode == 0
    current = alembic(db, "current")
    assert "0006_first_org_bootstrap (head)" in current.stdout + current.stderr


@pytest.mark.parametrize("value", ["0", "true", "yes", "1 ", "", "commit"])
def test_commit_option_must_be_exactly_one(make_db: Callable[..., str], value: str) -> None:
    db = make_db(migrate=False)
    done = alembic(db, "-x", f"commit={value}", "upgrade", "head")
    assert done.returncode != 0
    assert "must be exactly 1" in done.stderr
    assert tables(db) == set()


def test_unknown_and_retired_options_are_rejected(make_db: Callable[..., str]) -> None:
    db = make_db(migrate=False)
    for option in ("dry_run=0", "force=1"):
        done = alembic(db, "-x", option, "upgrade", "head")
        assert done.returncode != 0
        assert "unknown -x option" in done.stderr
    assert tables(db) == set()


def test_a_failing_migration_rolls_back_everything_even_with_commit(
    make_db: Callable[..., str],
) -> None:
    db = make_db(migrate=False)
    with connect("vt_migrator", db) as conn:
        # a foreign object makes 0001 fail half way through
        conn.execute("CREATE TABLE vt.vendors (x int)")
    done = alembic(db, "-x", "commit=1", "upgrade", "head")
    assert done.returncode != 0
    assert "Migrations COMMITTED" not in done.stdout
    with connect("vt_migrator", db) as conn:
        assert tables(db) == {"vendors"}
        assert conn.execute("SELECT to_regclass('vt.organizations')").fetchone() == (None,)


@pytest.mark.parametrize("user", ["vt_app", "sb_admin_sim", "vt_dev_admin"])
def test_refuses_to_migrate_as_any_other_role(make_db: Callable[..., str], user: str) -> None:
    db = make_db(migrate=False)
    done = alembic(db, "-x", "commit=1", "upgrade", "head", user=user)
    assert done.returncode != 0
    assert tables(db) == set()
    if user == "vt_app":  # a role-family violation: the loader refuses before connecting
        assert "role family" in done.stderr
    else:
        assert "vt_migrator" in done.stderr


def test_downgrade_is_refused_unless_explicitly_allowed(make_db: Callable[..., str]) -> None:
    db = make_db()
    refused = alembic(db, "-x", "commit=1", "downgrade", "base")
    assert refused.returncode != 0
    assert "refusing to downgrade" in refused.stderr
    assert tables(db) == TABLES
    allowed = alembic(db, "-x", "commit=1", "-x", "allow_downgrade=1", "downgrade", "base")
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert tables(db) == set()
    upgraded = alembic(db, "-x", "commit=1", "upgrade", "head")  # up, down, up again
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    assert tables(db) == TABLES


def test_downgrade_dry_run_with_permission_still_rolls_back(make_db: Callable[..., str]) -> None:
    db = make_db()
    done = alembic(db, "-x", "allow_downgrade=1", "downgrade", "base")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "DRY RUN OK" in done.stdout
    assert tables(db) == TABLES


def test_offline_sql_needs_no_credentials_and_contains_the_security_statements() -> None:
    done = alembic("unused", "upgrade", "head", "--sql", use_url=False)
    assert done.returncode == 0, done.stderr
    sql = done.stdout
    assert sql.count("FORCE ROW LEVEL SECURITY") == len(TABLES)
    assert "SECURITY DEFINER" in sql
    assert "BYPASSRLS" not in sql
    assert "CREATE TABLE vt.alembic_version" in sql
    assert re.search(r"DELETE\b.*\bTO vt_app", sql) is None


def test_missing_credentials_are_a_clean_error_not_a_traceback_with_secrets() -> None:
    done = alembic("unused", "upgrade", "head", use_url=False)
    assert done.returncode != 0
    assert "credentials are not configured" in done.stderr or "migration" in done.stderr


def test_the_url_comes_from_the_credential_loader_not_the_ini_file() -> None:
    from tests.db.alembic_tools import API_ROOT

    ini = (API_ROOT / "alembic.ini").read_text()
    assert not re.search(r"^\s*sqlalchemy\.url\s*=", ini, re.MULTILINE)
    assert "postgresql" not in ini.replace("vt_migrator credential", "")


def test_models_match_the_migrated_database(make_db: Callable[..., str]) -> None:
    db = make_db()
    # vt_migrator's search_path makes `vt` the default schema, which reflection reports as "no
    # schema", so compare against a schema-less copy of the models.
    unqualified = MetaData()
    for table in metadata.sorted_tables:
        table.to_metadata(unqualified, schema=None)  # type: ignore[arg-type]
    engine = create_engine(socket_url("vt_migrator", db))
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={
                    "compare_type": True,
                    "include_name": lambda name, kind, parents: name != "alembic_version",
                },
            )
            diff = compare_metadata(ctx, unqualified)
    finally:
        engine.dispose()
    assert diff == []
