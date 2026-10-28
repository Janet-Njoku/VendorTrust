"""The runbook's commands must do exactly what the runbook says.

These tests extract the bash blocks for Steps 4 to 7 from infra/db/RUNBOOK.md and execute them
literally (through a shell, with the same PG* environment variables Step 3 sets), against a
throwaway cluster that stands in for the Supabase project. If the runbook text and the scripts
ever drift apart, these fail.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from tests.db.conftest import REPO_ROOT, Scratch
from tests.db.pgtools import tool

pytestmark = pytest.mark.db

RUNBOOK = REPO_ROOT / "infra" / "db" / "RUNBOOK.md"


def _command(step: int) -> str:
    text = RUNBOOK.read_text()
    section = re.search(rf"^## Step {step}:.*?(?=^## )", text, re.MULTILINE | re.DOTALL)
    assert section, f"Step {step} not found in RUNBOOK.md"
    block = re.search(r"```bash\n(.*?)```", section.group(0), re.DOTALL)
    assert block, f"Step {step} has no bash block"
    return block.group(1).strip()


def _run(scratch: Scratch, command: str) -> subprocess.CompletedProcess[str]:
    psql = tool("psql")
    assert psql is not None
    env = {
        **os.environ,
        "PATH": f"{psql.parent}{os.pathsep}{os.environ['PATH']}",
        "PGHOST": str(scratch.cluster.sock),
        "PGPORT": scratch.cluster.port,
        "PGUSER": "sb_admin_sim",
        "PGDATABASE": scratch.db,
        "EXPECTED_REF": scratch.ref,
    }
    return subprocess.run(  # noqa: S602
        command, shell=True, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False
    )


def _supabase_like(scratch: Scratch) -> None:
    scratch.su("CREATE ROLE supabase_admin NOLOGIN; CREATE SCHEMA auth; CREATE SCHEMA storage;")


def test_step_5_has_no_commit_option_and_step_6_has_exactly_one() -> None:
    step5, step6 = _command(5), _command(6)
    assert "commit" not in step5
    assert "dry_run" not in step5
    assert step6.count("-v commit=1") == 1
    assert step6.replace(" -v commit=1", "") == step5
    assert "dry_run" not in RUNBOOK.read_text().replace("`dry_run`", "")


def test_runbook_steps_4_to_7_behave_exactly_as_documented(scratch: Scratch) -> None:
    _supabase_like(scratch)
    before = scratch.state()

    preflight = _run(scratch, _command(4))
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    assert "PREFLIGHT PASSED" in preflight.stdout

    dry = _run(scratch, _command(5))  # Step 5: nothing is applied
    assert dry.returncode == 0, dry.stderr
    assert "DRY RUN OK" in dry.stdout
    assert "NOTHING WAS CHANGED" in dry.stdout
    assert scratch.state() == before

    again = _run(scratch, _command(4))  # "should still say they do not exist yet"
    assert "vt_app and vt_migrator do not exist yet" in again.stdout

    real = _run(scratch, _command(6))  # Step 6: applies
    assert real.returncode == 0, real.stderr
    assert "Bootstrap COMMITTED" in real.stdout
    assert scratch.state() != before

    verify = _run(scratch, _command(7))  # Step 7: every row ok = t
    assert verify.returncode == 0, verify.stderr
    rows = [ln for ln in verify.stdout.splitlines() if ln.rstrip().endswith(("| t", "| f"))]
    assert len(rows) >= 15
    assert all(ln.rstrip().endswith("| t") for ln in rows), verify.stdout
