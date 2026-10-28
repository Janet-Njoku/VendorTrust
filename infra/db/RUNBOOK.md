# Runbook: set up the VendorTrust database roles on Supabase

A step-by-step guide for running the reviewed bootstrap against your **development** Supabase
project, written for someone who has not used `psql` before.

> **Run this only against a Supabase project you own and have reviewed, and only after you have read the
> SQL files named below.** Steps 1 to 4 only read; nothing is changed until step 6. Never run it against a
> production project.

## Ground rules (read once)

- **Passwords are only ever typed at a hidden `Password:` prompt.** Never in a command, never
  in a file you commit, never in chat. Nothing in this runbook asks you to paste one anywhere
  else.
- Values like the project reference and host are **not secret**; it is fine to type them.
- If anything fails, copy the **error text** to whoever is helping you. Error text from these
  scripts contains no passwords. Never paste your `.env` files.
- Every command is run from the **repository root** (the folder that contains `README.md`).

```bash
cd ~/Projects/VendorTrust
```

(`-X` ignores any personal psql settings, `-q` hides routine messages, `-v ON_ERROR_STOP=1` stops at the first error.)

## What each script does

| Script | Changes anything? | Purpose |
|---|---|---|
| `infra/db/preflight.sql` | **No** (database enforces read-only) | Confirms you are connected to the project you intend, and the admin can run the bootstrap |
| `infra/db/bootstrap.sql` (no `commit` option) | No (runs, then rolls back) | Proves every statement works on the real project, leaving no trace |
| `infra/db/bootstrap.sql` with `-v commit=1` | **Yes** | Creates roles `vt_app`, `vt_migrator` and schema `vt` (one all-or-nothing transaction) |
| `infra/db/verify_bootstrap.sql` | No | Checks the end state; every row must say `t` |

If the bootstrap meets anything unexpected (for example a schema `vt` that someone else owns,
or an existing `vt_app` with extra powers) it **stops and changes nothing**.

## Step 1: Make sure `psql` works

`psql` is the command-line tool for talking to PostgreSQL. It was installed with PostgreSQL 17.

```bash
export PATH="$(brew --prefix postgresql@17)/bin:$PATH"
psql --version
```

You should see `psql (PostgreSQL) 17.x`. This `export` only lasts for the current terminal
window; repeat it in any new window.

## Step 2: Collect three non-secret values from the Supabase dashboard

1. **Check you are in the development project.** The project name is shown at the top left of
   the dashboard. If you have several projects, make sure it is the right one.
2. **Project reference:** gear icon (Project Settings), then **General**, then **Reference ID**.
   It is 20 lowercase letters/numbers. Write it down: this is `YOURREF` below.
