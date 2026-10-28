"""infra/db/preflight.sql: read-only confirmation of the target before bootstrap.

The scratch cluster stands in for a Supabase project: its socket directory name doubles as
the "project reference" (the script matches the reference against the connection's user or
host, which on Supabase is `postgres.<ref>` or `db.<ref>.supabase.co`).
"""

from __future__ import annotations

import re

import pytest

from tests.db.conftest import ADMIN_ROLE, PREFLIGHT_SQL, Scratch

pytestmark = pytest.mark.db


def _supabase_like(scratch: Scratch) -> None:
    scratch.su("CREATE ROLE supabase_admin NOLOGIN; CREATE SCHEMA auth; CREATE SCHEMA storage;")


def test_passes_for_the_intended_project_and_changes_nothing(scratch: Scratch) -> None:
    _supabase_like(scratch)
    before = scratch.state()
    done = scratch.preflight(scratch.ref)
    assert done.returncode == 0, done.stderr
    assert "PREFLIGHT PASSED" in done.stdout
    assert f'PASS  connection targets project "{scratch.ref}"' in done.stdout
    assert "FAIL" not in done.stdout
    assert "vt_app and vt_migrator do not exist yet" in done.stdout
    assert scratch.state() == before


def test_fails_when_the_reference_names_a_different_project(scratch: Scratch) -> None:
    _supabase_like(scratch)
    done = scratch.preflight("abcdefghijklmnopqrst")
    assert done.returncode != 0
    assert "FAIL  connection targets project" in done.stdout
    assert "PREFLIGHT FAILED" in done.stdout


@pytest.mark.parametrize("ref", ["", "BAD REF", "UPPERCASE", "a b"])
def test_fails_for_malformed_reference(scratch: Scratch, ref: str) -> None:
    _supabase_like(scratch)
    done = scratch.preflight(ref)
    assert done.returncode != 0
    assert "FAIL  expected project reference looks valid" in done.stdout


def test_fails_when_reference_is_not_provided(scratch: Scratch) -> None:
    _supabase_like(scratch)
    done = scratch.preflight(None)
    assert done.returncode != 0
    assert "pass the project reference" in done.stdout + done.stderr


def test_fails_when_database_is_not_supabase(scratch: Scratch) -> None:
    done = scratch.preflight(scratch.ref)  # no supabase_admin role, auth or storage schemas
    assert done.returncode != 0
    assert "FAIL  database has Supabase markers" in done.stdout


def test_fails_when_admin_cannot_create_roles(scratch: Scratch) -> None:
    _supabase_like(scratch)
    scratch.su("CREATE ROLE vt_t_plain LOGIN;")
    done = scratch.preflight(scratch.ref, user="vt_t_plain")
    assert done.returncode != 0
    assert 'FAIL  admin role "vt_t_plain" can create roles' in done.stdout


def test_fails_when_admin_cannot_create_schemas(scratch: Scratch) -> None:
    _supabase_like(scratch)
    scratch.su("CREATE ROLE vt_t_nodb LOGIN CREATEROLE;")
    done = scratch.preflight(scratch.ref, user="vt_t_nodb")
    assert done.returncode != 0
    assert "FAIL  admin role has CREATE on database" in done.stdout


def test_fails_when_schema_vt_belongs_to_someone_else(scratch: Scratch) -> None:
    _supabase_like(scratch)
    scratch.su(f"CREATE SCHEMA vt AUTHORIZATION {ADMIN_ROLE};")
    done = scratch.preflight(scratch.ref)
    assert done.returncode != 0
    assert f'FAIL  existing schema vt is owned by "{ADMIN_ROLE}"' in done.stdout


def test_fails_when_existing_role_is_unsafe(scratch: Scratch) -> None:
    _supabase_like(scratch)
    scratch.su("CREATE ROLE vt_app LOGIN BYPASSRLS;")
    done = scratch.preflight(scratch.ref)
    assert done.returncode != 0
    assert 'FAIL  pre-existing role "vt_app"' in done.stdout


def test_reports_healthy_state_after_bootstrap(scratch: Scratch) -> None:
    _supabase_like(scratch)
    assert scratch.bootstrap().returncode == 0
    before = scratch.state()
    done = scratch.preflight(scratch.ref)
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'PASS  pre-existing role "vt_app"' in done.stdout
    assert 'PASS  existing schema vt is owned by "vt_migrator"' in done.stdout
    assert scratch.state() == before


def test_never_prints_credentials(scratch: Scratch) -> None:
    _supabase_like(scratch)
    out = scratch.preflight(scratch.ref).stdout.lower()
    assert "password" not in out


def _strip(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"'(?:[^']|'')*'", "''", sql)  # drop string literals


def test_script_is_read_only_by_construction() -> None:
    code = _strip(PREFLIGHT_SQL.read_text())
    assert re.search(r"^BEGIN READ ONLY;", code, re.MULTILINE)
    assert "COMMIT" not in code
    forbidden = r"\b(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|GRANT|REVOKE|TRUNCATE|COPY)\b"
    assert not re.search(forbidden, code), re.findall(forbidden, code)
