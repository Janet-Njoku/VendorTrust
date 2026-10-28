"""Catalog guards: RLS on every table, exact grants, the single SECURITY DEFINER function, and the
documented limits of a compromised application credential."""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from collections.abc import Callable

import psycopg
import pytest
from psycopg import errors

from tests.db.conftest import SUPERUSER_ROLE, connect
from tests.db.rls_world import ALL_TABLES, app, begin, count, forge, seed

pytestmark = pytest.mark.db

APP_FUNCTIONS = {
    "begin_request(uuid,uuid)",
    "claim_matches_active_membership()",
    "claimed_org_id()",
    "claimed_role()",
    "claimed_user_id()",
    "claimed_vendor_id()",
    "clear_claims()",
}
ALL_FUNCTIONS = APP_FUNCTIONS | {
    "audit_log_append_only()",
    "guard_last_org_admin()",
    "set_updated_at()",
    "guard_version_update()",
    "guard_question_write()",
    "guard_option_write()",
    "guard_assessment_insert()",
    "guard_assessment_update()",
    "guard_response_write()",
    "bootstrap_is_open()",
    "bootstrap_membership_open()",
    "bootstrap_audit_open()",
}
# Changing the definer function is a security change: update this hash deliberately.
DEFINER_SOURCE_SHA256 = "0d193fe6b35498e0771b633878d9e08bf85a7fbd7a51e7d540ddd8deed158bf1"


def rows(db: str, sql: str, *params: object) -> list[tuple[object, ...]]:
    with connect("vt_migrator", db) as conn:
        return conn.execute(sql, params).fetchall()


def unprotected_tables(db: str) -> list[str]:
    """Tables in vt (other than Alembic's own) lacking RLS, FORCE, a vt_app policy or the owner."""
    return [
        str(r[0])
        for r in rows(
            db,
            "SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'vt'::regnamespace "
            "AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version' AND NOT ("
            " c.relrowsecurity AND c.relforcerowsecurity AND c.relowner = 'vt_migrator'::regrole "
            " AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid "
            "             AND p.polroles = ARRAY['vt_app'::regrole::oid]::oid[])"
            ") ORDER BY 1",
        )
    ]


def test_every_table_has_rls_forced_an_owner_and_a_policy(migrated_db: str) -> None:
    assert unprotected_tables(migrated_db) == []
    names = {
        r[0]
        for r in rows(
            migrated_db,
            "SELECT relname FROM pg_class WHERE relnamespace = 'vt'::regnamespace AND relkind = 'r'",
        )
    }
    assert names == {*ALL_TABLES, "alembic_version"}


def test_the_guard_flags_a_table_added_without_row_level_security(
    make_db: Callable[..., str],
) -> None:
    db = make_db()
    with connect("vt_migrator", db) as conn:
        conn.execute("CREATE TABLE vt.forgotten (id int)")
    assert unprotected_tables(db) == ["forgotten"]
    with connect("vt_migrator", db) as conn:
        conn.execute("ALTER TABLE vt.forgotten ENABLE ROW LEVEL SECURITY")
    assert unprotected_tables(db) == ["forgotten"]  # enabled but not FORCEd: still flagged
    with connect("vt_migrator", db) as conn:
        conn.execute("ALTER TABLE vt.vendors NO FORCE ROW LEVEL SECURITY")
    assert unprotected_tables(db) == ["forgotten", "vendors"]


def test_no_policy_applies_to_public_or_to_other_roles(migrated_db: str) -> None:
    for polname, roles in rows(
        migrated_db,
        "SELECT polname, ARRAY(SELECT r.rolname FROM pg_roles r WHERE r.oid = ANY (polroles)) "
        "FROM pg_policy WHERE polrelid::regclass::text LIKE 'vt.%%'",
    ):
        assert roles in (["vt_app"], ["vt_migrator"]), polname


