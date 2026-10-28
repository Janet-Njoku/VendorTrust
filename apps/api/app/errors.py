"""Uniform error responses that never expose database details, SQL, parameters or tokens.

Integrity rules enforced by the database (immutable versions, final assessments, ...) surface
as their own fixed messages, chosen here by SQLSTATE. The database's own text is never sent.
"""

from __future__ import annotations

import logging

import psycopg.errors
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

logger = logging.getLogger("vendortrust.api")

# SQLSTATE raised by the integrity triggers -> (HTTP status, fixed message)
RULES: dict[str, tuple[int, str]] = {
    "VT003": (409, "An organization must keep at least one active org_admin"),
    "VT010": (
        409,
        "The questionnaire is not ready to publish: it needs at least one question, "
        "and every question needs at least two options",
    ),
    "VT011": (409, "Only a draft questionnaire version can be edited"),
    "VT012": (
        409,
        "This assessment cannot be started: the questionnaire version must be published, "
        "its questionnaire active, and the vendor active",
    ),
    "VT013": (409, "The assessment is final and can no longer change"),
    "VT014": (422, "Invalid question or option for this assessment"),
    "VT015": (409, "Invalid questionnaire version status change"),
    "VT016": (409, "Every question must be answered before the assessment can be scored"),
}


def _detail(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SQLAlchemyError)
    async def database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        original = exc.orig if isinstance(exc, DBAPIError) else None
        if isinstance(original, psycopg.errors.InsufficientPrivilege):
            # row level security or a grant refused the write: the caller lacks permission
            return _detail(403, "Insufficient permissions")
        rule = RULES.get(getattr(original, "sqlstate", None) or "")
        if rule is not None:
            return _detail(*rule)
        if isinstance(original, psycopg.errors.UniqueViolation):
            return _detail(409, "Conflict with existing data")
        if isinstance(original, psycopg.errors.ForeignKeyViolation):
            return _detail(422, "Invalid reference")
        if isinstance(original, psycopg.errors.CheckViolation | psycopg.errors.NotNullViolation):
            return _detail(422, "Invalid value")
        # Log only the error class: messages can contain SQL, parameters or identifiers.
        logger.error("database error: %s", type(original or exc).__name__)
        return _detail(500, "Internal server error")
