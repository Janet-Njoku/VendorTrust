"""Run the real `alembic` CLI against a throwaway test database as vt_migrator.

The URL is passed through the migration credential variable, exactly like production does it,
so these runs also exercise the credential loader. It holds no password: the local cluster
uses trust authentication on a unix socket.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from tests.db.conftest import host_port

API_ROOT = Path(__file__).resolve().parents[2]


def socket_url(user: str, dbname: str) -> str:
    host, port = host_port()
    return f"postgresql+psycopg://{user}@/{dbname}?host={quote(host, safe='/')}&port={port}"


def alembic(
    dbname: str,
    *args: str,
    user: str = "vt_migrator",
    extra_env: dict[str, str] | None = None,
    use_url: bool = True,
) -> subprocess.CompletedProcess[str]:
    child_env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG"}}
    if use_url:
        child_env["VT_MIGRATION_DATABASE_URL"] = socket_url(user, dbname)
    child_env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def upgrade(dbname: str) -> None:
    done = alembic(dbname, "-x", "commit=1", "upgrade", "head")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Migrations COMMITTED" in done.stdout, done.stdout
