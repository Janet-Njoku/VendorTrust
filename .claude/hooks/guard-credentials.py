#!/usr/bin/env python3
"""Claude Code PreToolUse hook for the Bash tool: a best-effort VendorTrust credential tripwire.

It reads the pending shell command as JSON on stdin and DENIES it when the command looks like it
would read real credentials: files under ~/.config/vendortrust/, real env files (.env,
.env.migrations, .env.local), or the environment. It never touches the filesystem: every check is
text matching, so the real credential directory is never even stat'ed.

THIS IS A TRIPWIRE, NOT A SANDBOX. It stops accidents and simple or semi-obfuscated attempts.
It cannot stop code that builds a path at run time (string concatenation, base64, a script file
written earlier). Only an OS-level sandbox can. It does nothing until it is registered as a PreToolUse
hook in your own .claude/settings.local.json (see CLAUDE.md).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from fnmatch import fnmatchcase

HOME = os.path.expanduser("~")
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CRED_DIR = os.path.join(HOME, ".config", "vendortrust")

PROTECTED = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "apps", "api", ".env"),
    os.path.join(ROOT, "apps", "api", ".env.migrations"),
    os.path.join(ROOT, "apps", "web", ".env.local"),
    CRED_DIR,
    os.path.join(CRED_DIR, "api.env"),
    os.path.join(CRED_DIR, "migrations.env"),
]
ENV_FILE_NAME = re.compile(r"(?<![A-Za-z0-9_])\.env(?:\.[A-Za-z0-9_-]+)*(?![A-Za-z0-9_-])")
TEMPLATE_SUFFIXES = {"example", "sample", "template"}
SECRET_VARIABLE = re.compile(
    r"\$\{?(VT_DATABASE_URL|VT_MIGRATION_DATABASE_URL|VT_SUPABASE_SERVICE_ROLE_KEY|PGPASSWORD)\b"
)
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&"}
WRAPPERS = {"sudo", "command", "exec", "time", "nohup", "builtin", "nice"}
QUOTED = re.compile(r"""['"]([^'"\n]{1,300})['"]""")
# Wrappers that run another command: the inner command is judged too, whatever flags sit in between.
WRAPPER_WORDS = WRAPPERS | {"env", "timeout", "stdbuf", "xargs"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
INTERPRETERS = re.compile(r"python[0-9.]*|node|perl|ruby")
ENV_ACCESS = re.compile(r"os\.environ|getenv|process\.env|\bENV\b|\benviron\b")
DYNAMIC = re.compile(r"\$\(|`|\$\{|\$[A-Za-z_]")  # an expansion that can build a path at run time
HOME_VARIABLE = re.compile(r"\$(\{HOME\}|HOME\b)")
MAX_COMMAND = 8192  # longer than any real command; also bounds the cost of glob matching
MAX_GLOB_PATH = 1024  # PATH_MAX is 1024 on macOS; no real path glob is longer
MAX_DEPTH = 3


def tokenize(command: str) -> list[str]:
    # A newline ends a command just like `;` does, so `echo hi` then `printenv` on the next line is two
    # commands (a backslash-newline is a continuation). Lines of a heredoc body are treated as commands
    # too: that can only over-block, never let something through.
    command = (
        command.replace("\\\r\n", " ").replace("\\\n", " ").replace("\r\n", "\n").replace("\n", " ; ")
    )
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        return command.split()


def split_commands(tokens: list[str]) -> list[list[str]]:
    """Raw simple commands (every token kept, including leading VAR=value assignments)."""
    commands: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in SEPARATORS:
            if current:
                commands.append(current)
                current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def strip_prefix(cmd: list[str]) -> list[str]:
    """The command proper: leading assignments and wrappers removed (used to classify it)."""
    i = 0
    while i < len(cmd) and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", cmd[i]) or cmd[i] in WRAPPERS):
        i += 1
    return cmd[i:] or cmd


def candidates(token: str) -> list[str]:
    found = [token]
    if "=" in token:
        found.append(token.split("=", 1)[1])
    found.extend(QUOTED.findall(token))
    return found


def normalize(expr: str, cwd: str) -> str:
    expanded = os.path.expanduser(expr.replace("${HOME}", HOME).replace("$HOME", HOME))
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return os.path.normpath(expanded)


def _match(pattern: list[str], parts: list[str]) -> bool:
    if not pattern:
        return not parts
    head = pattern[0]
    if head == "**":
        return any(_match(pattern[1:], parts[i:]) for i in range(len(parts) + 1))
    if not parts:
        return False
    first = parts[0]
    if first.startswith(".") and not head.startswith("."):  # shell: * skips dotfiles
        return False
    return _fnmatch(first, head) and _match(pattern[1:], parts[1:])


def _fnmatch(name: str, pattern: str) -> bool:
    """fnmatchcase, but a malformed bracket expression (for example `[l-e`) is plain text.

    The candidate string comes from an arbitrary shell command, and `fnmatch` raises `re.error` on a
    pattern like that. A shell does not expand a malformed bracket either, it keeps it literal, so
    matching it literally is the faithful reading. Any OTHER failure still propagates: the hook then
    exits non-zero and, with onFailure=block, denies the command (fail closed).
    """
    try:
        return fnmatchcase(name, pattern)
    except re.error:
        return fnmatchcase(name, pattern.replace("[", "[[]"))


def glob_match(pattern: str, path: str) -> bool:
    return _match(pattern.lower().split(os.sep), path.lower().split(os.sep))


def in_credential_dir(path: str) -> bool:
    lowered, cred = path.lower(), CRED_DIR.lower()
    return lowered == cred or lowered.startswith(cred + os.sep)


def hits_protected(expr: str, cwd: str) -> bool:
    path = normalize(expr, cwd)
    if in_credential_dir(path):
        return True
    if any(ch in path for ch in "*?[") and len(path) <= MAX_GLOB_PATH:
        return any(glob_match(path, p) for p in PROTECTED)
    return path.lower() in {p.lower() for p in PROTECTED}


def inner_commands(raw: list[str]) -> list[list[str]]:
    """The command proper plus, behind a wrapper (`timeout 5 env`, `time -p env`), every suffix."""
    cmd = strip_prefix(raw)
    first = next((w for w in raw if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", w)), raw[0])
    if os.path.basename(first) in WRAPPER_WORDS:
        return [cmd] + [raw[i:] for i in range(len(raw))]
    return [cmd]


def is_ancestor(expr: str, cwd: str) -> bool:
    path = normalize(expr, cwd).lower().rstrip(os.sep)
    return any(p.lower().startswith(path + os.sep) for p in PROTECTED)


def dumps_environment(cmd: list[str]) -> bool:
    word, args = os.path.basename(cmd[0]), cmd[1:]
    if word == "printenv":
        return True
    if word == "env":  # `env VAR=x cmd` and `env -u X cmd` run a command; bare `env` prints
        return not [a for a in args if not a.startswith("-") and "=" not in a]
    if word in {"export", "declare", "typeset"}:
        return not args or any(a in {"-p", "-x", "-px", "-xp"} for a in args)
    if word == "set":
        return not args
    return word == "compgen" and ("-e" in args or "-v" in args)


def is_recursive_read(cmd: list[str]) -> bool:
    word, args = os.path.basename(cmd[0]), cmd[1:]
    flags = [a for a in args if a.startswith("-")]
    if word in {"grep", "egrep", "fgrep"}:
        return any(re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", f) or f.endswith("recursive") for f in flags)
    if word in {"ag", "ack"}:
        return True
    if word == "rg":  # ripgrep skips ignored and hidden files unless told otherwise
        return any(
            re.fullmatch(r"-u+", f) or f in {"--hidden", "--no-ignore", "--no-ignore-vcs", "--unrestricted"}
            for f in flags
        )
    if word == "find":
        return any(a in {"-exec", "-execdir", "-ok", "-okdir", "-fprint"} for a in args)
    return word in {"cp", "rsync", "tar", "zip", "ditto", "scp"}


def blocked_reason(command: str, cwd: str, depth: int = 0) -> str | None:
    if len(command) > MAX_COMMAND:
        return "the command is too long to inspect safely"
    tokens = tokenize(command)
    joined = " ".join(tokens)
    commands = split_commands(tokens)

    if SECRET_VARIABLE.search(command) or SECRET_VARIABLE.search(joined):
        return "refusing to expand a database credential variable"
    if ".config" in joined.lower() and "vendortrust" in joined.lower():
        return "the command refers to the credential directory under ~/.config"
    if ".config" in command.lower() and DYNAMIC.search(HOME_VARIABLE.sub("", command)):
        return "the command builds a path under ~/.config at run time"

    cwds = [cwd]  # every directory a `cd` may have moved to (a conditional `cd` is assumed taken)
    for raw in commands:
        cmd = strip_prefix(raw)
        if cmd[0] in {"cd", "pushd"}:
            target = cmd[1] if len(cmd) > 1 and not cmd[1].startswith("-") else "~"
            cwds.append(normalize(target, cwds[-1]))
        for inner in inner_commands(raw):
            if dumps_environment(inner):
                return "refusing to print the environment"
            word, args = os.path.basename(inner[0]), inner[1:]
            if INTERPRETERS.fullmatch(word) and any(ENV_ACCESS.search(a) for a in args):
                return "refusing to read the environment from an interpreter"
            if word in SHELLS and depth < MAX_DEPTH:
                for i, arg in enumerate(args[:-1]):
                    if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", arg):
                        reason = blocked_reason(args[i + 1], cwds[-1], depth + 1)
                        if reason:
                            return reason
        # `git check-ignore` only inspects ignore rules; exempt it for in-repo env files, per command.
        ignore_check = cmd[:2] == ["git", "check-ignore"]
        if not ignore_check:
            for match in ENV_FILE_NAME.finditer(" ".join(raw)):
                if match.group(0).rsplit(".", 1)[-1].lower() not in TEMPLATE_SUFFIXES:
                    return f"the command names a real env file ({match.group(0)}); only *.example templates are allowed"
        for token in raw:
            for candidate in candidates(token):
                for where in cwds:
                    if hits_protected(candidate, where) and not (
                        ignore_check and not in_credential_dir(normalize(candidate, where))
                    ):
                        return "the command reaches a protected credential path"
        if is_recursive_read(cmd):
            paths = [a for a in cmd[1:] if not a.startswith("-")]
            if len(paths) <= 1:
                paths.append(".")  # recursive tools with no path search the working directory
            if any(is_ancestor(p, where) for p in paths for where in cwds):
                return "a recursive read or copy would cover protected credential paths"
    return None


def main() -> int:
    data = json.load(sys.stdin)  # any failure here exits non-zero: with onFailure=block that denies
    if not isinstance(data, dict) or "tool_name" not in data:
        raise ValueError("malformed hook input")  # fail closed
    if data["tool_name"] != "Bash":
        return 0
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        raise ValueError("malformed hook input: no command")  # fail closed
    reason = blocked_reason(command, data.get("cwd") or os.getcwd())
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"VendorTrust credential guard: {reason}",
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
