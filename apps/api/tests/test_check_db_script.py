"""scripts/check_db_connection.py: it must use the secure loader and never print a secret.

The actual database connection is replaced by a fake; nothing here touches a network.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from scripts import check_db_connection as dbc
from tests._creds import (
    APP_URL,
    CANARY,
    HOST,
    MIGRATION_URL,
    app_file,
    assert_no_canary,
    migration_file,
    secure_dir,
)


@pytest.fixture
def seen_urls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake_run(url: str, kind: str) -> list[dbc.Check]:
        calls.append((url, kind))
        return [dbc.Check("fake connection", True)]

    monkeypatch.setattr(dbc, "run", fake_run)
    return calls


def test_uses_an_explicit_credential_file_and_hides_the_password(
    tmp_path: Path, seen_urls: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    path = app_file(secure_dir(tmp_path))
    assert dbc.main(["--kind", "app", "--env-file", str(path)]) == 0
    out = capsys.readouterr()
    assert seen_urls == [(APP_URL, "app")]  # the real code receives the full URL...
    assert_no_canary(out.out, out.err)  # ...but nothing secret is ever printed
    assert f"Source: {path}" in out.out
    assert f"Target: vt_app.fakeproj@{HOST}:5432/postgres" in out.out


def test_defaults_to_the_standard_location_outside_the_repository(
    isolated_home: Path, seen_urls: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    folder = secure_dir(isolated_home / ".config")
    app_file(folder)
    migration_file(folder)
    assert dbc.main(["--kind", "app"]) == 0
    assert dbc.main(["--kind", "migrator"]) == 0
    assert [(u, k) for u, k in seen_urls] == [(APP_URL, "app"), (MIGRATION_URL, "migrator")]
    out = capsys.readouterr()
    assert_no_canary(out.out, out.err)


def test_relative_and_tilde_paths_are_expanded(
    isolated_home: Path,
    seen_urls: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = secure_dir(isolated_home / ".config")
    app_file(folder)
    assert dbc.main(["--env-file", "~/.config/vendortrust/api.env"]) == 0
    monkeypatch.chdir(folder)
    assert dbc.main(["--env-file", "api.env"]) == 0
    assert len(seen_urls) == 2


def test_the_wrong_kind_for_a_file_is_refused(
    tmp_path: Path, seen_urls: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    path = app_file(secure_dir(tmp_path))
    assert dbc.main(["--kind", "migrator", "--env-file", str(path)]) == 2
    out = capsys.readouterr()
    assert "unexpected variable name" in out.err
    assert_no_canary(out.out, out.err)
    assert seen_urls == []


def test_nothing_configured_is_a_clear_configuration_error(
    seen_urls: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert dbc.main(["--kind", "app"]) == 2
    assert "not configured" in capsys.readouterr().err
    assert seen_urls == []


@pytest.mark.parametrize("problem", ["missing", "mode", "folder"])
def test_unsafe_or_missing_explicit_files_stop_before_any_connection(
    tmp_path: Path,
    seen_urls: list[tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
    problem: str,
) -> None:
    folder = secure_dir(tmp_path)
    path = folder / "api.env" if problem == "missing" else app_file(folder)
    if problem == "mode":
        path.chmod(0o644)
    if problem == "folder":
        folder.chmod(0o777)
    assert dbc.main(["--env-file", str(path)]) == 2
    out = capsys.readouterr()
    assert out.err.startswith("error: ")
    assert_no_canary(out.out, out.err)
    assert seen_urls == []


def test_a_folder_mode_warning_is_shown_but_does_not_fail(
    tmp_path: Path, seen_urls: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    folder = secure_dir(tmp_path)
    path = app_file(folder)
    folder.chmod(0o755)
    assert dbc.main(["--env-file", str(path)]) == 0
    assert "WARNING:" in capsys.readouterr().out


def test_connection_errors_are_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = app_file(secure_dir(tmp_path))

    def failing_run(url: str, kind: str) -> list[dbc.Check]:
        raise psycopg.OperationalError(f'connection to "{url}" failed: password {CANARY} rejected')

    monkeypatch.setattr(dbc, "run", failing_run)
    assert dbc.main(["--env-file", str(path)]) == 1
    out = capsys.readouterr()
    assert "FAIL  connection error" in out.out
    assert_no_canary(out.out, out.err)
    assert APP_URL not in out.out


def test_the_old_url_env_option_is_gone() -> None:
    with pytest.raises(SystemExit) as info:
        dbc.main(["--url-env", "VT_DATABASE_URL"])
    assert info.value.code == 2
