"""Fake credentials and helpers shared by the credential tests. Nothing here is real."""

from __future__ import annotations

import traceback
from pathlib import Path

# A fake password that must NEVER appear in any message, repr, log line or script output.
CANARY = "CANARY-PW-q8Zx41"
HOST = "aws-0-fake.pooler.example.invalid"  # .invalid never resolves: no real network, ever
APP_URL = f"postgresql+psycopg://vt_app.fakeproj:{CANARY}@{HOST}:5432/postgres?sslmode=require"
MIGRATION_URL = APP_URL.replace("vt_app.", "vt_migrator.")


def secure_dir(base: Path, name: str = "vendortrust", mode: int = 0o700) -> Path:
    directory = base / name
    directory.mkdir(parents=True)
    directory.chmod(mode)
    return directory


def write_file(directory: Path, name: str, text: str, mode: int = 0o600) -> Path:
    path = directory / name
    path.write_text(text)
    path.chmod(mode)
    return path


def app_file(directory: Path, extra: str = "", url: str = APP_URL) -> Path:
    body = f"VT_ENVIRONMENT=development   # inline comment\nVT_DATABASE_URL={url}\n{extra}"
    return write_file(directory, "api.env", body)


def migration_file(directory: Path, extra: str = "", url: str = MIGRATION_URL) -> Path:
    return write_file(directory, "migrations.env", f"VT_MIGRATION_DATABASE_URL={url}\n{extra}")


def full_text(exc: BaseException) -> str:
    """Everything an exception could reveal: message, repr, args and the whole traceback."""
    return " ".join([str(exc), repr(exc), repr(exc.args), "".join(traceback.format_exception(exc))])


def assert_no_canary(*things: object) -> None:
    for thing in things:
        assert CANARY not in str(thing), "a secret value leaked"