3. **Session pooler details:** click the **Connect** button at the top of the dashboard and
   choose the **Session pooler** connection method (not "Transaction pooler", not "Direct
   connection"). Note the **host** (like `aws-0-<region>.pooler.supabase.com`). The port is
   `5432` and the user is `postgres.YOURREF`.
4. **Database password:** Project Settings, then **Database**. The dashboard cannot show an
   existing password; use **Reset database password**, generate a new one, and store it in
   your password manager. (On a brand-new development project nothing else depends on it.)

## Step 3: Tell your terminal where to connect (no secrets here)

Replace the three placeholders, then paste these lines. **Do not set `PGPASSWORD`.**

```bash
export PGHOST='aws-0-REGION.pooler.supabase.com'
export PGPORT=5432
export PGUSER='postgres.YOURREF'
export PGDATABASE=postgres
export PGSSLMODE=require
export EXPECTED_REF='YOURREF'
```

Test the connection. It will ask `Password for user postgres.YOURREF:`. Type or paste the
password; **nothing appears on screen while you type**, which is normal. Press Enter.

```bash
psql -X -c "select current_user, current_database()"
```

Expected: one row showing `postgres | postgres`.

## Step 4: Preflight (read-only): confirm this is the right project

```bash
psql -X -q -v ON_ERROR_STOP=1 -v expected_ref="$EXPECTED_REF" -f infra/db/preflight.sql
```

Enter the password again when prompted. You should end with `PREFLIGHT PASSED`. Check two
things yourself:

- The line `connection targets project "..."` shows your reference.
- The `auth.users rows: N` line matches the number of users under **Authentication, Users** in
  the dashboard. This proves the database is the same project you are looking at.

If any line says `FAIL`, **stop** and share the output.

## Step 5: Dry run (optional but recommended)

```bash
psql -X -q -v ON_ERROR_STOP=1 -f infra/db/bootstrap.sql
```

This command has **no** `commit` option, so the script is **safe by default**: it runs every
statement and then rolls everything back, ending with `DRY RUN OK: ... NOTHING WAS CHANGED.`
This is the real proof that the Supabase `postgres` role is allowed to do everything the
bootstrap needs (create roles, create the schema, set role defaults). Run `preflight.sql`
again afterwards: it should still say `vt_app and vt_migrator do not exist yet`.

Because rollback is the default, a typing mistake can never apply the changes by accident. The
only way to commit is the exact option shown in Step 6.

## Step 6: The real run (only once Steps 1 to 5 have passed and you are sure of the target)

```bash
psql -X -q -v ON_ERROR_STOP=1 -v commit=1 -f infra/db/bootstrap.sql
```

The only difference from Step 5 is `-v commit=1`. Type it exactly: lower case, and the value
must be exactly `1`.

- Success ends with `Bootstrap COMMITTED.`
- If it ends with `DRY RUN OK ... NOTHING WAS CHANGED` instead, the option was misspelled or
  missing. Nothing was applied; retype the command.
- If it stops with `invalid value for -v commit`, the value was not exactly `1`. Nothing was
  applied.
- If it prints another `ERROR`, **nothing was changed**: the whole script is one transaction
  and is rolled back automatically. Share the message.

## Step 7: Verify

```bash
psql -X -v ON_ERROR_STOP=1 -P pager=off -f infra/db/verify_bootstrap.sql
```

Every row must show `t` in the `ok` column. Any `f` means something differs from the design:
stop and share the output.

## Step 8: Give the two roles passwords

First create two passwords in your password manager: **letters and numbers only, 40
characters** (avoids special characters that break connection URLs). Then:

```bash
psql -X
```

At the `postgres=>` prompt type each command below. psql asks twice for each password (hidden
typing) and sends only a hash to the server, so the clear text never reaches your shell
history or any log:

```
\password vt_app
\password vt_migrator
\q
```

(Do not use `ALTER ROLE ... PASSWORD '...'` instead: the password would sit in your command
history.)

## Step 9: Keep the connection URLs in two private files outside the repository

Credentials never live inside the repository. Create two files in your own Terminal:

| File | Holds | Read by |
|---|---|---|
| `~/.config/vendortrust/api.env` | the **application** role's URL (user `vt_app.YOURREF`) plus the API's settings | the API, only when it needs the database |
| `~/.config/vendortrust/migrations.env` | exactly **one** variable: the **migration** role's URL (user `vt_migrator.YOURREF`) | migration tooling only (Phase 1.3) |

```bash
umask 077
mkdir -p ~/.config/vendortrust
chmod 700 ~/.config/vendortrust
cp apps/api/.env.example ~/.config/vendortrust/api.env
cp apps/api/.env.migrations.example ~/.config/vendortrust/migrations.env
chmod 600 ~/.config/vendortrust/api.env ~/.config/vendortrust/migrations.env
```

Open each file in a text editor (for example `nano ~/.config/vendortrust/api.env`) and replace
`PROJECT_REF`, `REGION` and `CHANGE_ME` with your real values. Save.

**What the loader enforces** (it refuses, and tells you how to fix it, without ever printing a
value): the folder is yours and not writable by others (`700` recommended); the file is yours
with mode `600`; neither is a symlink; the file is not inside the git repository; only the
variable names in the template are allowed (so the migration URL cannot sit in `api.env`); a
URL must belong to its own role family (`vt_app` vs `vt_migrator`); remote hosts need
`sslmode=require` or stronger. The format is plain `NAME=value`, with no `${VAR}` expansion.

Where credentials come from, in priority order: a script's `--env-file`, then the process
environment (`VT_DATABASE_URL` / `VT_MIGRATION_DATABASE_URL`), then a file named by `VT_ENV_FILE`
/ `VT_MIGRATION_ENV_FILE`, then the default files above. **Production never reads the default
folder**: set the variables or name a file explicitly. A process that has one role's URL in its
environment refuses to load the other role's.

## Step 10: Check each role from the application side

Run these yourself, in your own Terminal (the checks read your private files):

```bash
cd apps/api
uv run python -m scripts.check_db_connection --kind app
uv run python -m scripts.check_db_connection --kind migrator
VT_SUPABASE_URL="https://$EXPECTED_REF.supabase.co" uv run python -m scripts.check_jwks
cd ../..
```

Everything should say `PASS`. These scripts print the credential file's path and a summary like
`vt_app.YOURREF@host:5432/postgres`, never a password or a URL. Use `--env-file PATH` to check a
different file. A `FAIL` on the JWKS check means the project still uses the legacy shared JWT
secret: enable asymmetric signing keys in the dashboard (Authentication, JWT signing keys) and
run it again.

## Step 11: Clean up

```bash
unset PGHOST PGPORT PGUSER PGDATABASE PGSSLMODE EXPECTED_REF
```

Closing the terminal window has the same effect. Because no password was ever typed into a
command, `history | grep -i passw` should show nothing sensitive.

## Step 12: Create the first organization (once, after the migrations are applied)

> **Do not run this until the Alembic migrations (`apps/api/alembic/versions/`, 0001 to 0006; see
> [local-e2e.md](../../docs/local-e2e.md) for how they are applied locally) have been applied to the
> project and you are sure this is the right project.** It is a one-time operator step; there is no endpoint
> and no button for it, and registering never grants access.

1. In the web app, **register** the person who will be the administrator, confirm their email, and sign in
   once. (This provisions their user row; it grants nothing.)
2. In a terminal that holds **only** the migration role's URL (your `migrations.env`, loaded by the
   credential loader; never type a password on the command line):

   ```bash
   cd apps/api
   export VT_SUPABASE_URL='https://YOURREF.supabase.co'
   export VT_SUPABASE_PUBLISHABLE_KEY='<the publishable key: public, never the secret key>'
   uv run python -m scripts.bootstrap_org --org-name "Your Organization" --admin-email admin@example.com
   ```

   It asks for the administrator's password at a hidden prompt and signs in as them, which proves who
   they are. The first run is a **dry run** that is rolled back and says so.