def test_grants_are_exactly_the_documented_matrix(migrated_db: str) -> None:
    table_level: dict[str, set[str]] = defaultdict(set)
    other_grantees: set[object] = set()
    for table, grantee, privilege in rows(
        migrated_db,
        "SELECT c.relname, coalesce(r.rolname, 'PUBLIC'), a.privilege_type "
        "FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a "
        "LEFT JOIN pg_roles r ON r.oid = a.grantee "
        "WHERE c.relnamespace = 'vt'::regnamespace AND c.relkind = 'r' "
        "AND a.grantee <> c.relowner",
    ):
        if grantee == "vt_app":
            table_level[str(table)].add(str(privilege))
        else:
            other_grantees.add((table, grantee, privilege))
    assert other_grantees == set()
    assert dict(table_level) == {t: {"SELECT"} for t in ALL_TABLES}

    columns: dict[tuple[str, str], set[str]] = defaultdict(set)
    for table, column, grantee, privilege in rows(
        migrated_db,
        "SELECT c.relname, a.attname, coalesce(r.rolname, 'PUBLIC'), x.privilege_type "
        "FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid "
        "CROSS JOIN LATERAL aclexplode(a.attacl) x LEFT JOIN pg_roles r ON r.oid = x.grantee "
        "WHERE c.relnamespace = 'vt'::regnamespace AND a.attacl IS NOT NULL",
    ):
        assert grantee == "vt_app"
        columns[(str(table), str(privilege))].add(str(column))
    assert dict(columns) == {
        ("app_users", "INSERT"): {"id", "email", "display_name"},
        ("app_users", "UPDATE"): {"display_name"},
        ("memberships", "INSERT"): {"org_id", "user_id", "role", "vendor_id", "status"},
        ("memberships", "UPDATE"): {"role", "vendor_id", "status"},
        ("vendors", "INSERT"): {"org_id", "name", "status"},
        ("vendors", "UPDATE"): {"name", "status"},
        ("questionnaire_templates", "INSERT"): {"org_id", "name", "description"},
        ("questionnaire_templates", "UPDATE"): {"name", "description", "status"},
        ("questionnaire_versions", "INSERT"): {
            "org_id",
            "template_id",
            "version_number",
            "low_max",
            "medium_max",
            "high_max",
        },
        ("questionnaire_versions", "UPDATE"): {"status", "low_max", "medium_max", "high_max"},
        ("questions", "INSERT"): {
            "org_id",
            "version_id",
            "position",
            "category",
            "prompt",
            "guidance",
            "weight",
        },
        ("questions", "UPDATE"): {"position", "category", "prompt", "guidance", "weight", "status"},
        ("question_options", "INSERT"): {
            "org_id",
            "question_id",
            "position",
            "label",
            "risk_points",
        },
        ("question_options", "UPDATE"): {"position", "label", "risk_points", "status"},
        ("assessments", "INSERT"): {"org_id", "vendor_id", "version_id", "created_by"},
        ("assessments", "UPDATE"): {
            "status",
            "score",
            "risk_level",
            "scored_at",
            "scored_by",
            "breakdown",
        },
        ("assessment_responses", "INSERT"): {
            "org_id",
            "assessment_id",
            "question_id",
            "option_id",
            "comment",
            "answered_by",
        },
        ("assessment_responses", "UPDATE"): {"option_id", "comment", "answered_by"},
        ("audit_log", "INSERT"): {
            "org_id",
            "actor_user_id",
            "actor_role",
            "action",
            "entity_type",
            "entity_id",
            "before_data",
            "after_data",
        },
    }


def test_vt_app_has_no_delete_truncate_references_or_trigger_anywhere(migrated_db: str) -> None:
    for table in ALL_TABLES:
        for privilege in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert rows(
                migrated_db,
                "SELECT has_table_privilege('vt_app', %s, %s)",
                f"vt.{table}",
                privilege,
            ) == [(False,)]


def test_roles_have_no_dangerous_attributes(migrated_db: str) -> None:
    for role in ("vt_app", "vt_migrator"):
        assert rows(
            migrated_db,
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
            "FROM pg_roles WHERE rolname = %s",
            role,
        ) == [(False, False, False, False, False)]


# --- the SECURITY DEFINER function and the other helpers ---------------------------------------


def function_rows(db: str) -> list[tuple[str, bool, str, list[str] | None, list[str] | None]]:
    return rows(  # type: ignore[return-value]
        db,
        "SELECT p.proname || '(' || array_to_string(p.proargtypes::regtype[], ',') || ')', p.prosecdef, p.proowner::regrole::text, p.proconfig, "
        "ARRAY(SELECT coalesce(r.rolname, 'PUBLIC') FROM aclexplode(p.proacl) a "
        "      LEFT JOIN pg_roles r ON r.oid = a.grantee WHERE a.privilege_type = 'EXECUTE' "
        "      AND a.grantee <> p.proowner) "
        "FROM pg_proc p WHERE p.pronamespace = 'vt'::regnamespace ORDER BY 1",
    )


