"""Python side of the request context: one call that resolves a verified user to a membership.

`begin_request` only passes a user id the API has ALREADY authenticated (verified JWT) and,
optionally, an organization the caller wants to act in. The role and vendor come back from the
database's own membership row; this module never accepts them from a caller. The settings are
transaction-local, so they vanish at commit or rollback.

The database cannot verify the id it is given: see docs/trust-boundary.md for the limits.
"""

from __future__ import annotations

import uuid

import psycopg.errors
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

ACCESS_DENIED = "VT001"
ORGANIZATION_REQUIRED = "VT002"


class AccessDenied(Exception):
    """No live membership for this user (or organization). Deliberately says nothing more."""


class OrganizationRequired(Exception):
    """The user has several organizations and must choose one."""


def begin_request(
    connection: Connection, user_id: uuid.UUID, org_id: uuid.UUID | None = None
) -> None:
    """Set the transaction-local context. The connection must be inside a transaction."""
    if not connection.in_transaction():
        raise RuntimeError("begin_request must run inside a transaction (context is local to it)")
    try:
        connection.execute(
            text("SELECT vt.begin_request(CAST(:user_id AS uuid), CAST(:org_id AS uuid))"),
            {"user_id": str(user_id), "org_id": str(org_id) if org_id else None},
        )
    except DBAPIError as exc:
        original = exc.orig
        state = original.sqlstate if isinstance(original, psycopg.Error) else None
        if state == ACCESS_DENIED:
            raise AccessDenied from None
        if state == ORGANIZATION_REQUIRED:
            raise OrganizationRequired from None
        raise
