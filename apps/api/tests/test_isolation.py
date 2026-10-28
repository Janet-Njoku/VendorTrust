"""The test suite must never see the real home folder or inherited credential variables.

At import time (collection) this module deliberately plants fake "inherited" credential
variables in the process environment, exactly like a developer's shell might have them. The
autouse fixture in tests/conftest.py must strip them before any test runs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REAL_HOME_AT_COLLECTION = os.environ.get("HOME")
for _name in (
    "VT_DATABASE_URL",
    "VT_MIGRATION_DATABASE_URL",
    "VT_ENV_FILE",
    "VT_MIGRATION_ENV_FILE",
    "VT_ENVIRONMENT",
    "VT_SUPABASE_SERVICE_ROLE_KEY",
):
    os.environ[_name] = "inherited-fake-value-must-not-be-visible"


def test_inherited_credential_variables_are_stripped() -> None:
    leaked = [n for n in os.environ if n.startswith("VT_") and not n.startswith("VT_TEST_")]
    assert leaked == []


def test_home_points_at_an_empty_temporary_folder(isolated_home: Path) -> None:
    assert Path.home() == isolated_home
    assert os.environ["HOME"] == str(isolated_home)
    assert str(isolated_home) != REAL_HOME_AT_COLLECTION
    assert list(isolated_home.iterdir()) == []


def test_default_credential_path_resolves_inside_the_fake_home(isolated_home: Path) -> None:
    from app.credentials import CredentialsNotConfiguredError, load_database_credential

    with pytest.raises(CredentialsNotConfiguredError) as info:
        load_database_credential("app")
    assert str(isolated_home) in str(info.value)


def test_isolation_is_restored_between_tests(isolated_home: Path) -> None:
    assert Path.home() == isolated_home
    assert "VT_DATABASE_URL" not in os.environ
