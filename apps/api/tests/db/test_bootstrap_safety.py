"""Safety properties of infra/db/bootstrap.sql, run exactly as documented:

    psql -X -v ON_ERROR_STOP=1 -f infra/db/bootstrap.sql

Each scenario starts from a fresh database in an isolated scratch cluster, so we can also
start from deliberately unsafe pre-existing states and prove the script refuses and changes
nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.db.conftest import ADMIN_ROLE, BOOTSTRAP_SQL, Scratch

pytestmark = pytest.mark.db


def _admin_option(role: str) -> str:
    return f"GRANT {role} TO {ADMIN_ROLE} WITH ADMIN OPTION, INHERIT FALSE, SET FALSE;"


# (id, setup SQL run as superuser, text that must appear in the error)
REJECTIONS = [
    ("app_nologin", "CREATE ROLE vt_app NOLOGIN;", "cannot log in"),
    ("app_superuser", "CREATE ROLE vt_app LOGIN SUPERUSER;", "superuser=t"),
    ("app_createdb", "CREATE ROLE vt_app LOGIN CREATEDB;", "createdb=t"),
    ("app_createrole", "CREATE ROLE vt_app LOGIN CREATEROLE;", "createrole=t"),
    ("app_replication", "CREATE ROLE vt_app LOGIN REPLICATION;", "replication=t"),
    ("app_bypassrls", "CREATE ROLE vt_app LOGIN BYPASSRLS;", "bypassrls=t"),
    ("migrator_nologin", "CREATE ROLE vt_migrator NOLOGIN;", "cannot log in"),
    ("migrator_superuser", "CREATE ROLE vt_migrator LOGIN SUPERUSER;", "superuser=t"),
    ("migrator_bypassrls", "CREATE ROLE vt_migrator LOGIN BYPASSRLS;", "bypassrls=t"),
    ("no_admin_option", "CREATE ROLE vt_app LOGIN;", "no ADMIN OPTION"),
    (
        "member_of_other_role",
        f"CREATE ROLE vt_app LOGIN; {_admin_option('vt_app')} GRANT anon TO vt_app;",
        "is a member of other roles",
    ),
    (
        "foreign_member",
        f"CREATE ROLE vt_app LOGIN; {_admin_option('vt_app')} GRANT vt_app TO authenticated;",
        "members other than the admin",
    ),
    (
        "app_owns_a_table",
        f"CREATE ROLE vt_app LOGIN; {_admin_option('vt_app')} "
        "CREATE TABLE public.leak (i int); ALTER TABLE public.leak OWNER TO vt_app;",
        "owns 1 unexpected object",
    ),
    (
        "migrator_owns_table_outside_vt",
        f"CREATE ROLE vt_migrator LOGIN; {_admin_option('vt_migrator')} "
        "CREATE TABLE public.leak (i int); ALTER TABLE public.leak OWNER TO vt_migrator;",
        "owns 1 unexpected object",
    ),
    (
        "schema_owned_by_someone_else",
        f"CREATE SCHEMA vt AUTHORIZATION {ADMIN_ROLE};",
        "refusing to take it over",
    ),
    (
        "schema_contains_foreign_table",
        f"CREATE ROLE vt_migrator LOGIN; {_admin_option('vt_migrator')} "
        "CREATE SCHEMA vt AUTHORIZATION vt_migrator; CREATE TABLE vt.foreign_t (i int);",
        "relations not owned by vt_migrator: foreign_t",
    ),
    (
        "schema_contains_foreign_function",
        f"CREATE ROLE vt_migrator LOGIN; {_admin_option('vt_migrator')} "
        "CREATE SCHEMA vt AUTHORIZATION vt_migrator; "
        "CREATE FUNCTION vt.foreign_f() RETURNS int LANGUAGE sql AS 'SELECT 1';",
        "functions not owned by vt_migrator: foreign_f",
    ),
    (
        "schema_grants_to_unexpected_role",
        f"CREATE ROLE vt_migrator LOGIN; {_admin_option('vt_migrator')} "
        "CREATE SCHEMA vt AUTHORIZATION vt_migrator; "
        "CREATE ROLE vt_t_other NOLOGIN; GRANT USAGE ON SCHEMA vt TO vt_t_other;",
        "unexpected role(s): vt_t_other",
    ),
]


@pytest.mark.parametrize(
    ("setup", "expected"), [r[1:] for r in REJECTIONS], ids=[r[0] for r in REJECTIONS]
)
def test_refuses_unexpected_state_and_changes_nothing(
    scratch: Scratch, setup: str, expected: str
) -> None:
    scratch.su(setup)
    before = scratch.state()
    done = scratch.bootstrap()
    assert done.returncode != 0, done.stdout
    assert expected in done.stderr, done.stderr
    assert "Bootstrap committed" not in done.stdout
    assert scratch.state() == before


def test_refusal_does_not_depend_on_the_on_error_stop_flag(scratch: Scratch) -> None:
    """The script sets ON_ERROR_STOP itself, so forgetting `-v ON_ERROR_STOP=1` is harmless."""
    scratch.su(f"CREATE SCHEMA vt AUTHORIZATION {ADMIN_ROLE};")
    before = scratch.state()
    done = scratch.bootstrap(on_error_stop=False)
    assert done.returncode != 0
    assert "refusing to take it over" in done.stderr
    assert scratch.state() == before


def test_existing_schema_ownership_is_never_changed(scratch: Scratch) -> None:
    scratch.su(f"CREATE SCHEMA vt AUTHORIZATION {ADMIN_ROLE};")
    assert scratch.bootstrap().returncode != 0
    owner = scratch.su("SELECT nspowner::regrole FROM pg_namespace WHERE nspname = 'vt'")
    assert owner == ADMIN_ROLE


def test_late_failure_rolls_back_everything(scratch: Scratch) -> None:
    """An error AFTER roles were created must leave no trace: the transaction rolls back."""
    scratch.su(
        "CREATE FUNCTION public.vt_block() RETURNS event_trigger LANGUAGE plpgsql "
        "AS $$ BEGIN RAISE EXCEPTION 'blocked by test'; END $$; "
        "CREATE EVENT TRIGGER vt_block ON ddl_command_start WHEN TAG IN ('CREATE SCHEMA') "
        "EXECUTE FUNCTION public.vt_block();"
    )
    before = scratch.state()
    done = scratch.bootstrap()
    assert done.returncode != 0
    assert "blocked by test" in done.stderr
    assert (
        scratch.su("SELECT count(*) FROM pg_roles WHERE rolname IN ('vt_app', 'vt_migrator')")
        == "0"
    )
    assert scratch.su("SELECT count(*) FROM pg_namespace WHERE nspname = 'vt'") == "0"
    assert scratch.state() == before


def test_default_run_executes_everything_then_rolls_back(scratch: Scratch) -> None:
    """No `commit` option: every statement runs, then ROLLBACK. Nothing persists."""
    before = scratch.state()
    done = scratch.bootstrap(commit=False)
    assert done.returncode == 0, done.stderr
    assert "DRY RUN OK" in done.stdout
    assert "NOTHING WAS CHANGED" in done.stdout
    assert "COMMITTED" not in done.stdout
    assert scratch.state() == before
    assert (
        scratch.su("SELECT count(*) FROM pg_roles WHERE rolname IN ('vt_app','vt_migrator')") == "0"
    )


@pytest.mark.parametrize(
    "name",
    ["comit", "Commit", "COMMIT", "commit_", "commits", "commit1", "dryrun", "apply", "yes", "c"],
)
def test_mistyped_option_name_rolls_back_instead_of_committing(scratch: Scratch, name: str) -> None:
    """psql defines any `-v name=1`, even misspelled ones. They must never cause a commit."""
    before = scratch.state()
    done = scratch.bootstrap(variables={name: "1"})
    assert done.returncode == 0, done.stderr
    assert "DRY RUN OK" in done.stdout
    assert "COMMITTED" not in done.stdout
    assert scratch.state() == before


@pytest.mark.parametrize(
    "value",
    ["0", "2", "01", "-1", "true", "TRUE", "yes", "on", "", " 1", "1 ", "11", "1;", "one",
     "1' OR '1'='1", "1'; DROP SCHEMA public CASCADE; --"],
)  # fmt: skip
def test_invalid_commit_value_is_rejected_before_anything_runs(
    scratch: Scratch, value: str
) -> None:
    before = scratch.state()
    done = scratch.bootstrap(variables={"commit": value})
    assert done.returncode != 0
    assert "invalid value for commit" in done.stderr
    assert "COMMITTED" not in done.stdout
    assert "DRY RUN OK" not in done.stdout  # it never reached the end
    assert scratch.state() == before


@pytest.mark.parametrize("variables", [{"dry_run": "1"}, {"dry_run": "0"}, {"dry_run": ""},
                                       {"dry_run": "1", "commit": "1"}])  # fmt: skip
def test_retired_dry_run_option_is_rejected(scratch: Scratch, variables: dict[str, str]) -> None:
    before = scratch.state()
    done = scratch.bootstrap(variables=variables)
    assert done.returncode != 0
    assert "dry_run is no longer supported" in done.stderr
    assert "COMMITTED" not in done.stdout
    assert scratch.state() == before


def test_explicit_commit_applies_the_bootstrap(scratch: Scratch) -> None:
    done = scratch.bootstrap(variables={"commit": "1"})
    assert done.returncode == 0, done.stderr
    assert "Bootstrap COMMITTED" in done.stdout
    assert "DRY RUN OK" not in done.stdout
    assert (
        scratch.su("SELECT count(*) FROM pg_roles WHERE rolname IN ('vt_app','vt_migrator')") == "2"
    )
    assert (
        scratch.su("SELECT nspowner::regrole FROM pg_namespace WHERE nspname = 'vt'")
        == "vt_migrator"
    )
    assert scratch.verify_failures() == []


def test_dry_run_then_commit_changes_state_only_on_the_second_run(scratch: Scratch) -> None:
    before = scratch.state()
    assert scratch.bootstrap(commit=False).returncode == 0
    assert scratch.state() == before
    assert scratch.bootstrap().returncode == 0
    assert scratch.state() != before
    assert scratch.verify_failures() == []


def test_v_without_equals_unsets_the_option_and_rolls_back(scratch: Scratch) -> None:
    """In psql, `-v commit` (no `=`) UNSETS the variable. That must mean rollback, never commit."""
    before = scratch.state()
    done = scratch.cluster.psql(
        ADMIN_ROLE, scratch.db, file=BOOTSTRAP_SQL, variables=None, extra=["-v", "commit"]
    )
    assert done.returncode == 0, done.stderr
    assert "DRY RUN OK" in done.stdout
    assert "COMMITTED" not in done.stdout
    assert scratch.state() == before


@pytest.mark.parametrize("value", ["0", "true", "yes", "on", "", "11", "1 ", "TRUE"])
def test_commit_check_at_the_end_holds_without_the_upfront_validation(
    scratch: Scratch, tmp_path: Path, value: str
) -> None:
    """Defense in depth: even if the up-front parameter check were removed, the check made at
    the moment of COMMIT must still refuse every value other than exactly 1."""
    text = BOOTSTRAP_SQL.read_text()
    start = text.index("\\if :{?dry_run}")
    end = text.index("BEGIN;\n", start)
    stripped = tmp_path / "bootstrap_without_upfront_check.sql"
    stripped.write_text(text[:start] + text[end:])

    before = scratch.state()
    done = scratch.cluster.psql(ADMIN_ROLE, scratch.db, file=stripped, variables={"commit": value})
    assert done.returncode == 0, done.stderr
    assert "DRY RUN OK" in done.stdout
    assert "COMMITTED" not in done.stdout
    assert scratch.state() == before


@pytest.mark.parametrize("admin_variant", ["default", "self_grant"])
def test_fresh_run_leaves_admin_memberships_exactly_as_role_creation_made_them(
    scratch: Scratch, admin_variant: str
) -> None:
    """The temporary SET membership must not leave a trace, whether or not the admin's
    configuration (createrole_self_grant) already gives it SET on roles it creates."""
    if admin_variant == "self_grant":
        scratch.su(f"ALTER ROLE {ADMIN_ROLE} SET createrole_self_grant = 'set, inherit'")
    scratch.as_role(ADMIN_ROLE, "CREATE ROLE vt_app LOGIN; CREATE ROLE vt_migrator LOGIN;")
    baseline = scratch.members()
    scratch.su("DROP ROLE vt_app; DROP ROLE vt_migrator;")

    done = scratch.bootstrap()
    assert done.returncode == 0, done.stderr
    assert scratch.members() == baseline
    assert scratch.verify_failures() == []

    again = scratch.bootstrap()  # idempotent
    assert again.returncode == 0, again.stderr
    assert scratch.members() == baseline
    assert scratch.verify_failures() == []


@pytest.mark.parametrize("admin_variant", ["default", "self_grant"])
def test_existing_memberships_survive_when_roles_pre_exist(
    scratch: Scratch, admin_variant: str
) -> None:
    if admin_variant == "self_grant":
        scratch.su(f"ALTER ROLE {ADMIN_ROLE} SET createrole_self_grant = 'set, inherit'")
    scratch.as_role(ADMIN_ROLE, "CREATE ROLE vt_app LOGIN; CREATE ROLE vt_migrator LOGIN;")
    before = scratch.members()
    done = scratch.bootstrap()
    assert done.returncode == 0, done.stderr
    assert scratch.members() == before


def test_pre_existing_self_granted_row_without_set_is_restored_exactly(scratch: Scratch) -> None:
    """A membership row the admin granted itself earlier (no SET) is modified temporarily
    and must be put back, not deleted."""
    scratch.as_role(ADMIN_ROLE, "CREATE ROLE vt_app LOGIN; CREATE ROLE vt_migrator LOGIN;")
    scratch.su(
        f"GRANT vt_migrator TO {ADMIN_ROLE} WITH SET FALSE, INHERIT FALSE GRANTED BY {ADMIN_ROLE}"
    )
    before = scratch.members()
    assert f"vt_migrator<-{ADMIN_ROLE} by {ADMIN_ROLE}" in before
    done = scratch.bootstrap()
    assert done.returncode == 0, done.stderr
    assert scratch.members() == before


def test_runs_cleanly_as_superuser_with_no_membership_changes(scratch: Scratch) -> None:
    done = scratch.bootstrap(user=scratch.cluster.superuser)
    assert done.returncode == 0, done.stderr
    assert scratch.members() == ""
    assert scratch.verify_failures(user=scratch.cluster.superuser) == []


def test_rerun_keeps_vt_objects_and_adds_no_default_grants(scratch: Scratch) -> None:
    assert scratch.bootstrap().returncode == 0
    scratch.as_role("vt_migrator", "CREATE TABLE vt.keep (i int)")
    done = scratch.bootstrap()  # schema now holds a vt_migrator-owned table: still expected
    assert done.returncode == 0, done.stderr
    assert scratch.su("SELECT tableowner FROM pg_tables WHERE tablename = 'keep'") == "vt_migrator"
    assert scratch.su("SELECT has_table_privilege('vt_app', 'vt.keep', 'SELECT')") == "f"


def test_requires_createrole(scratch: Scratch) -> None:
    scratch.su("CREATE ROLE vt_t_plain LOGIN;")
    before = scratch.state()
    done = scratch.bootstrap(user="vt_t_plain")
    assert done.returncode != 0
    assert "needs CREATEROLE" in done.stderr
    assert scratch.state() == before


def test_requires_create_on_database(scratch: Scratch) -> None:
    scratch.su("CREATE ROLE vt_t_nodb LOGIN CREATEROLE;")
    before = scratch.state()
    done = scratch.bootstrap(user="vt_t_nodb")
    assert done.returncode != 0
    assert "needs CREATE on database" in done.stderr
    assert scratch.state() == before


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_script_structure_guards_are_present() -> None:
    code = _code(BOOTSTRAP_SQL.read_text())
    assert "\\set ON_ERROR_STOP on" in code
    assert re.search(r"^BEGIN;", code, re.MULTILINE)
    assert code.index("BEGIN;") < code.index("CREATE ROLE")
    assert code.index("refusing to take it over") < code.index("CREATE ROLE")
    assert not re.search(r"\bOWNER\s+TO\b", code, re.IGNORECASE), "must never re-own a schema"
    assert "REVOKE vt_migrator FROM CURRENT_USER;" not in code, "revoke must be exact, not blanket"


def test_commit_is_reachable_only_through_the_exact_value_check() -> None:
    code = _code(BOOTSTRAP_SQL.read_text())
    assert code.count("COMMIT;") == 1
    assert code.count("ROLLBACK;") == 1
    # Parameter validation happens before BEGIN, so a bad value never opens a transaction.
    assert code.index("invalid value for commit") < code.index("BEGIN;")
    assert code.index("dry_run is no longer supported") < code.index("BEGIN;")
    # The only COMMIT sits in the true-branch of a check that compares against exactly '1';
    # ROLLBACK is the default (else) branch.
    tail = code[code.index("\\set do_commit false") :]
    assert "(:'commit' = '1') AS do_commit" in tail
    assert (
        tail.index("\\if :do_commit")
        < tail.index("COMMIT;")
        < tail.index("\\else")
        < tail.index("ROLLBACK;")
        < tail.rindex("\\endif")
    )
    assert "\\if :{?dry_run}" in code  # only to reject it
    assert "DRY RUN OK" in code
