"""One-time creation of the first organization, its first administrator and their membership.

This is an OPERATOR action, not an API feature: there is no endpoint, and the web app cannot reach
it. It runs from scripts/bootstrap_org.py with the migration credential (vt_migrator) that only the
operator holds, and is protected three ways:

1. **Authorization**: the caller holds the migration credential, and must prove who the new
   administrator is by signing in as them (a hidden password prompt), so a typo cannot hand the
   organization to the wrong account and the account is known to be real and confirmed.
2. **One-time, enforced by the database**: the INSERT policies of migration 0006 are open only while
   no organization exists. A second run is refused here, and even a modified script is refused by
   PostgreSQL itself.
3. **Safe by default**: it applies everything and rolls back unless committed explicitly.

Nothing here grants access to anyone else, and registering or signing in never reaches this code.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, text

from app.auth import AuthError, JwtVerifier
from app.db.migrate import MigrationError, assert_migration_role

ACTION = "bootstrap.first_organization"
LOCK_KEY = "vt.bootstrap"


class BootstrapError(Exception):
    """The bootstrap was refused or could not run. Messages never contain a secret."""


@dataclass(frozen=True)
class VerifiedAdmin:
    user_id: uuid.UUID
    email: str


@dataclass(frozen=True)
class BootstrapResult:
    org_id: uuid.UUID
    user_id: uuid.UUID
    user_existed: bool
    committed: bool


def clean_org_name(raw: str) -> str:
    name = raw.strip()
    if not 1 <= len(name) <= 200:
        raise BootstrapError("the organization name must be 1 to 200 characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise BootstrapError("the organization name must not contain control characters")
    return name


def password_sign_in(supabase_url: str, publishable_key: str, email: str, password: str) -> str:
    """Sign in over Supabase Auth's public REST API and return the access token."""
    request = urllib.request.Request(  # noqa: S310 - the URL is validated https (or localhost) config
        f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        method="POST",
        headers={"apikey": publishable_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            body: Any = json.loads(response.read())
    except urllib.error.HTTPError:
        raise BootstrapError(
            "could not sign in as the administrator (wrong email or password, or the email is "
            "not confirmed)"
        ) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise BootstrapError("could not reach the authentication service") from None
    token = body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(token, str):
        raise BootstrapError("the authentication service returned an unexpected response")
    return token


def verify_admin_identity(
    supabase_url: str | None,
    publishable_key: str | None,
    verifier: JwtVerifier,
    email: str,
    password: str,
) -> VerifiedAdmin:
    """Prove the administrator controls the account: sign in, then verify the token."""
    if not supabase_url or not publishable_key:
        raise BootstrapError(
            "set VT_SUPABASE_URL and VT_SUPABASE_PUBLISHABLE_KEY (the public key only) so the "
            "administrator's identity can be verified"
        )
    wanted = email.strip().lower()
    token = password_sign_in(supabase_url, publishable_key, wanted, password)
    try:
        identity = verifier.verify(token)
    except AuthError:
        raise BootstrapError("the administrator's token did not verify") from None
    if identity.email != wanted:
        raise BootstrapError("the verified account's email does not match the one given")
    return VerifiedAdmin(user_id=identity.user_id, email=wanted)


def bootstrap_first_organization(
    engine: Engine, admin: VerifiedAdmin, org_name: str, *, commit: bool
) -> BootstrapResult:
    """Create the first organization, the administrator's user row (if first login has not already)
    and an active org_admin membership, in one transaction. Rolls back unless `commit`."""
    name = clean_org_name(org_name)
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            try:
                assert_migration_role(connection)
            except MigrationError as exc:
                raise BootstrapError(str(exc)) from None
            # serialize concurrent runs: the second waits, then finds the organization and refuses
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": LOCK_KEY}
            )
            if not connection.execute(text("SELECT vt.bootstrap_is_open()")).scalar_one():
                raise BootstrapError(
                    "already bootstrapped: an organization exists, so this one-time step is closed"
                )

            existing = connection.execute(
                text("SELECT status FROM vt.app_users WHERE id = :id"), {"id": admin.user_id}
            ).scalar_one_or_none()
            if existing is not None and existing != "active":
                raise BootstrapError("that user account is disabled")
            if existing is None:  # the user must exist before the organization (the policy order)
                connection.execute(
                    text("INSERT INTO vt.app_users (id, email) VALUES (:id, :email)"),
                    {"id": admin.user_id, "email": admin.email},
                )
            org_id = connection.execute(
                text("INSERT INTO vt.organizations (name) VALUES (:name) RETURNING id"),
                {"name": name},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO vt.memberships (org_id, user_id, role, status) "
                    "VALUES (:org, :user, 'org_admin', 'active')"
                ),
                {"org": org_id, "user": admin.user_id},
            )
            _audit(connection, org_id, admin.user_id, name, existing is not None)
        except BaseException:
            outer.rollback()
            raise
        if commit:
            outer.commit()
        else:
            outer.rollback()
    return BootstrapResult(
        org_id=org_id, user_id=admin.user_id, user_existed=existing is not None, committed=commit
    )


def _audit(
    connection: Any, org_id: uuid.UUID, user_id: uuid.UUID, name: str, existed: bool
) -> None:
    connection.execute(
        text(
            "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type, "
            "entity_id, after_data) VALUES (:org, :user, 'org_admin', :action, 'organization', "
            ":org, CAST(:after AS jsonb))"
        ),
        {
            "org": org_id,
            "user": user_id,
            "action": ACTION,
            "after": json.dumps({"organization": name, "user_already_provisioned": existed}),
        },
    )
