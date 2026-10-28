"""FastAPI dependencies: authenticate, then open the request's RLS-scoped transaction.

Order matters and is fixed here: verify the JWT (identity only), then let the DATABASE resolve
the organization, role and vendor scope from the user's own membership (`vt.begin_request`).
Nothing the client sends, other than which of its own organizations to act in, influences them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine

from app.auth import AuthError, AuthUnavailable, Identity, JwtVerifier, verifier_from_settings
from app.config import Settings
from app.db.context import AccessDenied, OrganizationRequired
from app.db.session import RequestContext, get_engine, open_context

bearer = HTTPBearer(auto_error=False)
WRITE_ROLES = frozenset({"org_admin", "analyst"})
STAFF_ROLES = frozenset({"org_admin", "analyst", "reviewer", "auditor"})


def _unauthorized() -> HTTPException:
    return HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})


def get_verifier(request: Request) -> JwtVerifier:
    settings: Settings = request.app.state.settings
    try:
        return verifier_from_settings(settings)
    except AuthUnavailable:
        raise HTTPException(503, "Authentication is not available") from None


def authenticated(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    verifier: Annotated[JwtVerifier, Depends(get_verifier)],
) -> Identity:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    try:
        return verifier.verify(credentials.credentials)
    except AuthError:
        raise _unauthorized() from None
    except AuthUnavailable:
        raise HTTPException(503, "Authentication is not available") from None


MFA_REQUIRED = "Multi-factor authentication required"


def enforce_mfa(identity: Identity, context: RequestContext, settings: Settings) -> None:
    """Internal roles need an aal2 token. The role comes from the database, the level from the
    signed token; vendor users are exempt. Raised as 403 (not 401) so clients can tell "sign in
    again" from "complete MFA"."""
    if settings.enforce_mfa_internal and context.role in STAFF_ROLES and identity.aal != "aal2":
        raise HTTPException(
            403,
            MFA_REQUIRED,
            headers={
                "WWW-Authenticate": 'Bearer error="insufficient_user_authentication", '
                'error_description="aal2 required"'
            },
        )


def parse_org_header(x_org_id: str | None) -> uuid.UUID | None:
    if x_org_id is None:
        return None
    try:
        return uuid.UUID(x_org_id)
    except ValueError:
        raise HTTPException(400, "Invalid X-Org-Id header") from None


def request_context(
    identity: Annotated[Identity, Depends(authenticated)],
    engine: Annotated[Engine, Depends(get_engine)],
    request: Request,
    x_org_id: Annotated[str | None, Header()] = None,
) -> Iterator[RequestContext]:
    """One transaction per request; rolled back unless the endpoint commits it."""
    org_id = parse_org_header(x_org_id)
    try:
        context = open_context(engine, identity.user_id, org_id)
    except AccessDenied:
        raise HTTPException(403, "No active membership") from None
    except OrganizationRequired:
        raise HTTPException(400, "Choose an organization with the X-Org-Id header") from None
    try:
        enforce_mfa(identity, context, request.app.state.settings)
        yield context
    finally:
        context.close()


def require_staff(context: RequestContext) -> None:
    """Internal roles only. A vendor_user has no access to questionnaires or assessments."""
    if context.role not in STAFF_ROLES:
        raise HTTPException(403, "Insufficient permissions")


def require_writer(context: RequestContext) -> None:
    """org_admin and analyst may change vendors. (RLS enforces the same rule underneath.)"""
    if context.role not in WRITE_ROLES:
        raise HTTPException(403, "Insufficient permissions")
