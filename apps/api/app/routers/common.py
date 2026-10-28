"""Small helpers shared by the routers."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException
from sqlalchemy import Connection, RowMapping, text

from app.db.session import RequestContext
from app.deps import request_context

Context = Annotated[RequestContext, Depends(request_context)]


def not_found(what: str) -> HTTPException:
    return HTTPException(404, f"{what} not found")


def fetch_row(
    connection: Connection,
    table: str,
    columns: str,
    row_id: uuid.UUID,
    *,
    lock: bool = False,
) -> RowMapping | None:
    """Read one row by id. `table` and `columns` are constants in this code base, never input."""
    suffix = " FOR UPDATE" if lock else ""
    return (
        connection.execute(
            text(f"SELECT {columns} FROM vt.{table} WHERE id = :id{suffix}"), {"id": row_id}
        )
        .mappings()
        .first()
    )


def patch_row(
    connection: Connection,
    table: str,
    allowed: frozenset[str],
    changes: dict[str, Any],
    row_id: uuid.UUID,
    returning: str,
) -> RowMapping | None:
    """UPDATE the given columns. Column names are checked against a fixed allowlist and every
    value is a bound parameter."""
    if not changes or not set(changes) <= allowed:
        raise ValueError("unexpected columns")
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    return (
        connection.execute(
            text(f"UPDATE vt.{table} SET {assignments} WHERE id = :row_id RETURNING {returning}"),
            {**changes, "row_id": row_id},
        )
        .mappings()
        .first()
    )


def present[T](value: T | None) -> T:
    """A row this request itself just wrote or read must exist; fail loudly (500) if not."""
    if value is None:
        raise RuntimeError("expected row is missing")
    return value