def test_exactly_one_security_definer_function_owned_by_the_migration_role(
    migrated_db: str,
) -> None:
    listed = function_rows(migrated_db)
    assert {r[0] for r in listed} == ALL_FUNCTIONS
    assert {r[0] for r in listed if r[1]} == {"claim_matches_active_membership()"}
    for name, _definer, owner, config, _grantees in listed:
        assert owner == "vt_migrator", name
        assert config is not None and "search_path=pg_catalog, pg_temp" in config, name


def test_function_execute_rights_are_explicit_and_never_public(migrated_db: str) -> None:
    # a NULL ACL is PostgreSQL's default, which grants EXECUTE to PUBLIC
    assert (
        rows(
            migrated_db,
            "SELECT proname FROM pg_proc WHERE pronamespace = 'vt'::regnamespace AND proacl IS NULL",
        )
        == []
    )
    for name, _definer, _owner, _config, grantees in function_rows(migrated_db):
        assert grantees is not None and "PUBLIC" not in grantees, name
        assert (grantees == ["vt_app"]) == (name in APP_FUNCTIONS), name


def test_the_definer_function_is_narrow_and_its_source_is_pinned(migrated_db: str) -> None:
    ((arguments, returns, language, source),) = rows(
        migrated_db,
        "SELECT pronargs, prorettype::regtype::text, l.lanname, prosrc FROM pg_proc p "
        "JOIN pg_language l ON l.oid = p.prolang "
        "WHERE p.pronamespace = 'vt'::regnamespace AND p.proname = 'claim_matches_active_membership'",
    )
    assert (arguments, returns, language) == (0, "boolean", "sql")
    text = str(source).lower()
    for forbidden in ("execute", "format(", "insert", "update ", "delete", "set_config", "dblink"):
        assert forbidden not in text, forbidden
    assert hashlib.sha256(str(source).encode()).hexdigest() == DEFINER_SOURCE_SHA256, (
        "the SECURITY DEFINER function changed: review it, then update DEFINER_SOURCE_SHA256"
    )


def test_temporary_objects_cannot_shadow_what_the_definer_function_reads(
    migrated_db: str,
) -> None:
    world = seed(migrated_db)
    with app(migrated_db) as conn:
        forged_user = str(uuid.uuid4())
        forged_org = str(uuid.uuid4())
        for table in ("memberships", "app_users", "organizations"):
            conn.execute(f"CREATE TEMP TABLE {table} (LIKE vt.{table} INCLUDING DEFAULTS)")
        conn.execute("CREATE TEMP TABLE memberships_x (x int)")
        conn.execute(
            "CREATE FUNCTION pg_temp.current_setting(text, boolean) RETURNS text "
            "LANGUAGE sql AS $$ SELECT 'x' $$"
        )
        conn.execute(
            "INSERT INTO pg_temp.organizations (id, name) VALUES (%s, 'fake')", (forged_org,)
        )
        conn.execute(
            "INSERT INTO pg_temp.app_users (id, email) VALUES (%s, 'f@example.invalid')",
            (forged_user,),
        )
        conn.execute(
            "INSERT INTO pg_temp.memberships (org_id, user_id, role) VALUES (%s, %s, 'org_admin')",
            (forged_org, forged_user),
        )
        forge(conn, forged_user, forged_org, "org_admin")
        assert conn.execute("SELECT vt.claim_matches_active_membership()").fetchone() == (False,)
        conn.rollback()
        begin(conn, world.a_admin)  # and the real context still resolves
        assert conn.execute("SELECT vt.claim_matches_active_membership()").fetchone() == (True,)


