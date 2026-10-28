# VendorTrust: instructions for Claude

## Credentials: never access the real credential directory

- Database credentials live **outside the repository**, in `~/.config/vendortrust/`
  (`api.env`, `migrations.env`). Never read, list, stat, copy, grep or print anything in that
  directory, and never put its path in a shell command. The user creates and edits those files
  themselves.
- Never read real env files: `.env`, `apps/api/.env`, `apps/api/.env.migrations`,
  `apps/web/.env.local`. Only the `*.example` templates may be read.
- Never ask the user to paste passwords, keys or connection strings, and never print
  environment variables (`env`, `printenv`).
- Tests and demonstrations use obviously fake values only.

**This is a reminder, not a security control.** It is not enforced and must not be relied on
for protection. Enforcement lives in Claude Code settings (permission deny rules and
`blockReadsOutsideWorkingDirectories`), which have known gaps (for example, shell commands).
Follow these rules regardless.

## Claude Code setup: nothing here is active until you register it locally

A clone gets the hook script (`.claude/hooks/guard-credentials.py`) but **no settings**.
`.claude/settings.local.json` is git-ignored because it holds machine-specific absolute paths. Until you
create it yourself, the hook, the deny rules and the sandbox are all **off**. Register them in your own
`.claude/settings.local.json`:

- A `PreToolUse` hook with matcher `Bash` running `python3 <absolute path to the hook>`, with
  `"timeout": 10` and `"onFailure": "block"` (without `onFailure: block` a crashing hook does not deny).
- `permissions.deny` rules for the four real env files, and `blockReadsOutsideWorkingDirectories`.
- The OS sandbox with `denyRead` on `~/.config/vendortrust/`.

The hook is **a text-matching tripwire, not a sandbox, and not a substitute for the sandbox and deny rules**.
It cannot see paths or commands built at run time (variable or `base64` tricks, scripts written earlier),
and it refuses commands over 8 KB. The tests in `apps/api/tests/test_credential_guard.py` prove the script's
behaviour only, not that your settings register it.
