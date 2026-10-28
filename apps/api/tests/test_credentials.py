"""Credential lookup: sources, precedence, production behaviour and role separation.

Every file is fake and lives in a temporary folder. HOME and all inherited VT_* variables are
isolated by tests/conftest.py, so the real ~/.config/vendortrust/ cannot be reached.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.credentials import (
    APP_ALLOWED_NAMES,
    CredentialError,
    CredentialsNotConfiguredError,
    DatabaseCredential,
    app_database_url,
    load_database_credential,
    migration_database_url,
)
from tests._creds import (
    APP_URL,
    CANARY,
    HOST,
    MIGRATION_URL,
    app_file,
    assert_no_canary,
    full_text,
    migration_file,
    secure_dir,
    write_file,
)

OTHER_APP_URL = APP_URL.replace("fakeproj", "otherproj").replace(CANARY, "OTHER-FAKE-PW")


@pytest.fixture
def cred_dir(isolated_home: Path) -> Path:
    """The default credential folder inside the isolated (fake) home."""
    return secure_dir(isolated_home / ".config")


# --- loading from the default location --------------------------------------------


def test_application_url_is_loaded_from_the_default_file(cred_dir: Path) -> None:
    app_file(cred_dir)
    credential = load_database_credential("app")
    assert credential.url.get_secret_value() == APP_URL
    assert credential.source == str(cred_dir / "api.env")
    assert credential.role == "app"


def test_migration_url_is_loaded_from_the_default_file(cred_dir: Path) -> None:
    migration_file(cred_dir)
    assert load_database_credential("migrator").url.get_secret_value() == MIGRATION_URL


def test_each_role_reads_only_its_own_file(cred_dir: Path) -> None:
    app_file(cred_dir)
    migration_file(cred_dir)
    assert app_database_url().get_secret_value() == APP_URL
    assert migration_database_url().get_secret_value() == MIGRATION_URL


def test_the_template_style_file_with_every_allowed_name_loads(cred_dir: Path) -> None:
    lines = [f"{name}=placeholder" for name in sorted(APP_ALLOWED_NAMES - {"VT_DATABASE_URL"})]
    write_file(cred_dir, "api.env", "\n".join([*lines, f"VT_DATABASE_URL={APP_URL}"]) + "\n")
    assert app_database_url().get_secret_value() == APP_URL


def test_directory_mode_warning_is_passed_on(cred_dir: Path) -> None:
    app_file(cred_dir)
    cred_dir.chmod(0o755)
    credential = load_database_credential("app")
    assert len(credential.warnings) == 1
    assert "0700" in credential.warnings[0]


# --- precedence -------------------------------------------------------------------


def test_process_environment_beats_the_file(cred_dir: Path) -> None:
    app_file(cred_dir)
    credential = load_database_credential("app", environ={"VT_DATABASE_URL": OTHER_APP_URL})
    assert credential.url.get_secret_value() == OTHER_APP_URL
    assert credential.source == "environment variable VT_DATABASE_URL"


def test_an_invalid_environment_url_is_rejected_without_falling_back(cred_dir: Path) -> None:
    app_file(cred_dir)
    bad = f"postgresql://postgres:{CANARY}@{HOST}/db?sslmode=require"
    with pytest.raises(CredentialError, match="role family") as info:
        load_database_credential("app", environ={"VT_DATABASE_URL": bad})
    assert_no_canary(full_text(info.value))


def test_vt_env_file_selects_an_explicit_file(tmp_path: Path, isolated_home: Path) -> None:
    path = app_file(secure_dir(tmp_path / "elsewhere"), url=OTHER_APP_URL)
    credential = load_database_credential("app", environ={"VT_ENV_FILE": str(path)})
    assert credential.url.get_secret_value() == OTHER_APP_URL
    assert credential.source == str(path)


def test_explicit_file_argument_beats_environment_and_default(
    cred_dir: Path, tmp_path: Path
) -> None:
    app_file(cred_dir)
    other = app_file(secure_dir(tmp_path / "other"), url=OTHER_APP_URL)
    credential = load_database_credential(
        "app", environ={"VT_DATABASE_URL": APP_URL}, env_file=other
    )
    assert credential.url.get_secret_value() == OTHER_APP_URL


def test_tilde_in_the_configured_path_uses_the_given_home(isolated_home: Path) -> None:
    app_file(secure_dir(isolated_home / "custom"))
    credential = load_database_credential(
        "app", environ={"VT_ENV_FILE": "~/custom/vendortrust/api.env"}, home=isolated_home
    )
    assert credential.url.get_secret_value() == APP_URL


def test_the_migration_file_has_its_own_path_variable(tmp_path: Path) -> None:
    path = migration_file(secure_dir(tmp_path / "m"))
    credential = load_database_credential("migrator", environ={"VT_MIGRATION_ENV_FILE": str(path)})
    assert credential.url.get_secret_value() == MIGRATION_URL


# --- absent versus explicitly configured ------------------------------------------


def test_absent_default_file_is_a_safe_not_configured_state(isolated_home: Path) -> None:
    with pytest.raises(CredentialsNotConfiguredError) as info:
        load_database_credential("app")
    message = str(info.value)
    assert str(isolated_home / ".config" / "vendortrust" / "api.env") in message
    assert "RUNBOOK" in message


@pytest.mark.parametrize("how", ["VT_ENV_FILE", "argument"])
def test_a_missing_explicitly_configured_file_fails_clearly(tmp_path: Path, how: str) -> None:
    missing = secure_dir(tmp_path / "empty") / "api.env"
    kwargs = (
        {"environ": {"VT_ENV_FILE": str(missing)}}
        if how == "VT_ENV_FILE"
        else {"env_file": missing}
    )
    with pytest.raises(CredentialError) as info:
        load_database_credential("app", **kwargs)
    assert not isinstance(info.value, CredentialsNotConfiguredError)
    assert "does not exist" in str(info.value)


def test_a_relative_explicit_path_is_refused() -> None:
    with pytest.raises(CredentialError, match="absolute"):
        load_database_credential("app", environ={"VT_ENV_FILE": "api.env"})


def test_insecure_files_are_refused_with_a_fix_hint(cred_dir: Path) -> None:
    path = app_file(cred_dir)
    path.chmod(0o644)
    with pytest.raises(CredentialError, match="chmod 600") as info:
        load_database_credential("app")
    assert_no_canary(full_text(info.value))


def test_a_group_writable_folder_is_refused(cred_dir: Path) -> None:
    app_file(cred_dir)
    cred_dir.chmod(0o775)
    with pytest.raises(CredentialError, match="writable by other users"):
        load_database_credential("app")


def test_a_file_inside_the_repository_is_refused(tmp_path: Path) -> None:
    path = app_file(secure_dir(tmp_path / "repo"))
    with pytest.raises(CredentialError, match="outside the git repository"):
        load_database_credential("app", env_file=path, repo_root=tmp_path / "repo")


# --- production -------------------------------------------------------------------


def test_production_never_reads_the_default_folder(cred_dir: Path) -> None:
    app_file(cred_dir)
    migration_file(cred_dir)
    for role in ("app", "migrator"):
        with pytest.raises(CredentialsNotConfiguredError, match="production"):
            load_database_credential(role, environ={"VT_ENVIRONMENT": "production"})


def test_production_accepts_an_explicit_file_or_explicit_variables(
    cred_dir: Path, tmp_path: Path
) -> None:
    explicit = app_file(secure_dir(tmp_path / "prod"), url=OTHER_APP_URL)
    by_file = load_database_credential(
        "app", environ={"VT_ENVIRONMENT": "production", "VT_ENV_FILE": str(explicit)}
    )
    by_variable = load_database_credential(
        "app", environ={"VT_ENVIRONMENT": "production", "VT_DATABASE_URL": APP_URL}
    )
    assert by_file.url.get_secret_value() == OTHER_APP_URL
    assert by_variable.url.get_secret_value() == APP_URL


def test_other_environments_use_the_default_folder(cred_dir: Path) -> None:
    app_file(cred_dir)
    for name in ("development", "test", "staging", ""):
        assert load_database_credential("app", environ={"VT_ENVIRONMENT": name}).role == "app"


# --- strict separation of the two roles -------------------------------------------


def test_the_api_file_cannot_hold_the_migration_credential(cred_dir: Path) -> None:
    app_file(cred_dir, extra=f"VT_MIGRATION_DATABASE_URL={MIGRATION_URL}\n")
    with pytest.raises(CredentialError, match="unexpected variable name") as info:
        load_database_credential("app")
    assert "VT_MIGRATION_DATABASE_URL" in str(info.value)
    assert_no_canary(full_text(info.value))


@pytest.mark.parametrize("extra", ["VT_DATABASE_URL", "VT_ENVIRONMENT", "PGPASSWORD", "VT_FOO"])
def test_the_migration_file_accepts_exactly_one_name(cred_dir: Path, extra: str) -> None:
    migration_file(cred_dir, extra=f"{extra}={CANARY}\n")
    with pytest.raises(CredentialError, match="unexpected variable name") as info:
        load_database_credential("migrator")
    assert_no_canary(full_text(info.value))


def test_a_migration_url_in_the_api_variable_is_rejected(cred_dir: Path) -> None:
    app_file(cred_dir, url=MIGRATION_URL)
    with pytest.raises(CredentialError, match="role family") as info:
        load_database_credential("app")
    assert_no_canary(full_text(info.value))


def test_an_application_url_in_the_migration_variable_is_rejected(cred_dir: Path) -> None:
    migration_file(cred_dir, url=APP_URL)
    with pytest.raises(CredentialError, match="role family") as info:
        load_database_credential("migrator")
    assert_no_canary(full_text(info.value))


def test_the_api_process_refuses_to_hold_the_migration_credential(cred_dir: Path) -> None:
    app_file(cred_dir)
    with pytest.raises(CredentialError, match="must not hold the migration credential") as info:
        load_database_credential("app", environ={"VT_MIGRATION_DATABASE_URL": MIGRATION_URL})
    assert_no_canary(full_text(info.value))


def test_the_migration_process_refuses_to_hold_the_application_credential(cred_dir: Path) -> None:
    migration_file(cred_dir)
    with pytest.raises(CredentialError, match="must not hold the application credential") as info:
        load_database_credential("migrator", environ={"VT_DATABASE_URL": APP_URL})
    assert_no_canary(full_text(info.value))


# --- missing values and bad URLs ------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", ["VT_ENVIRONMENT=development\n", "VT_DATABASE_URL=\n", 'VT_DATABASE_URL=""\n']
)
def test_a_missing_or_empty_url_is_an_error(cred_dir: Path, body: str) -> None:
    write_file(cred_dir, "api.env", body)
    with pytest.raises(CredentialError, match="missing or empty"):
        load_database_credential("app")


def test_a_remote_url_without_tls_is_rejected(cred_dir: Path) -> None:
    app_file(cred_dir, url=APP_URL.replace("?sslmode=require", ""))
    with pytest.raises(CredentialError, match="sslmode") as info:
        load_database_credential("app")
    assert_no_canary(full_text(info.value))


def test_a_malformed_file_is_reported_by_line_without_content(cred_dir: Path) -> None:
    write_file(cred_dir, "api.env", f"VT_ENVIRONMENT=development\nthis is junk {CANARY}\n")
    with pytest.raises(CredentialError, match="line 2 is malformed") as info:
        load_database_credential("app")
    assert_no_canary(full_text(info.value))


# --- output hygiene ---------------------------------------------------------------


def test_nothing_secret_appears_in_reprs_or_masked_values(cred_dir: Path) -> None:
    app_file(cred_dir)
    credential = load_database_credential("app")
    assert isinstance(credential, DatabaseCredential)
    assert isinstance(credential.url, SecretStr)
    assert_no_canary(repr(credential), str(credential), repr(credential.url), str(credential.url))
    assert credential.url.get_secret_value() == APP_URL


def _scenarios(cred_dir: Path) -> list[Callable[[], object]]:
    path = app_file(cred_dir)
    return [
        lambda: load_database_credential(
            "app", environ={"VT_MIGRATION_DATABASE_URL": MIGRATION_URL}
        ),
        lambda: load_database_credential("migrator", environ={"VT_DATABASE_URL": APP_URL}),
        lambda: load_database_credential("migrator"),  # no migrations.env: not configured
        lambda: load_database_credential("app", environ={"VT_DATABASE_URL": f"mysql://{CANARY}@h"}),
        lambda: load_database_credential("app", env_file=path, repo_root=path.parent.parent),
    ]


def test_no_error_path_ever_prints_the_password(cred_dir: Path) -> None:
    for scenario in _scenarios(cred_dir):
        with pytest.raises(CredentialError) as info:
            scenario()
        assert_no_canary(full_text(info.value))
    chmod_target = cred_dir / "api.env"
    chmod_target.chmod(0o666)
    with pytest.raises(CredentialError) as info:
        load_database_credential("app")
    assert_no_canary(full_text(info.value))


def test_loading_never_logs(cred_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    app_file(cred_dir)
    caplog.set_level(logging.DEBUG)
    load_database_credential("app")
    with pytest.raises(CredentialError):
        load_database_credential("migrator")
    assert [r for r in caplog.records if CANARY in r.getMessage()] == []
    assert caplog.records == []
