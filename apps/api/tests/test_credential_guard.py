"""Regression tests for .claude/hooks/guard-credentials.py, the Bash credential tripwire.

The hook is driven exactly as Claude Code drives it (a JSON document on stdin, a decision on stdout or
a non-zero exit), as a subprocess, under every interpreter that might run it. A fake HOME is used: the
hook only matches text and never touches the filesystem, so the real credential directory is not
involved in any of these tests.

Covers: the crash on malformed bracket expressions (a bug where `re.error` escaped `fnmatch` and the
hook died, blocking harmless commands), that blocking still works around brackets, and that malformed
input fails closed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / ".claude" / "hooks" / "guard-credentials.py"

SYSTEM_PYTHON = (
    "/usr/bin/python3"  # the macOS interpreter a bare `python3` hook command may resolve to
)
INTERPRETERS = [sys.executable] + ([SYSTEM_PYTHON] if Path(SYSTEM_PYTHON).exists() else [])
ids = ["project-python"] + (["system-python3"] if len(INTERPRETERS) > 1 else [])


@pytest.fixture(params=INTERPRETERS, ids=ids)
def python(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def run_hook(
    stdin: str, python: str, home: Path, cwd: Path = REPO_ROOT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [python, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        cwd=cwd,
        timeout=30,
        check=False,
    )


def verdict(done: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    """("allow"|"deny"|"crash", reason). A crash exits non-zero, which Claude Code treats as a denial."""
    if done.returncode != 0:
        return "crash", done.stderr[-300:]
    if not done.stdout.strip():
        return "allow", ""
    output = json.loads(done.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    return "deny", output["permissionDecisionReason"]


def decide(command: str, python: str, home: Path, cwd: Path = REPO_ROOT) -> tuple[str, str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    return verdict(run_hook(payload, python, home, cwd))


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "fake-home"


# --- bracket characters: the crash this file exists for --------------------------------------------

MALFORMED_BRACKETS = [
    "echo [l-e",  # the shape that crashed the hook: a range with its end before its start
    "echo [z-a]",
    "echo x[0-9]*y",
    "ls [a-",
    "ls [",
    "ls []",
    "ls [[]",
    "ls [!",
    "ls src/[!a]*",
    "ls [[:alpha:]]*",
    "grep -n '[' README.md",
    "echo '[l-e' '[z-a]' '[a-'",
    "python3 -c \"import re; re.compile('[l-e')\"",
    "cat <<'EOF'\nprint(x[l-e], y[0:2], re.sub('[^a-z]', '', z), '[[:digit:]]')\nEOF",
    "sed -i '' -e 's/x[a-/y/' notes.txt",
    "ls ~/Projects/[a-",
    "ls " + "[" * 50,
    "ls " + "[a-z" * 40,
]


@pytest.mark.parametrize("command", MALFORMED_BRACKETS)
def test_malformed_bracket_expressions_do_not_crash_the_hook(
    command: str, python: str, home: Path
) -> None:
    kind, reason = decide(command, python, home)
    assert kind == "allow", (kind, reason)


@pytest.mark.skipif(not Path(SYSTEM_PYTHON).exists(), reason="needs the system interpreter")
def test_the_crash_is_real_on_the_system_interpreter() -> None:
    """Why the fix exists: plain fnmatch raises on a reversed range inside brackets on Python 3.9
    (the macOS system interpreter a bare `python3` hook command resolves to)."""
    script = "from fnmatch import fnmatchcase; fnmatchcase('x', '[l-e]')"
    done = subprocess.run(
        [SYSTEM_PYTHON, "-c", script], capture_output=True, text=True, check=False
    )
    if done.returncode == 0:
        pytest.skip("this interpreter's fnmatch no longer raises on a reversed range")
    assert "bad character range" in done.stderr


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("guard_credentials", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_glob_matching_still_works_and_treats_malformed_brackets_literally() -> None:
    hook = load_hook()
    assert hook._fnmatch("api.env", "api.e[n]v")  # a valid bracket is still a glob
    assert hook._fnmatch("api.env", "*.env")
    assert hook._fnmatch("a", "?")
    assert not hook._fnmatch("a[", "x")
    assert hook._fnmatch("[l-e", "[l-e")  # an unclosed bracket is plain text everywhere
    for pattern in ("[l-e]", "x[z-a]y", "[[", "[]", "[!]"):
        hook._fnmatch("anything", pattern)  # a malformed bracket expression never raises
    assert not hook._fnmatch("l", "[l-e")
    assert hook.glob_match("/h/.config/vendor*/api.env", "/h/.config/vendortrust/api.env")
    for malformed in ("/h/[l-e]/api.env", "/h/.config/[z-a]/api.env", "/h/[/api.env"):
        # no match and, above all, no crash (the protected paths never contain brackets)
        assert not hook.glob_match(malformed, "/h/.config/vendortrust/api.env")


# --- protections are intact, including around brackets ------------------------------------------------


BLOCKED = [
    ("cat ~/.config/vendortrust/api.env", "credential directory"),
    ("ls ~/.config/vendortrust", "credential directory"),
    ("cat $HOME/.config/vendortrust/migrations.env", "credential directory"),
    ("cat '~/.config/vendortrust/api.env'", "credential directory"),
    ('cat "~/.config/vendortrust/api.env', "credential directory"),  # unbalanced quote
    ("cat ~/.config/vendor*/api.env", "protected credential path"),
    ("cat ~/.config/vendortrus[t]/api.env", "protected credential path"),
    ("cat ~/.config/*/api.env", "protected credential path"),
    ("cat .env", "real env file"),
    ("cat ./.env", "real env file"),
    ("cat apps/api/.env", "real env file"),
    ("cat apps/api/.env.migrations", "real env file"),
    ("cat apps/web/.env.local", "real env file"),
    ("cp apps/api/.env /tmp/leak", "real env file"),
    ("source .env", "real env file"),
    ("less < .env", "real env file"),
    ("printenv", "environment"),
    ("env", "environment"),
    ("export -p", "environment"),
    ("echo $VT_DATABASE_URL", "credential variable"),
    ("echo ${PGPASSWORD}", "credential variable"),
    ("echo $VT_MIGRATION_DATABASE_URL", "credential variable"),
    ("grep -r token ~", "recursive"),
    ("cp -r ~/.config /tmp/x", "recursive"),
    ("tar czf x.tgz ~/.config", "recursive"),
    ("rsync -a ~/.config /tmp/x", "recursive"),
    # a newline ends a command like `;` does: the second line is judged on its own
    ("echo hi\nprintenv", "environment"),
    ("ls\nenv", "environment"),
    ("echo [l-e\nexport -p", "environment"),
    ("echo ok\ngrep -r token ~", "recursive"),
    ("echo ok\r\nprintenv", "environment"),
    ("echo a\n\n\nprintenv", "environment"),
]


@pytest.mark.parametrize(("command", "why"), BLOCKED)
def test_credential_access_is_still_blocked(
    command: str, why: str, python: str, home: Path
) -> None:
    kind, reason = decide(command, python, home)
    assert kind == "deny", (command, kind, reason)
    assert why in reason, (command, reason)


@pytest.mark.parametrize("noise", ["echo [l-e", "ls [a-", "echo '[z-a]'", "x=[0-9]*", "echo ]["])
@pytest.mark.parametrize(
    "secret", ["cat ~/.config/vendortrust/api.env", "cat apps/api/.env", "printenv"]
)
def test_brackets_elsewhere_in_the_command_do_not_hide_a_blocked_one(
    noise: str, secret: str, python: str, home: Path
) -> None:
    for joiner in (" && ", " ; ", " | ", "\n"):
        kind, reason = decide(f"{noise}{joiner}{secret}", python, home)
        assert kind == "deny", (noise, joiner, secret, kind, reason)


def test_a_blocked_path_inside_a_command_that_also_has_python_with_brackets(
    python: str, home: Path
) -> None:
    command = (
        "python3 - <<'EOF'\nimport re\nprint(re.findall('[l-e]', 'x'))\n"
        "open('/dev/null').read()\nEOF\ncat ~/.config/vendortrust/api.env"
    )
    assert decide(command, python, home)[0] == "deny"


ALLOWED = [
    "ls -la",
    "echo hello",
    "git status",
    "uv run pytest -q",
    "cat apps/api/.env.example",
    "cat apps/api/.env.migrations.example",
    "cat apps/web/.env.example",
    "git check-ignore apps/web/.env.local",
    "env VT_ENVIRONMENT=test uv run pytest",
    "grep -rn needle apps/api/app",
    "cp apps/api/pyproject.toml /tmp/copy.toml",
    "echo $HOME",
    "cat ~/.zshrc",
    "echo first\necho second",
    "cat > notes.txt <<'EOF'\nplain text\nwith several lines\nEOF",
    "python3 - <<'EOF'\nenv = 1\nprint(env_value)\nEOF",
    "echo ok \\\nprintenv",  # a backslash-newline joins the lines: printenv is just an argument here
]


@pytest.mark.parametrize("command", ALLOWED)
def test_ordinary_commands_are_still_allowed(command: str, python: str, home: Path) -> None:
    assert decide(command, python, home)[0] == "allow", command


def test_it_is_a_text_matcher_and_never_reads_the_filesystem(python: str, home: Path) -> None:
    kind, _ = decide("cat ~/.config/vendortrust/api.env", python, home)
    assert kind == "deny"
    # the hook never touched the credential directory (the macOS python3 shim may create HOME itself)
    assert not (home / ".config").exists()


# --- malformed input fails closed ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "   ",
        "not json",
        "{",
        "[]",
        "null",
        "42",
        '"Bash"',
        "{}",
        '{"tool_name": "Bash"}',
        '{"tool_name": "Bash", "tool_input": null}',
        '{"tool_name": "Bash", "tool_input": []}',
        '{"tool_name": "Bash", "tool_input": {}}',
        '{"tool_name": "Bash", "tool_input": {"command": 5}}',
        '{"tool_name": "Bash", "tool_input": {"command": ["cat", ".env"]}}',
        '{"tool_name": "Bash", "tool_input": {"command": null}}',
    ],
)
def test_malformed_hook_input_fails_closed(stdin: str, python: str, home: Path) -> None:
    done = run_hook(stdin, python, home)
    assert done.returncode != 0, (
        "a hook that cannot read its input must fail, so onFailure=block denies"
    )
    assert "allow" not in done.stdout.lower()


def test_other_tools_are_not_this_hooks_business(python: str, home: Path) -> None:
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "README.md"}})
    done = run_hook(payload, python, home)
    assert (done.returncode, done.stdout) == (0, "")


@pytest.mark.parametrize(
    "command",
    [
        'echo "unterminated',
        "echo 'also unterminated",
        "echo \\",
        "echo \x00 nul",
        "echo \x1b[31mred\x1b[0m",
        "echo ünïcödé ✓ ‮",
        "echo " + "a" * 200_000,
        "echo " + "'" * 999,
        "(((( echo",
        "echo ))))",
        "| | ; ; &&",
        "",
        " ",
    ],
)
def test_awkward_commands_get_a_clean_verdict_not_a_crash(
    command: str, python: str, home: Path
) -> None:
    kind, reason = decide(command, python, home)
    assert kind in {"allow", "deny"}, (kind, reason)


def test_a_missing_or_odd_working_directory_still_gives_a_verdict(python: str, home: Path) -> None:
    for cwd_value in (None, "", "/nonexistent/place", "relative/dir"):
        payload = {"tool_name": "Bash", "tool_input": {"command": "cat .env"}}
        if cwd_value is not None:
            payload["cwd"] = cwd_value
        kind, _ = verdict(run_hook(json.dumps(payload), python, home))
        assert kind in {"allow", "deny"}


# --- bypasses found in the pre-commit review, and pathological input -------------------------------

CFG = "~/." + "config"  # built from parts so this file never spells the protected path whole


@pytest.mark.parametrize(
    "command",
    [
        f"cd {CFG} && cat vendor*/api.env",
        "cd ~ && cat ." + "config/vendor*/*",
        f"cat {CFG}/vend$(echo ortrust)/api.env",
        f"X=ven; cat {CFG}/${{X}}dortrust/api.env",
        "bash -c printenv",
        "sh -c 'env'",
        'zsh -c "export -p"',
        "bash -lc 'bash -c printenv'",
        "env A=1 printenv",
        "timeout 5 printenv",
        "stdbuf -o0 printenv",
        "time -p env",
        "nice -n 5 printenv",
        "sudo -u nobody printenv",
        "xargs -0 printenv",
        "timeout 5 bash -c env",
        "python3 -c 'import os; print(dict(os.environ))'",
    ],
)
def test_bypasses_found_in_review_are_blocked(command: str, python: str, home: Path) -> None:
    assert decide(command, python, home)[0] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "cd apps/api && ls",
        "bash -c 'ls -la'",
        "env FOO=1 uv run pytest -q",
        "timeout 5 git status",
        "cd ~ && ls",
    ],
)
def test_unwrapping_does_not_block_ordinary_commands(command: str, python: str, home: Path) -> None:
    assert decide(command, python, home)[0] == "allow"


def test_bracket_heavy_input_is_fast_and_still_judged(python: str, home: Path) -> None:
    import time

    started = time.monotonic()
    long_token = "ls " + "[a-" * 2500  # under the length cap: must be quick, not quadratic
    assert decide(long_token, python, home)[0] == "allow"
    assert time.monotonic() - started < 5
    started = time.monotonic()
    assert (
        decide("ls " + "[a-" * 30000, python, home)[0] == "deny"
    )  # over the cap: refused outright
    assert time.monotonic() - started < 5


def test_the_protected_paths_and_the_hook_registration_are_intact() -> None:
    hook = load_hook()
    protected = {p.lower() for p in hook.PROTECTED}
    expected = {
        str(REPO_ROOT / ".env"),
        str(REPO_ROOT / "apps" / "api" / ".env"),
        str(REPO_ROOT / "apps" / "api" / ".env.migrations"),
        str(REPO_ROOT / "apps" / "web" / ".env.local"),
    }
    assert {p.lower() for p in expected} <= protected
    assert any(p.endswith(os.path.join(".config", "vendortrust")) for p in protected)
