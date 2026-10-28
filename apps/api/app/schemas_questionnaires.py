"""Models for questionnaire templates, versions, questions, assessments and results.

Unknown fields are rejected. The organization, acting user and every id of a parent are
never accepted from a body; they come from the path and the database-resolved context.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

ItemStatus = Literal["active", "archived"]
VersionStatus = Literal["draft", "published", "retired"]
AssessmentStatus = Literal["in_progress", "scored"]
RiskLevel = Literal["low", "medium", "high", "critical"]


def _clean(value: str) -> str:
    if any((ord(ch) < 32 and ch not in "\n\t") or ord(ch) == 127 for ch in value):
        raise ValueError("must not contain control characters")
    return value


def _single_line(value: str) -> str:
    if "\n" in value or "\t" in value:
        raise ValueError("must be a single line")
    return value


Name = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_single_line)]
Prompt = Annotated[str, Field(min_length=5, max_length=1000), AfterValidator(_clean)]
LongText = Annotated[str, Field(max_length=2000), AfterValidator(_clean)]
Category = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")]
Weight = Annotated[int, Field(ge=1, le=10)]
RiskPoints = Annotated[int, Field(ge=0, le=100)]
Comment = Annotated[str, Field(max_length=2000), AfterValidator(_clean)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Patch(Strict):
    """A partial update: at least one field, and only listed fields may be set to null."""

    NULLABLE: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _valid_patch(self) -> Patch:
        if not self.model_fields_set:
            raise ValueError("provide at least one field to change")
        for field in self.model_fields_set:
            if getattr(self, field) is None and field not in self.NULLABLE:
                raise ValueError(f"{field} cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self.model_fields_set}


# --- templates ---


class TemplateCreate(Strict):
    name: Name
    description: LongText | None = None


class StarterCreate(Strict):
    name: Name | None = None


class TemplateUpdate(Patch):
    NULLABLE = frozenset({"description"})

    name: Name | None = None
    description: LongText | None = None
    status: Literal["active", "archived"] | None = None


class TemplateOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str | None
    status: Literal["active", "archived"]
    created_at: datetime
    updated_at: datetime


class VersionSummary(BaseModel):
    id: uuid.UUID
    version_number: int
    status: VersionStatus
    question_count: int
    published_at: datetime | None


class TemplateDetail(TemplateOut):
    versions: list[VersionSummary]


# --- versions, questions, options ---


class OptionIn(Strict):
    label: Name
    risk_points: RiskPoints | None  # required: null means "not applicable"


class OptionUpdate(Patch):
    NULLABLE = frozenset({"risk_points"})

    label: Name | None = None
    risk_points: RiskPoints | None = None
    status: ItemStatus | None = None


class OptionOut(BaseModel):
    id: uuid.UUID
    position: int
    label: str
    risk_points: int | None
    status: ItemStatus


class QuestionIn(Strict):
    category: Category
    prompt: Prompt
    guidance: LongText | None = None
    weight: Weight
    options: Annotated[list[OptionIn], Field(min_length=2, max_length=12)]


class QuestionUpdate(Patch):
    NULLABLE = frozenset({"guidance"})

    category: Category | None = None
    prompt: Prompt | None = None
    guidance: LongText | None = None
    weight: Weight | None = None
    status: ItemStatus | None = None


class QuestionOut(BaseModel):
    id: uuid.UUID
    position: int
    category: str
    prompt: str
    guidance: str | None
    weight: int
    status: ItemStatus
    options: list[OptionOut]


class ThresholdsUpdate(Strict):
    low_max: Annotated[int, Field(ge=1, le=100)]
    medium_max: Annotated[int, Field(ge=1, le=100)]
    high_max: Annotated[int, Field(ge=1, le=100)]

    @model_validator(mode="after")
    def _ordered(self) -> ThresholdsUpdate:
        if not 0 < self.low_max < self.medium_max < self.high_max <= 100:
            raise ValueError("thresholds must satisfy 0 < low_max < medium_max < high_max <= 100")
        return self


class OrderIn(Strict):
    question_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def _unique(self) -> OrderIn:
        if len(set(self.question_ids)) != len(self.question_ids):
            raise ValueError("question_ids must not repeat")
        return self


class VersionOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    template_id: uuid.UUID
    version_number: int
    status: VersionStatus
    low_max: int
    medium_max: int
    high_max: int
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    questions: list[QuestionOut]


# --- assessments ---


class AssessmentCreate(Strict):
    """Name exactly one: a specific published version, or a questionnaire (its latest published)."""

    version_id: uuid.UUID | None = None
    questionnaire_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> AssessmentCreate:
        if (self.version_id is None) == (self.questionnaire_id is None):
            raise ValueError("provide exactly one of version_id or questionnaire_id")
        return self


class ResponseIn(Strict):
    option_id: uuid.UUID
    comment: Comment | None = None


class ResponseOut(BaseModel):
    id: uuid.UUID
    assessment_id: uuid.UUID
    question_id: uuid.UUID
    option_id: uuid.UUID
    comment: str | None
    answered_by: uuid.UUID
    updated_at: datetime


class AssessmentOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    vendor_id: uuid.UUID
    version_id: uuid.UUID
    status: AssessmentStatus
    created_by: uuid.UUID
    score: float | None
    risk_level: RiskLevel | None
    scored_at: datetime | None
    scored_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class AssessmentPage(BaseModel):
    items: list[AssessmentOut]
    limit: int
    offset: int


class AssessmentQuestion(QuestionOut):
    response: ResponseOut | None


class Progress(BaseModel):
    answered: int
    total: int


class AssessmentDetail(AssessmentOut):
    progress: Progress
    questions: list[AssessmentQuestion]


class ResultOut(BaseModel):
    assessment_id: uuid.UUID
    vendor_id: uuid.UUID
    version_id: uuid.UUID
    score: float
    risk_level: RiskLevel
    scored_at: datetime
    scored_by: uuid.UUID
    breakdown: dict[str, Any]