3. Re-run with `--commit`. It prints `Bootstrap COMMITTED` and the new organization's id.
4. Sign in to the web app as that administrator. The app asks them to enroll a TOTP authenticator
   (internal roles require MFA: the API checks the `aal2` claim), then the dashboard opens.

A second run is refused (`already bootstrapped`): the database itself closes the window once an
organization exists. If the command stops with an error, **nothing was changed** (one transaction).
Other people get access only when an administrator gives them a membership.

## If something goes wrong

| What you see | Meaning and fix |
|---|---|
| `psql: command not found` | Repeat the `export PATH=...` line in Step 1 |
| `Tenant or user not found` | `PGUSER` must be exactly `postgres.YOURREF` (with the dot) and the reference must be correct |
| `password authentication failed` | Wrong password. Reset it in Project Settings, Database |
| Long hang, or `no route to host` | You used the *Direct connection* host (IPv6 only). Use the Session pooler host |
| `FAIL  connection targets project` | The reference you gave does not match the connection. Do not continue |
| `FAIL  database has Supabase markers` | You are not connected to a Supabase database. Do not continue |
| `refusing to continue` / `refusing to take it over` | Something unexpected already exists. Nothing was changed. Share the message |
| `permission denied to create role` | You are not connected as `postgres` |
| `group or other users can access this file` | Run the `chmod 600 <file>` the message shows |
| `the folder is writable by other users` | Run `chmod 700 ~/.config/vendortrust` |
| `unexpected variable name(s): ...` | The file defines a name it may not (for example the migration URL inside `api.env`). Remove it |
| `... credentials are not configured` | The file does not exist yet (Step 9), or in production no variable or `VT_ENV_FILE` is set |
| `the user is not in the ... role family` | A URL is in the wrong file: `vt_app...` belongs in `api.env`, `vt_migrator...` in `migrations.env` |
| `must not hold the ... credential` | The shell has the other role's URL exported. Run `unset VT_DATABASE_URL VT_MIGRATION_DATABASE_URL` |
| `credentials must live outside the git repository` | The file path is inside the repository. Move it to `~/.config/vendortrust/` |

## Undoing it (development projects only)

See "Teardown" in [README.md](README.md).
