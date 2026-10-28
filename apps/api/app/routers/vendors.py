"""Vendor management: create, list, retrieve and update.

Every operation runs inside the request's RLS-scoped transaction (app.deps.request_context).
The role checks here give clear errors; row level security enforces the same rules and the
organization boundary underneath, so a bug in this file cannot leak another tenant's rows.
A vendor that is not visible to the caller is always reported as 404, never 403.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app import audit
from app.db.session import RequestContext
from app.deps import request_context, require_writer
from app.schemas import VendorCreate, VendorOut, VendorPage, VendorStatus, VendorUpdate

router = APIRouter(prefix="/vendors", tags=["vendors"])

Context = Annotated[RequestContext, Depends(request_context)]
COLUMNS = "id, org_id, name, status, created_at, updated_at"


def _not_found() -> HTTPException:
    return HTTPException(404, "Vendor not found")


@router.post("", response_model=VendorOut, status_code=201)
def create_vendor(body: VendorCreate, context: Context) -> VendorOut:
    require_writer(context)
    row = (
        context.connection.execute(
            text(
                f"INSERT INTO vt.vendors (org_id, name, status) VALUES (:org, :name, :status) "
                f"RETURNING {COLUMNS}"
            ),
            {"org": context.org_id, "name": body.name, "status": body.status},
        )
        .mappings()
        .one()
    )
    vendor = VendorOut.model_validate(dict(row))
    audit.record(
        context, "vendor.create", "vendor", vendor.id, None, vendor.model_dump(mode="json")
    )
    context.commit()
    return vendor


@router.get("", response_model=VendorPage)
def list_vendors(
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    status: VendorStatus | None = None,
) -> VendorPage:
    rows = (
        context.connection.execute(
            text(
                f"SELECT {COLUMNS} FROM vt.vendors "
                "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
                # "C" collation: byte order, the same on every platform (the default differs between
                # macOS and Linux). id breaks ties so pages never overlap or skip.
                'ORDER BY name COLLATE "C", id LIMIT :limit OFFSET :offset'
            ),
            {"status": status, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return VendorPage(
        items=[VendorOut.model_validate(dict(r)) for r in rows], limit=limit, offset=offset
    )


@router.get("/{vendor_id}", response_model=VendorOut)
def get_vendor(vendor_id: uuid.UUID, context: Context) -> VendorOut:
    row = (
        context.connection.execute(
            text(f"SELECT {COLUMNS} FROM vt.vendors WHERE id = :id"), {"id": vendor_id}
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _not_found()
    return VendorOut.model_validate(dict(row))


@router.patch("/{vendor_id}", response_model=VendorOut)
def update_vendor(vendor_id: uuid.UUID, body: VendorUpdate, context: Context) -> VendorOut:
    require_writer(context)
    connection = context.connection
    before_row = (
        connection.execute(
            text(f"SELECT {COLUMNS} FROM vt.vendors WHERE id = :id FOR UPDATE"), {"id": vendor_id}
        )
        .mappings()
        .first()
    )
    if before_row is None:
        raise _not_found()
    before = VendorOut.model_validate(dict(before_row))
    row = (
        connection.execute(
            text(
                f"UPDATE vt.vendors SET name = coalesce(:name, name), "
                f"status = coalesce(:status, status) WHERE id = :id RETURNING {COLUMNS}"
            ),
            {"id": vendor_id, "name": body.name, "status": body.status},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _not_found()
    after = VendorOut.model_validate(dict(row))
    audit.record(
        context,
        "vendor.update",
        "vendor",
        vendor_id,
        before.model_dump(mode="json"),
        after.model_dump(mode="json"),
    )
    context.commit()
    return after
