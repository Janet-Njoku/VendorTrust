"""Importing the app or building Settings must never read a credential file.

Credentials are loaded on demand by app.credentials, when database functionality needs them.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.main
from app import credentials, envfile
from app.config import Settings, get_settings
from tests._creds import APP_URL, CANARY, app_file, assert_no_canary, full_text, secure_dir


@pytest.fixture
def tripwire(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record (and fail on) any attempt to open a file under the fake home or load credentials."""
    opened: list[str] = []
    real_open = os.open

    def guarded_open(path: object, *args: object, **kwargs: object) -> int:
        if str(path).startswith(str(isolated_home)):
            opened.append(str(path))
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("credentials were loaded without being asked for")

    monkeypatch.setattr(os, "open", guarded_open)
    monkeypatch.setattr(envfile, "load_env_file", forbidden)
    monkeypatch.setattr(credentials, "load_env_file", forbidden)
    monkeypatch.setattr(credentials, "load_database_credential", forbidden)
    return opened


def test_import_settings_and_app_creation_never_touch_credential_files(
    isolated_home: Path, tripwire: list[str]
) -> None:
    # A perfectly valid credential file sits at the default location, and it even tries to
    # change a setting: none of it may be read.
    folder = secure_dir(isolated_home / ".config")
    app_file(folder, extra="VT_LOG_LEVEL=CANARY\n")
    importlib.reload(app.main)  # re-runs the module-level `app = create_app()`
    get_settings.cache_clear()
    settings = get_settings()
    assert Settings().environment == "development"
    assert settings.log_level == "INFO"
    assert tripwire == []


def test_settings_has_no_file_source_and_no_credential_fields() -> None:
    assert "env_file" not in Settings.model_config or Settings.model_config["env_file"] is None
    names = set(Settings.model_fields)
    # Only these public Supabase Auth endpoints may be URLs here; database URLs never are.
    public_urls = {"supabase_url", "supabase_jwks_url"}
    flagged = {name for name in names if "url" in name or "password" in name or "secret" in name}
    assert flagged == public_urls
    assert not {name for name in names if "database" in name or "dsn" in name}


def test_settings_validation_errors_do_not_echo_the_input() -> None:
    with pytest.raises(ValidationError) as info:
        Settings(environment=f"{CANARY}-not-an-environment")
    assert_no_canary(full_text(info.value))


def test_a_stray_env_file_in_the_working_directory_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(f"VT_ENVIRONMENT=production\nVT_DATABASE_URL={APP_URL}\n")
    monkeypatch.chdir(tmp_path)
    assert Settings().environment == "development"
