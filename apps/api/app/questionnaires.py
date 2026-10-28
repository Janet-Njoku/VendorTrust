"""Loading questionnaire versions with their questions and options (always under RLS)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import Connection, text

from app.schemas_questionnaires import OptionOut, QuestionOut, VersionOut

VERSION_COLUMNS = (
    "id, org_id, template_id, version_number, status, low_max, medium_max, high_max, "
    "published_at, created_at, updated_at"
)
QUESTION_COLUMNS = "id, position, category, prompt, guidance, weight, status"


def load_questions(
    connection: Connection, version_id: uuid.UUID, *, include_archived: bool
) -> list[QuestionOut]:
    params = {"version": version_id, "all": include_archived}
    questions = (
        connection.execute(
            text(
                f"SELECT {QUESTION_COLUMNS} FROM vt.questions WHERE version_id = :version "
                "AND (CAST(:all AS boolean) OR status = 'active') ORDER BY position, id"
            ),
            params,
        )
        .mappings()
        .all()
    )
    options: dict[uuid.UUID, list[OptionOut]] = defaultdict(list)
    for row in (
        connection.execute(
            text(
                "SELECT o.id, o.question_id, o.position, o.label, o.risk_points, o.status "
                "FROM vt.question_options o JOIN vt.questions q ON q.id = o.question_id "
                "WHERE q.version_id = :version "
                "AND (CAST(:all AS boolean) OR o.status = 'active') ORDER BY o.position, o.id"
            ),
            params,
        )
        .mappings()
        .all()
    ):
        options[row["question_id"]].append(OptionOut.model_validate(dict(row)))
    return [QuestionOut.model_validate({**dict(q), "options": options[q["id"]]}) for q in questions]


def load_version(
    connection: Connection, version_id: uuid.UUID, *, include_archived: bool = False
) -> VersionOut | None:
    row = (
        connection.execute(
            text(f"SELECT {VERSION_COLUMNS} FROM vt.questionnaire_versions WHERE id = :id"),
            {"id": version_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    questions = load_questions(connection, version_id, include_archived=include_archived)
    return VersionOut.model_validate({**dict(row), "questions": questions})


def insert_question(
    connection: Connection,
    org_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    category: str,
    prompt: str,
    weight: int,
    options: list[dict[str, Any]],
    guidance: str | None = None,
) -> QuestionOut:
    """Append a question (and its options, in the order given) to a draft version."""
    question = (
        connection.execute(
            text(
                "INSERT INTO vt.questions (org_id, version_id, position, category, prompt, "
                "guidance, weight) SELECT :org, :version, coalesce(max(position), 0) + 1, "
                ":category, :prompt, :guidance, :weight FROM vt.questions "
                f"WHERE version_id = :version RETURNING {QUESTION_COLUMNS}"
            ),
            {
                "org": org_id,
                "version": version_id,
                "category": category,
                "prompt": prompt,
                "guidance": guidance,
                "weight": weight,
            },
        )
        .mappings()
        .one()
    )
    saved: list[OptionOut] = []
    for position, option in enumerate(options, start=1):
        row = (
            connection.execute(
                text(
                    "INSERT INTO vt.question_options (org_id, question_id, position, label, "
                    "risk_points) VALUES (:org, :question, :position, :label, :points) "
                    "RETURNING id, position, label, risk_points, status"
                ),
                {
                    "org": org_id,
                    "question": question["id"],
                    "position": position,
                    "label": option["label"],
                    "points": option["risk_points"],
                },
            )
            .mappings()
            .one()
        )
        saved.append(OptionOut.model_validate(dict(row)))
    return QuestionOut.model_validate({**dict(question), "options": saved})
