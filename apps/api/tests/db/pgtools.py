"""Helpers that drive the real PostgreSQL command-line tools.

bootstrap.sql and preflight.sql use psql meta-commands (`\\set`, `\\if`), and the documented
procedure is `psql -X -v ON_ERROR_STOP=1 -f ...`, so tests execute them exactly that way.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
# Captured at import, before the test suite redirects HOME to an empty temporary folder.
_REAL_HOME = os.environ.get("HOME")


def _candidates(name: str) -> list[Path]:
    paths: list[Path] = []
    if env := os.environ.get("VT_TEST_PG_BIN"):
        paths.append(Path(env) / name)
    brew = shutil.which("brew")
    if brew:
        brew_env = {**os.environ, **({"HOME": _REAL_HOME} if _REAL_HOME else {})}
        done = subprocess.run(
            [brew, "--prefix", "postgresql@17"],
            capture_output=True,
            text=True,
            check=False,
            env=brew_env,
        )
        if done.returncode == 0:
            paths.append(Path(done.stdout.strip()) / "bin" / name)
    if found := shutil.which(name):
        paths.append(Path(found))
    paths.extend(sorted(Path("/usr/lib/postgresql").glob(f"*/bin/{name}"), reverse=True))
    return paths


def tool(name: str) -> Path | None:
    """Locate a PostgreSQL client/server binary (psql, initdb, pg_ctl), or None."""
    return next((p for p in _candidates(name) if p.is_file()), None)


def run_psql(
    *,
    host: str,
    port: str,
    user: str,
    dbname: str,
    file: Path | None = None,
    sql: str | None = None,
    variables: Mapping[str, str] | None = None,
    on_error_stop: bool = True,
    extra: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    """Run psql with -X (no psqlrc) and, by default, -v ON_ERROR_STOP=1, like the runbook."""
    psql = tool("psql")
    if psql is None:
        raise FileNotFoundError("psql not found (set VT_TEST_PG_BIN)")
    cmd = [str(psql), "-X", "-q", "-h", host, "-p", port, "-U", user, "-d", dbname]
    if on_error_stop:
        cmd += ["-v", "ON_ERROR_STOP=1"]
    for key, value in (variables or {}).items():
        cmd += ["-v", f"{key}={value}"]
    cmd += list(extra)
    if file is not None:
        cmd += ["-f", str(file)]
    if sql is not None:
        cmd += ["-At", "-c", sql]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=REPO_ROOT,
        env={**os.environ, "PGCONNECT_TIMEOUT": "5"},
    )


class Cluster:
    """A throwaway PostgreSQL cluster (unix socket only) used for destructive scenarios."""

    def __init__(self, base: Path, port: str = "54331") -> None:
        self.base = base
        self.data = base / "pg"
        self.sock = base / "sock"
        self.port = port
        self.ref = base.name  # lowercase alphanumerics: doubles as a fake Supabase project ref
        self.superuser = "vt_dev_admin"

    def start(self) -> None:
        initdb, pg_ctl = tool("initdb"), tool("pg_ctl")
        if initdb is None or pg_ctl is None:
            raise FileNotFoundError("initdb/pg_ctl not found (set VT_TEST_PG_BIN)")
        self.sock.mkdir(parents=True, exist_ok=True)
        self.sock.chmod(0o700)
        init_cmd = [
            str(initdb),
            "-D",
            str(self.data),
            "-U",
            self.superuser,
            "--auth-local=trust",
            "--auth-host=reject",
            "--encoding=UTF8",
            "--locale=C",
        ]
        subprocess.run(init_cmd, capture_output=True, check=True)
        server_opts = (
            f"-c listen_addresses='' -c unix_socket_directories='{self.sock}' -c port={self.port}"
        )
        start_cmd = [
            str(pg_ctl),
            "-D",
            str(self.data),
            "-w",
            "-l",
            str(self.base / "pg.log"),
            "-o",
            server_opts,
            "start",
        ]
        subprocess.run(start_cmd, capture_output=True, check=True)
        sim = REPO_ROOT / "infra" / "db" / "dev" / "supabase-sim.sql"
        done = self.psql(self.superuser, "postgres", file=sim)
        if done.returncode != 0:
            raise RuntimeError(done.stderr)

    def stop(self) -> None:
        pg_ctl = tool("pg_ctl")
        if pg_ctl and self.data.exists():
            stop_cmd = [str(pg_ctl), "-D", str(self.data), "-m", "immediate", "-w", "stop"]
            subprocess.run(stop_cmd, capture_output=True, check=False)
        shutil.rmtree(self.base, ignore_errors=True)

    def psql(self, user: str, dbname: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return run_psql(
            host=str(self.sock), port=self.port, user=user, dbname=dbname,
            **kwargs,  # type: ignore[arg-type]
        )  # fmt: skip

    def sql(self, query: str, *, dbname: str = "postgres", user: str | None = None) -> str:
        done = self.psql(user or self.superuser, dbname, sql=query)
        if done.returncode != 0:
            raise RuntimeError(f"SQL failed: {query}\n{done.stderr}")
        return done.stdout.strip()


def new_scratch_base() -> Path:
    """Short path inside the git-ignored .local-data (unix socket paths are length-limited)."""
    base = REPO_ROOT / ".local-data" / f"scratch{secrets.token_hex(3)}"
    base.mkdir(parents=True, exist_ok=True)
    return base