# --- limits: what a holder of the application credential can and cannot do ------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE vt.vendors DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE vt.vendors NO FORCE ROW LEVEL SECURITY",
        "DROP POLICY vendors_select ON vt.vendors",
        "ALTER POLICY vendors_select ON vt.vendors USING (true)",
        "CREATE POLICY open ON vt.vendors FOR ALL TO vt_app USING (true)",
        "CREATE TABLE vt.mine (id int)",
        "CREATE FUNCTION vt.evil() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        "CREATE OR REPLACE FUNCTION vt.claimed_user_id() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid'",
        "ALTER FUNCTION vt.claim_matches_active_membership() OWNER TO vt_app",
        "ALTER TABLE vt.audit_log DISABLE TRIGGER ALL",
        "TRUNCATE vt.vendors",
        "SET ROLE vt_migrator",
        "SET ROLE vt_dev_admin",
        "ALTER ROLE vt_app BYPASSRLS",
        "COPY vt.vendors TO PROGRAM 'true'",
    ],
)
def test_the_application_credential_cannot_change_the_security_model(
    migrated_db: str, statement: str
) -> None:
    with app(migrated_db) as conn, pytest.raises(psycopg.Error) as info:
        conn.execute(statement)
    assert isinstance(
        info.value,
        errors.InsufficientPrivilege
        | errors.InvalidParameterValue
        | errors.UndefinedObject
        | errors.SyntaxError,
    ), (
        statement,
        type(info.value).__name__,
    )


def test_limit_a_holder_of_the_credential_can_act_as_any_user_whose_id_they_know(
    migrated_db: str,
) -> None:
    """DOCUMENTED LIMIT, not a defended case: RLS cannot tell a real request from a forged one.

    begin_request trusts the id it is given. What bounds the damage: only that user's own
    organization and role are reachable, and ids are random UUIDs that cannot be enumerated.
    """
    world = seed(migrated_db)
    with app(migrated_db) as conn:
        begin(conn, world.a_admin)  # impersonation succeeds
        assert count(conn, "vendors") == 2
        assert count(conn, "audit_log") == 1  # that user's reach includes the audit log
        conn.rollback()
        begin(conn, world.a_reviewer)  # ... but only with that user's reach: read-only
        assert count(conn, "audit_log") == 0
        assert conn.execute("UPDATE vt.vendors SET name = 'x'").rowcount == 0


def test_limit_guessing_identifiers_gets_nothing(migrated_db: str) -> None:
    seed(migrated_db)
    with app(migrated_db) as conn:
        for _ in range(25):
            with pytest.raises(psycopg.Error) as info:
                begin(conn, str(uuid.uuid4()), str(uuid.uuid4()))
            assert info.value.sqlstate == "VT001"
            conn.rollback()
        forge(conn, str(uuid.uuid4()), str(uuid.uuid4()), "org_admin")
        assert count(conn, "vendors") == count(conn, "memberships") == count(conn, "app_users") == 0


def test_limit_the_membership_check_is_a_yes_no_oracle_for_a_chosen_triple(
    migrated_db: str,
) -> None:
    """DOCUMENTED LIMIT: with the credential, the helper confirms whether (user, org, role) is live."""
    world = seed(migrated_db)
    with app(migrated_db) as conn:
        forge(conn, world.a_admin, world.org_a, "org_admin")
        assert conn.execute("SELECT vt.claim_matches_active_membership()").fetchone() == (True,)
        forge(conn, world.a_admin, world.org_a, "analyst")
        assert conn.execute("SELECT vt.claim_matches_active_membership()").fetchone() == (False,)
        _ = SUPERUSER_ROLE


def test_granting_privileges_without_the_grant_option_changes_nothing(migrated_db: str) -> None:
    before = rows(
        migrated_db, "SELECT relacl::text FROM pg_class WHERE oid = 'vt.vendors'::regclass"
    )
    with app(migrated_db, autocommit=True) as conn:
        conn.execute(
            "GRANT ALL ON vt.vendors TO PUBLIC"
        )  # PostgreSQL only warns, and grants nothing
        conn.execute("GRANT DELETE ON vt.vendors TO vt_app")
    assert (
        rows(migrated_db, "SELECT relacl::text FROM pg_class WHERE oid = 'vt.vendors'::regclass")
        == before
    )


def test_limit_the_application_role_can_change_its_own_session_defaults(migrated_db: str) -> None:
    """DOCUMENTED LIMIT: PostgreSQL lets any role alter its own settings, so the role-level
    timeouts from bootstrap are defaults, not enforcement, against a compromised credential.
    (Run inside a transaction that is rolled back so the shared cluster is never changed.)"""
    with app(migrated_db) as conn:
        conn.execute("ALTER ROLE vt_app SET statement_timeout = 0")
        conn.execute("SET statement_timeout = 0")
        assert conn.execute("SHOW statement_timeout").fetchone() == ("0",)
        conn.rollback()
