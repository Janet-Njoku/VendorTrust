"""Questionnaire templates, versions, questions and options: fully configurable data.

Model: a template has numbered versions. Only a DRAFT version can be edited; publishing freezes
it (questions, options, weights, thresholds), and further changes happen in a new draft cloned
from the latest published version. Questions and options are archived, never deleted. Assessments
bind to one published version, so editing a template never changes an assessment already started.

Read: org_admin, analyst, reviewer, auditor. Write: org_admin and analyst. Row level security and
database triggers enforce the same rules underneath.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app import audit
from app.db.session import RequestContext
from app.deps import require_staff, require_writer
from app.questionnaire_starter import STARTER_DESCRIPTION, STARTER_NAME, starter_questions
from app.questionnaires import (
    QUESTION_COLUMNS,
    VERSION_COLUMNS,
    insert_question,
    load_questions,
    load_version,
)
from app.routers.common import Context, fetch_row, not_found, patch_row, present
from app.schemas_questionnaires import (
    OptionIn,
    OptionOut,
    OptionUpdate,
    OrderIn,
    QuestionIn,
    QuestionOut,
    QuestionUpdate,
    StarterCreate,
    TemplateCreate,
    TemplateDetail,
    TemplateOut,
    TemplateUpdate,
    ThresholdsUpdate,
    VersionOut,
)

router = APIRouter(tags=["questionnaires"])

TEMPLATE_COLUMNS = "id, org_id, name, description, status, created_at, updated_at"
OPTION_COLUMNS = "id, position, label, risk_points, status"


def _template_detail(context: RequestContext, template_id: uuid.UUID) -> TemplateDetail | None:
    row = fetch_row(context.connection, "questionnaire_templates", TEMPLATE_COLUMNS, template_id)
    if row is None:
        return None
    versions = (
        context.connection.execute(
            text(
                "SELECT v.id, v.version_number, v.status, v.published_at, "
                "(SELECT count(*) FROM vt.questions q WHERE q.version_id = v.id "
                "AND q.status = 'active') AS question_count "
                "FROM vt.questionnaire_versions v WHERE v.template_id = :t "
                "ORDER BY v.version_number DESC"
            ),
            {"t": template_id},
        )
        .mappings()
        .all()
    )
    return TemplateDetail.model_validate({**dict(row), "versions": [dict(v) for v in versions]})


def _new_template(
    context: RequestContext, name: str, description: str | None
) -> tuple[uuid.UUID, uuid.UUID]:
    connection = context.connection
    template = (
        connection.execute(
            text(
                "INSERT INTO vt.questionnaire_templates (org_id, name, description) "
                "VALUES (:org, :name, :description) RETURNING id"
            ),
            {"org": context.org_id, "name": name, "description": description},
        )
        .mappings()
        .one()
    )
    version = (
        connection.execute(
            text(
                "INSERT INTO vt.questionnaire_versions (org_id, template_id, version_number) "
                "VALUES (:org, :template, 1) RETURNING id"
            ),
            {"org": context.org_id, "template": template["id"]},
        )
        .mappings()
        .one()
    )
    return template["id"], version["id"]


# --- templates ---


@router.post("/questionnaires", response_model=TemplateDetail, status_code=201)
def create_template(body: TemplateCreate, context: Context) -> TemplateDetail:
    require_writer(context)
    template_id, version_id = _new_template(context, body.name, body.description)
    detail = _template_detail(context, template_id)
    detail = present(detail)
    audit.record(
        context,
        "questionnaire.create",
        "questionnaire",
        template_id,
        None,
        {"name": body.name, "draft_version_id": str(version_id)},
    )
    context.commit()
    return detail


@router.post("/questionnaires/from-starter", response_model=TemplateDetail, status_code=201)
def create_from_starter(context: Context, body: StarterCreate | None = None) -> TemplateDetail:
    """Copy the built-in starter questionnaire into this organization as an editable draft."""
    require_writer(context)
    name = body.name if body and body.name else STARTER_NAME
    template_id, version_id = _new_template(context, name, STARTER_DESCRIPTION)
    questions = starter_questions()
    for question in questions:
        insert_question(context.connection, context.org_id, version_id, **question)
    audit.record(
        context,
        "questionnaire.create_from_starter",
        "questionnaire",
        template_id,
        None,
        {"name": name, "draft_version_id": str(version_id), "questions": len(questions)},
    )
    detail = _template_detail(context, template_id)
    context.commit()
    detail = present(detail)
    return detail


@router.get("/questionnaires", response_model=list[TemplateOut])
def list_templates(
    context: Context,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[TemplateOut]:
    require_staff(context)
    rows = (
        context.connection.execute(
            text(
                f"SELECT {TEMPLATE_COLUMNS} FROM vt.questionnaire_templates "
                "WHERE CAST(:all AS boolean) OR status = 'active' "
                "ORDER BY name, id LIMIT :limit OFFSET :offset"
            ),
            {"all": include_archived, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return [TemplateOut.model_validate(dict(r)) for r in rows]


@router.get("/questionnaires/{template_id}", response_model=TemplateDetail)
def get_template(template_id: uuid.UUID, context: Context) -> TemplateDetail:
    require_staff(context)
    detail = _template_detail(context, template_id)
    if detail is None:
        raise not_found("Questionnaire")
    return detail


@router.patch("/questionnaires/{template_id}", response_model=TemplateDetail)
def update_template(
    template_id: uuid.UUID, body: TemplateUpdate, context: Context
) -> TemplateDetail:
    require_writer(context)
    before = fetch_row(
        context.connection, "questionnaire_templates", TEMPLATE_COLUMNS, template_id, lock=True
    )
    if before is None:
        raise not_found("Questionnaire")
    after = patch_row(
        context.connection,
        "questionnaire_templates",
        frozenset({"name", "description", "status"}),
        body.changes(),
        template_id,
        TEMPLATE_COLUMNS,
    )
    after = present(after)
    action = (
        "questionnaire.archive"
        if after["status"] == "archived" and before["status"] != "archived"
        else "questionnaire.update"
    )
    audit.record(
        context,
        action,
        "questionnaire",
        template_id,
        TemplateOut.model_validate(dict(before)).model_dump(mode="json"),
        TemplateOut.model_validate(dict(after)).model_dump(mode="json"),
    )
    detail = _template_detail(context, template_id)
    context.commit()
    detail = present(detail)
    return detail


# --- versions ---


@router.post("/questionnaires/{template_id}/versions", response_model=VersionOut, status_code=201)
def create_version(template_id: uuid.UUID, context: Context) -> VersionOut:
    """Start a new draft, cloned from the latest published (or retired) version."""
    require_writer(context)
    connection = context.connection
    template = fetch_row(
        connection, "questionnaire_templates", TEMPLATE_COLUMNS, template_id, lock=True
    )
    if template is None:
        raise not_found("Questionnaire")
    if template["status"] != "active":
        raise HTTPException(409, "The questionnaire is archived")
    versions = (
        connection.execute(
            text(
                "SELECT id, version_number, status, low_max, medium_max, high_max "
                "FROM vt.questionnaire_versions WHERE template_id = :t ORDER BY version_number DESC"
            ),
            {"t": template_id},
        )
        .mappings()
        .all()
    )
    if any(v["status"] == "draft" for v in versions):
        raise HTTPException(409, "A draft version already exists; edit or retire it first")
    source = next(v for v in versions if v["status"] != "draft")
    created = (
        connection.execute(
            text(
                "INSERT INTO vt.questionnaire_versions (org_id, template_id, version_number, "
                "low_max, medium_max, high_max) VALUES (:org, :t, :number, :low, :medium, :high) "
                "RETURNING id"
            ),
            {
                "org": context.org_id,
                "t": template_id,
                "number": versions[0]["version_number"] + 1,
                "low": source["low_max"],
                "medium": source["medium_max"],
                "high": source["high_max"],
            },
        )
        .mappings()
        .one()
    )
    new_id = created["id"]
    for question in (
        connection.execute(
            text(
                "SELECT id, category, prompt, guidance, weight FROM vt.questions "
                "WHERE version_id = :v AND status = 'active' ORDER BY position, id"
            ),
            {"v": source["id"]},
        )
        .mappings()
        .all()
    ):
        options = [
            {"label": o["label"], "risk_points": o["risk_points"]}
            for o in connection.execute(
                text(
                    "SELECT label, risk_points FROM vt.question_options "
                    "WHERE question_id = :q AND status = 'active' ORDER BY position, id"
                ),
                {"q": question["id"]},
            )
            .mappings()
            .all()
        ]
        insert_question(
            connection,
            context.org_id,
            new_id,
            category=question["category"],
            prompt=question["prompt"],
            guidance=question["guidance"],
            weight=question["weight"],
            options=options,
        )
    audit.record(
        context,
        "questionnaire.version.create",
        "questionnaire_version",
        new_id,
        None,
        {"template_id": str(template_id), "cloned_from": str(source["id"])},
    )
    version = load_version(connection, new_id)
    context.commit()
    version = present(version)
    return version


@router.get("/questionnaire-versions/{version_id}", response_model=VersionOut)
def get_version(
    version_id: uuid.UUID, context: Context, include_archived: bool = False
) -> VersionOut:
    require_staff(context)
    version = load_version(context.connection, version_id, include_archived=include_archived)
    if version is None:
        raise not_found("Questionnaire version")
    return version


@router.patch("/questionnaire-versions/{version_id}", response_model=VersionOut)
def update_thresholds(
    version_id: uuid.UUID, body: ThresholdsUpdate, context: Context
) -> VersionOut:
    """Change the risk thresholds of a DRAFT version. Published versions are frozen."""
    require_writer(context)
    before = fetch_row(
        context.connection, "questionnaire_versions", VERSION_COLUMNS, version_id, lock=True
    )
    if before is None:
        raise not_found("Questionnaire version")
    patch_row(
        context.connection,
        "questionnaire_versions",
        frozenset({"low_max", "medium_max", "high_max"}),
        body.model_dump(),
        version_id,
        "id",
    )
    audit.record(
        context,
        "questionnaire.version.update_thresholds",
        "questionnaire_version",
        version_id,
        {k: before[k] for k in ("low_max", "medium_max", "high_max")},
        body.model_dump(),
    )
    version = load_version(context.connection, version_id)
    context.commit()
    version = present(version)
    return version


def _transition(
    context: RequestContext, version_id: uuid.UUID, target: str, action: str, allowed_from: set[str]
) -> VersionOut:
    require_writer(context)
    before = fetch_row(
        context.connection, "questionnaire_versions", VERSION_COLUMNS, version_id, lock=True
    )
    if before is None:
        raise not_found("Questionnaire version")
    if before["status"] not in allowed_from:
        raise HTTPException(409, f"A {before['status']} version cannot be {target}")
    patch_row(
        context.connection,
        "questionnaire_versions",
        frozenset({"status"}),
        {"status": target},
        version_id,
        "id",
    )
    audit.record(
        context,
        action,
        "questionnaire_version",
        version_id,
        {"status": before["status"]},
        {"status": target, "version_number": before["version_number"]},
    )
    version = load_version(context.connection, version_id)
    context.commit()
    version = present(version)
    return version


@router.post("/questionnaire-versions/{version_id}/publish", response_model=VersionOut)
def publish_version(version_id: uuid.UUID, context: Context) -> VersionOut:
    """Freeze a draft. It becomes available for new assessments and can never change."""
    return _transition(context, version_id, "published", "questionnaire.version.publish", {"draft"})


@router.post("/questionnaire-versions/{version_id}/retire", response_model=VersionOut)
def retire_version(version_id: uuid.UUID, context: Context) -> VersionOut:
    """Stop using a version for new assessments (existing assessments are unaffected)."""
    return _transition(
        context, version_id, "retired", "questionnaire.version.retire", {"draft", "published"}
    )


# --- questions and options (draft versions only; the database enforces it) ---


@router.post(
    "/questionnaire-versions/{version_id}/questions", response_model=QuestionOut, status_code=201
)
def add_question(version_id: uuid.UUID, body: QuestionIn, context: Context) -> QuestionOut:
    require_writer(context)
    if fetch_row(context.connection, "questionnaire_versions", "id", version_id) is None:
        raise not_found("Questionnaire version")
    question = insert_question(
        context.connection,
        context.org_id,
        version_id,
        category=body.category,
        prompt=body.prompt,
        guidance=body.guidance,
        weight=body.weight,
        options=[o.model_dump() for o in body.options],
    )
    audit.record(
        context,
        "question.create",
        "question",
        question.id,
        None,
        {**question.model_dump(mode="json"), "version_id": str(version_id)},
    )
    context.commit()
    return question


def _question_out(context: RequestContext, question_id: uuid.UUID) -> QuestionOut:
    version_id = fetch_row(context.connection, "questions", "version_id", question_id)
    version_id = present(version_id)
    for question in load_questions(
        context.connection, version_id["version_id"], include_archived=True
    ):
        if question.id == question_id:
            return question
    raise not_found("Question")


@router.patch("/questions/{question_id}", response_model=QuestionOut)
def update_question(question_id: uuid.UUID, body: QuestionUpdate, context: Context) -> QuestionOut:
    """Edit or archive (status=archived) a question of a draft version."""
    require_writer(context)
    before = fetch_row(context.connection, "questions", QUESTION_COLUMNS, question_id, lock=True)
    if before is None:
        raise not_found("Question")
    after = patch_row(
        context.connection,
        "questions",
        frozenset({"category", "prompt", "guidance", "weight", "status"}),
        body.changes(),
        question_id,
        QUESTION_COLUMNS,
    )
    after = present(after)
    archived = after["status"] == "archived" and before["status"] != "archived"
    audit.record(
        context,
        "question.archive" if archived else "question.update",
        "question",
        question_id,
        {k: before[k] for k in body.changes()},
        {k: after[k] for k in body.changes()},
    )
    result = _question_out(context, question_id)
    context.commit()
    return result


@router.post("/questions/{question_id}/options", response_model=OptionOut, status_code=201)
def add_option(question_id: uuid.UUID, body: OptionIn, context: Context) -> OptionOut:
    require_writer(context)
    if fetch_row(context.connection, "questions", "id", question_id) is None:
        raise not_found("Question")
    row = (
        context.connection.execute(
            text(
                "INSERT INTO vt.question_options (org_id, question_id, position, label, "
                "risk_points) SELECT :org, :q, coalesce(max(position), 0) + 1, :label, :points "
                f"FROM vt.question_options WHERE question_id = :q RETURNING {OPTION_COLUMNS}"
            ),
            {
                "org": context.org_id,
                "q": question_id,
                "label": body.label,
                "points": body.risk_points,
            },
        )
        .mappings()
        .one()
    )
    option = OptionOut.model_validate(dict(row))
    audit.record(
        context,
        "option.create",
        "question_option",
        option.id,
        None,
        {**option.model_dump(mode="json"), "question_id": str(question_id)},
    )
    context.commit()
    return option


@router.patch("/options/{option_id}", response_model=OptionOut)
def update_option(option_id: uuid.UUID, body: OptionUpdate, context: Context) -> OptionOut:
    """Edit or archive (status=archived) an option of a draft version."""
    require_writer(context)
    before = fetch_row(context.connection, "question_options", OPTION_COLUMNS, option_id, lock=True)
    if before is None:
        raise not_found("Option")
    after = patch_row(
        context.connection,
        "question_options",
        frozenset({"label", "risk_points", "status"}),
        body.changes(),
        option_id,
        OPTION_COLUMNS,
    )
    after = present(after)
    archived = after["status"] == "archived" and before["status"] != "archived"
    audit.record(
        context,
        "option.archive" if archived else "option.update",
        "question_option",
        option_id,
        {k: before[k] for k in body.changes()},
        {k: after[k] for k in body.changes()},
    )
    context.commit()
    return OptionOut.model_validate(dict(after))


@router.put("/questionnaire-versions/{version_id}/order", response_model=VersionOut)
def reorder_questions(version_id: uuid.UUID, body: OrderIn, context: Context) -> VersionOut:
    """Set the order of a draft's active questions: list every active question id exactly once."""
    require_writer(context)
    connection = context.connection
    if fetch_row(connection, "questionnaire_versions", "id", version_id) is None:
        raise not_found("Questionnaire version")
    current = [
        r["id"]
        for r in connection.execute(
            text(
                "SELECT id FROM vt.questions WHERE version_id = :v AND status = 'active' "
                "ORDER BY position, id FOR UPDATE"
            ),
            {"v": version_id},
        )
        .mappings()
        .all()
    ]
    if set(current) != set(body.question_ids):
        raise HTTPException(422, "question_ids must list every active question exactly once")
    for position, question_id in enumerate(body.question_ids, start=1):
        connection.execute(
            text("UPDATE vt.questions SET position = :p WHERE id = :id"),
            {"p": position, "id": question_id},
        )
    audit.record(
        context,
        "question.reorder",
        "questionnaire_version",
        version_id,
        {"question_ids": [str(q) for q in current]},
        {"question_ids": [str(q) for q in body.question_ids]},
    )
    version = load_version(connection, version_id)
    context.commit()
    version = present(version)
    return version
