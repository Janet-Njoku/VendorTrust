"""Create the first organization and its first administrator. A one-time OPERATOR step.

    cd apps/api
    export VT_SUPABASE_URL=https://<project-ref>.supabase.co
    export VT_SUPABASE_PUBLISHABLE_KEY=<the publishable key, public>
    uv run python -m scripts.bootstrap_org --org-name "Acme Ltd" --admin-email you@example.com
    # prompts for the administrator's password (hidden), then does a DRY RUN that is rolled back.
    uv run python -m scripts.bootstrap_org --org-name "Acme Ltd" --admin-email you@example.com \
        --commit

The administrator must already have registered (and confirmed their email) in the app. They are
proven by signing in as them, so the organization cannot go to a mistyped account. The database
connection is the MIGRATION role, read through app.credentials like Alembic does (never a password
on the command line); the database allows this exactly once (migration 0006). Nothing is exposed
over HTTP.

Exit status: 0 done (or dry run passed), 1 refused, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

from app.auth import verifier_from_settings
from app.bootstrap import BootstrapError, bootstrap_first_organization, verify_admin_identity
from app.config import Settings
from app.credentials import CredentialError, load_database_credential
from app.dburl import parse_target, redact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--org-name", required=True, help="name of the first organization")
    parser.add_argument(
        "--admin-email", required=True, help="email of the registered administrator"
    )
    parser.add_argument("--commit", action="store_true", help="apply (the default is a dry run)")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the administrator's password from one line of stdin (for automation and tests)",
    )
    parser.add_argument("--env-file", type=Path, help="credential file for the migration role")
    args = parser.parse_args(argv)

    password = (
        sys.stdin.readline().rstrip("\n")
        if args.password_stdin
        else getpass.getpass(f"Password for {args.admin_email}: ")
    )
    if not password:
        print("refused: no password given", file=sys.stderr)
        return 2

    url = ""
    try:
        settings = Settings()
        admin = verify_admin_identity(
            settings.supabase_url,
            os.environ.get("VT_SUPABASE_PUBLISHABLE_KEY"),
            verifier_from_settings(settings),
            args.admin_email,
            password,
        )
        credential = load_database_credential("migrator", env_file=args.env_file)
        url = credential.url.get_secret_value()
        engine = create_engine(
            make_url(url).set(drivername="postgresql+psycopg"), poolclass=pool.NullPool
        )
        print(f"Database: {credential.source} -> {_describe(url)}")
        print(f"Administrator verified: {admin.email}")
        try:
            result = bootstrap_first_organization(engine, admin, args.org_name, commit=args.commit)
        finally:
            engine.dispose()
    except CredentialError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    except BootstrapError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"failed: {redact(f'{type(error).__name__}: {error}', url)}", file=sys.stderr)
        return 2

    note = "(their user row already existed from first login)" if result.user_existed else ""
    if result.committed:
        print(
            f"Bootstrap COMMITTED: organization {result.org_id}, "
            f"administrator {result.user_id} {note}"
        )
    else:
        print(
            "DRY RUN OK: the organization, user and org_admin membership were created and "
            "ROLLED BACK. NOTHING WAS CHANGED. Re-run with --commit to apply."
        )
    return 0


def _describe(url: str) -> str:
    target = parse_target(url)
    host = target.get("host", "local-socket")
    return f"{target.get('user', '?')}@{host}/{target.get('dbname', '?')}"


if __name__ == "__main__":
    raise SystemExit(main())
