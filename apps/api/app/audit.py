"""Audit records, written in the same transaction as the change they describe.

The acting user, organization and role come from the database-resolved request context, never
from the request. The audit log is append-only for the application role (see
docs/trust-boundary.md for the limits of that guarantee).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text

from app.db.session import RequestContext


def record(
    context: RequestContext,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    context.connection.execute(
        text(
            "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type, "
            "entity_id, before_data, after_data) VALUES (:org, :actor, :role, :action, "
            ":entity_type, :entity, CAST(:before AS jsonb), CAST(:after AS jsonb))"
        ),
        {
            "org": context.org_id,
            "actor": context.user_id,
            "role": context.role,
            "action": action,
            "entity_type": entity_type,
            "entity": entity_id,
            "before": json.dumps(before, default=str) if before is not None else None,
            "after": json.dumps(after, default=str) if after is not None else None,
        },
    )
