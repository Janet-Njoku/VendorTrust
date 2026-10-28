"""Vendor assessments: start one from a published questionnaire version, record responses,
calculate the score, retrieve the result.

Workflow: `in_progress` (responses can be created and changed) -> `scored` (final). Scoring needs
every active question answered and is computed by the deterministic rules in app.scoring from
the version the assessment started with, so later edits to the template cannot change it.
Responses, score and the scoring decision are all written to the audit log.

Read: org_admin, analyst, reviewer, auditor. Write: org_admin and analyst. A vendor_user has no
access. Row level security and database triggers enforce the same rules underneath.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import RowMapping, text

from app import audit
from app.db.session import RequestContext
from app.deps import require_staff, require_writer
from app.questionnaires import load_questions
from app.routers.common import Context, fetch_row, not_found
from app.schemas_questionnaires import (
    AssessmentCreate,
    AssessmentDetail,
    AssessmentOut,
    AssessmentPage,
    AssessmentQuestion,
    AssessmentStatus,
    Progress,
    ResponseIn,
    ResponseOut,
    ResultOut,
)
from app.scoring import Answer, ScoringError, Thresholds, calculate

router = APIRouter(tags=["assessments"])

ASSESSMENT_COLUMNS = (
    "id, org_id, vendor_id, version_id, status, created_by, score, risk_level, scored_at, "
    "scored_by, created_at, updated_at"
)
RESPONSE_COLUMNS = "id, assessment_id, question_id, option_id, comment, answered_by, updated_at"


def _assessment(
    context: RequestContext, assessment_id: uuid.UUID, *, lock: bool = False
) -> RowMapping:
    row = fetch_row(context.connection, "assessments", ASSESSMENT_COLUMNS, assessment_id, lock=lock)
    if row is None:
        raise not_found("Assessment")
    return row


def _out(row: RowMapping) -> AssessmentOut:
    return AssessmentOut.model_validate(dict(row))


@router.post("/vendors/{vendor_id}/assessments", response_model=AssessmentOut, status_code=201)
def create_assessment(
    vendor_id: uuid.UUID, body: AssessmentCreate, context: Context
) -> AssessmentOut:
    require_writer(context)
    connection = context.connection
    if fetch_row(connection, "vendors", "id", vendor_id) is None:
        raise not_found("Vendor")
    version_id = body.version_id
    if version_id is None:
        latest = (
            connection.execute(
                text(
                    "SELECT v.id FROM vt.questionnaire_versions v "
                    "JOIN vt.questionnaire_templates t ON t.id = v.template_id "
                    "WHERE v.template_id = :t AND v.status = 'published' AND t.status = 'active' "
                    "ORDER BY v.version_number DESC LIMIT 1"
                ),
                {"t": body.questionnaire_id},
            )
            .mappings()
            .first()
        )
        if latest is None:
            raise HTTPException(409, "The questionnaire has no published version available")
        version_id = latest["id"]
    elif fetch_row(connection, "questionnaire_versions", "id", version_id) is None:
        raise not_found("Questionnaire version")
    row = (
        connection.execute(
            text(
                "INSERT INTO vt.assessments (org_id, vendor_id, version_id, created_by) "
                f"VALUES (:org, :vendor, :version, :actor) RETURNING {ASSESSMENT_COLUMNS}"
            ),
            {
                "org": context.org_id,
                "vendor": vendor_id,
                "version": version_id,
                "actor": context.user_id,
            },
        )
        .mappings()
        .one()
    )
    assessment = _out(row)
    audit.record(
        context,
        "assessment.create",
        "assessment",
        assessment.id,
        None,
        {"vendor_id": str(vendor_id), "version_id": str(version_id)},
    )
    context.commit()
    return assessment


@router.get("/assessments", response_model=AssessmentPage)
def list_assessments(
    context: Context,
    vendor_id: uuid.UUID | None = None,
    status: AssessmentStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> AssessmentPage:
    require_staff(context)
    rows = (
        context.connection.execute(
            text(
                f"SELECT {ASSESSMENT_COLUMNS} FROM vt.assessments "
                "WHERE (CAST(:vendor AS uuid) IS NULL OR vendor_id = :vendor) "
                "AND (CAST(:status AS text) IS NULL OR status = :status) "
                "ORDER BY created_at DESC, id LIMIT :limit OFFSET :offset"
            ),
            {"vendor": vendor_id, "status": status, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return AssessmentPage(items=[_out(r) for r in rows], limit=limit, offset=offset)


@router.get("/assessments/{assessment_id}", response_model=AssessmentDetail)
def get_assessment(assessment_id: uuid.UUID, context: Context) -> AssessmentDetail:
    require_staff(context)
    row = _assessment(context, assessment_id)
    responses = {
        r["question_id"]: ResponseOut.model_validate(dict(r))
        for r in context.connection.execute(
            text(
                f"SELECT {RESPONSE_COLUMNS} FROM vt.assessment_responses WHERE assessment_id = :a"
            ),
            {"a": assessment_id},
        )
        .mappings()
        .all()
    }
    questions = [
        AssessmentQuestion.model_validate({**q.model_dump(), "response": responses.get(q.id)})
        for q in load_questions(context.connection, row["version_id"], include_archived=False)
    ]
    answered = sum(1 for q in questions if q.response is not None)
    return AssessmentDetail.model_validate(
        {
            **_out(row).model_dump(),
            "progress": Progress(answered=answered, total=len(questions)),
            "questions": questions,
        }
    )


@router.put("/assessments/{assessment_id}/responses/{question_id}", response_model=ResponseOut)
def set_response(
    assessment_id: uuid.UUID, question_id: uuid.UUID, body: ResponseIn, context: Context
) -> ResponseOut:
    """Record or change the answer to one question while the assessment is in progress."""
    require_writer(context)
    connection = context.connection
    _assessment(context, assessment_id, lock=True)
    before = (
        connection.execute(
            text(
                "SELECT option_id, comment FROM vt.assessment_responses "
                "WHERE assessment_id = :a AND question_id = :q FOR UPDATE"
            ),
            {"a": assessment_id, "q": question_id},
        )
        .mappings()
        .first()
    )
    row = (
        connection.execute(
            text(
                "INSERT INTO vt.assessment_responses (org_id, assessment_id, question_id, "
                "option_id, comment, answered_by) VALUES (:org, :a, :q, :option, :comment, :actor) "
                "ON CONFLICT (assessment_id, question_id) DO UPDATE SET "
                "option_id = EXCLUDED.option_id, comment = EXCLUDED.comment, "
                f"answered_by = EXCLUDED.answered_by RETURNING {RESPONSE_COLUMNS}"
            ),
            {
                "org": context.org_id,
                "a": assessment_id,
                "q": question_id,
                "option": body.option_id,
                "comment": body.comment,
                "actor": context.user_id,
            },
        )
        .mappings()
        .one()
    )
    response = ResponseOut.model_validate(dict(row))
    audit.record(
        context,
        "response.update" if before else "response.create",
        "assessment_response",
        response.id,
        ({"option_id": str(before["option_id"]), "comment": before["comment"]} if before else None),
        {
            "assessment_id": str(assessment_id),
            "question_id": str(question_id),
            "option_id": str(response.option_id),
            "comment": response.comment,
        },
    )
    context.commit()
    return response


@router.post("/assessments/{assessment_id}/score", response_model=ResultOut)
def score_assessment(assessment_id: uuid.UUID, context: Context) -> ResultOut | JSONResponse:
    """Calculate the risk score and finalize the assessment (it can no longer change)."""
    require_writer(context)
    connection = context.connection
    row = _assessment(context, assessment_id, lock=True)
    if row["status"] == "scored":
        raise HTTPException(409, "The assessment has already been scored")
    version = (
        connection.execute(
            text(
                "SELECT low_max, medium_max, high_max FROM vt.questionnaire_versions WHERE id = :v"
            ),
            {"v": row["version_id"]},
        )
        .mappings()
        .one()
    )
    rows = (
        connection.execute(
            text(
                "SELECT q.id, q.category, q.weight, o.risk_points, (r.id IS NOT NULL) AS answered "
                "FROM vt.questions q "
                "LEFT JOIN vt.assessment_responses r "
                "  ON r.question_id = q.id AND r.assessment_id = :a "
                "LEFT JOIN vt.question_options o ON o.id = r.option_id "
                "WHERE q.version_id = :v AND q.status = 'active' ORDER BY q.position, q.id"
            ),
            {"a": assessment_id, "v": row["version_id"]},
        )
        .mappings()
        .all()
    )
    unanswered = [str(r["id"]) for r in rows if not r["answered"]]
    if unanswered:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Every question must be answered before the assessment can be scored",
                "unanswered_question_ids": unanswered,
            },
        )
    try:
        result = calculate(
            [Answer(r["id"], r["category"], r["weight"], r["risk_points"]) for r in rows],
            Thresholds(version["low_max"], version["medium_max"], version["high_max"]),
        )
    except ScoringError as exc:
        raise HTTPException(
            409, "Every question is not applicable, so there is nothing to score"
        ) from exc
    breakdown = result.breakdown()
    scored = (
        connection.execute(
            text(
                "UPDATE vt.assessments SET status = 'scored', score = :score, "
                "risk_level = :level, scored_at = now(), scored_by = :actor, "
                "breakdown = CAST(:breakdown AS jsonb) WHERE id = :id "
                "RETURNING scored_at"
            ),
            {
                "score": result.score,
                "level": result.risk_level,
                "actor": context.user_id,
                "breakdown": json.dumps(breakdown),
                "id": assessment_id,
            },
        )
        .mappings()
        .one()
    )
    audit.record(
        context,
        "assessment.score",
        "assessment",
        assessment_id,
        {"status": "in_progress"},
        {
            "status": "scored",
            "method": breakdown["method"],
            "score": float(result.score),
            "risk_level": result.risk_level,
            "thresholds": breakdown["thresholds"],
            "version_id": str(row["version_id"]),
            "applicable_weight": result.applicable_weight,
        },
    )
    context.commit()
    return ResultOut(
        assessment_id=assessment_id,
        vendor_id=row["vendor_id"],
        version_id=row["version_id"],
        score=float(result.score),
        risk_level=result.risk_level,
        scored_at=scored["scored_at"],
        scored_by=context.user_id,
        breakdown=breakdown,
    )


@router.get("/assessments/{assessment_id}/result", response_model=ResultOut)
def get_result(assessment_id: uuid.UUID, context: Context) -> ResultOut:
    require_staff(context)
    row = (
        context.connection.execute(
            text(
                "SELECT id AS assessment_id, vendor_id, version_id, score, risk_level, "
                "scored_at, scored_by, breakdown FROM vt.assessments "
                "WHERE id = :id AND status = 'scored'"
            ),
            {"id": assessment_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        # distinguish "not found / not visible" from "visible but not scored yet"
        _assessment(context, assessment_id)
        raise HTTPException(409, "The assessment has not been scored yet")
    return ResultOut.model_validate({**dict(row), "score": float(Decimal(row["score"]))})
