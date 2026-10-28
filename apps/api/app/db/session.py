"""The application's database engine and the per-request, RLS-scoped transaction.

Credentials are loaded on demand (first use), never at import. Each request gets one
transaction in which `vt.begin_request` sets the transaction-local context; the transaction
ends with the request, so the context cannot reach the next request on a pooled connection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy import Connection, Engine, RootTransaction, create_engine, event, text
from sqlalchemy.engine import make_url

from app.credentials import app_database_url
from app.db.context import begin_request

APP_ROLE = "vt_app"


class UnsafeDatabaseRole(RuntimeError):
    """The application connected as something other than the plain restricted role."""


def _check_role(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(
            "SELECT current_user::text, r.rolsuper, r.rolbypassrls "
            "FROM pg_roles r WHERE r.rolname = current_user"
        )
        user, superuser, bypass = cursor.fetchone()
    finally:
        cursor.close()
        dbapi_connection.rollback()
    if user != APP_ROLE or superuser or bypass:
        raise UnsafeDatabaseRole(
            f"the API must connect as the restricted role {APP_ROLE} "
            "(not a superuser, no BYPASSRLS)"
        )


def build_engine(url: str) -> Engine:
    engine = create_engine(
        make_url(url).set(drivername="postgresql+psycopg"),
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    event.listen(engine, "connect", _check_role)
    return engine


@lru_cache
def get_engine() -> Engine:
    return build_engine(app_database_url().get_secret_value())


@dataclass
class RequestContext:
    """One request's transaction. `role`, `org_id` and `vendor_id` come from the database."""

    connection: Connection
    transaction: RootTransaction
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    vendor_id: uuid.UUID | None

    def commit(self) -> None:
        self.transaction.commit()

    def close(self) -> None:
        if self.transaction.is_active:
            self.transaction.rollback()
        self.connection.close()


def open_context(engine: Engine, user_id: uuid.UUID, org_id: uuid.UUID | None) -> RequestContext:
    """Open a connection and transaction and resolve the context (raises app.db.context errors)."""
    connection = engine.connect()
    transaction = connection.begin()
    try:
        begin_request(connection, user_id, org_id)
        role, org, vendor = connection.execute(
            text(
                "SELECT current_setting('vt.claim_role'), "
                "CAST(current_setting('vt.claim_org_id') AS uuid), "
                "CAST(nullif(current_setting('vt.claim_vendor_id'), '') AS uuid)"
            )
        ).one()
    except BaseException:
        transaction.rollback()
        connection.close()
        raise
    return RequestContext(connection, transaction, user_id, org, role, vendor)
