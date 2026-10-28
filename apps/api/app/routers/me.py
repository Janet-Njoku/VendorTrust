"""The signed-in user: first-login provisioning and "what can I do here".

`GET /me` is the first call the web app makes after sign-in. For a user the API has verified (a
valid Supabase token) it:

1. **Provisions** an `app_users` row for that user if there is none. This is the ONLY thing that is
   created: provisioning never grants organization access. A fresh registration has no membership
   until an organization administrator (or the one-time bootstrap) gives it one, so every data
   endpoint still answers 403 "No active membership" for them.
2. Reports the user's active memberships, whether they must choose an organization, and the MFA
   state, so the UI can show the right screen (no access yet, choose an organization, complete MFA).

Provisioning uses the same transaction-local claim as every other request, set only from the
verified token; the database policy allows an insert of exactly the caller's own, active row and
nothing else (`app_users_insert_self`). `/me` is deliberately NOT subject to the MFA requirement:
a user who has not completed MFA must still be able to learn that they have to.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Engine, text

from app.auth import Identity
from app.db.context import AccessDenied, OrganizationRequired
from app.db.session import get_engine, open_context
from app.deps import STAFF_ROLES, authenticated, parse_org_header

router = APIRouter(tags=["me"])

Role = Literal["org_admin", "analyst", "reviewer", "auditor", "vendor_user"]


class MembershipOut(BaseModel):
    org_id: uuid.UUID
    role: Role
    vendor_id: uuid.UUID | None


class MfaOut(BaseModel):
    #: True when the user's role needs aal2 (an internal role) and enforcement is on.
    required: bool
    satisfied: bool
    level: Literal["aal1", "aal2"]


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    status: Literal["active", "disabled"]
    #: The user's own active memberships (organization ids and roles).
    memberships: list[MembershipOut]
    #: The membership the API resolved for this request, if any.
    active: MembershipOut | None
    has_access: bool
    #: Several memberships and none chosen: send X-Org-Id to pick one.
    needs_organization: bool
    mfa: MfaOut


def provision(engine: Engine, identity: Identity, email: str) -> tuple[str, list[MembershipOut]]:
    """Create the user's own app_users row if missing; return its status and active memberships."""
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('vt.claim_user_id', :user, true)"),
            {"user": str(identity.user_id)},
        )
        connection.execute(
            text(
                "INSERT INTO vt.app_users (id, email) VALUES (:id, :email) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": identity.user_id, "email": email},
        )
        status = connection.execute(
            text("SELECT status FROM vt.app_users WHERE id = :id"), {"id": identity.user_id}
        ).scalar_one()
        rows = (
            connection.execute(
                text(
                    "SELECT org_id, role, vendor_id FROM vt.memberships "
                    "WHERE user_id = :id AND status = 'active' ORDER BY created_at, id"
                ),
                {"id": identity.user_id},
            )
            .mappings()
            .all()
        )
    return str(status), [MembershipOut.model_validate(dict(r)) for r in rows]


@router.get("/me", response_model=MeOut)
def read_me(
    identity: Annotated[Identity, Depends(authenticated)],
    engine: Annotated[Engine, Depends(get_engine)],
    request: Request,
    x_org_id: Annotated[str | None, Header()] = None,
) -> MeOut:
    if identity.email is None:
        raise HTTPException(403, "Your account has no email address")
    org_id = parse_org_header(x_org_id)
    status, memberships = provision(engine, identity, identity.email)

    active: MembershipOut | None = None
    needs_organization = False
    if status == "active":
        try:
            context = open_context(engine, identity.user_id, org_id)
        except AccessDenied:
            pass
        except OrganizationRequired:
            needs_organization = True
        else:
            try:
                active = MembershipOut(
                    org_id=context.org_id,
                    role=context.role,
                    vendor_id=context.vendor_id,
                )
            finally:
                context.close()

    roles = [active.role] if active else [m.role for m in memberships]
    required = request.app.state.settings.enforce_mfa_internal and any(
        role in STAFF_ROLES for role in roles
    )
    return MeOut(
        user_id=identity.user_id,
        email=identity.email,
        status="active" if status == "active" else "disabled",
        memberships=memberships,
        active=active,
        has_access=active is not None,
        needs_organization=needs_organization,
        mfa=MfaOut(
            required=required,
            satisfied=not required or identity.aal == "aal2",
            level="aal2" if identity.aal == "aal2" else "aal1",
        ),
    )
