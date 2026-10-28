"""Questionnaire templates (versioned) and vendor assessments: tables, constraints, indexes.

Model:

* A TEMPLATE belongs to one organization and has numbered VERSIONS. A version is a draft until
  published; a published version is immutable (enforced by triggers in 0005), so every assessment
  keeps the exact questions, options, weights and risk thresholds it started with.
* QUESTIONS and OPTIONS are soft-archived (never deleted). An option's `risk_points` is
  0 (best answer) to 100 (worst); NULL means "not applicable" and is left out of the score.
* An ASSESSMENT binds one vendor to one published version. RESPONSES pick one option per question.
* Composite foreign keys keep every row inside one organization and one version: an assessment
  cannot point at another tenant's vendor or version, a response cannot use a question from a
  different version, and an option must belong to its question.

Row level security, grants and triggers are added in 0005.

Revision ID: 0004_questionnaire_tables
Revises: 0003_rls_grants_triggers
"""

from alembic import op

revision = "0004_questionnaire_tables"
down_revision = "0003_rls_grants_triggers"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE vt.questionnaire_templates (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL,
    name         text NOT NULL,
    description  text,
    status       text NOT NULL DEFAULT 'active',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_questionnaire_templates_org FOREIGN KEY (org_id)
        REFERENCES vt.organizations (id),
    CONSTRAINT uq_questionnaire_templates_org_id_id UNIQUE (org_id, id),
    CONSTRAINT ck_questionnaire_templates_name CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    CONSTRAINT ck_questionnaire_templates_description
        CHECK (description IS NULL OR length(description) <= 2000),
    CONSTRAINT ck_questionnaire_templates_status CHECK (status IN ('active', 'archived'))
);
CREATE INDEX ix_questionnaire_templates_org_id_name ON vt.questionnaire_templates (org_id, name);

CREATE TABLE vt.questionnaire_versions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL,
    template_id     uuid NOT NULL,
    version_number  integer NOT NULL,
    status          text NOT NULL DEFAULT 'draft',
    -- risk classification thresholds frozen with the version (score < low_max is 'low', ...)
    low_max         integer NOT NULL DEFAULT 25,
    medium_max      integer NOT NULL DEFAULT 50,
    high_max        integer NOT NULL DEFAULT 75,
    published_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_questionnaire_versions_template FOREIGN KEY (org_id, template_id)
        REFERENCES vt.questionnaire_templates (org_id, id),
    CONSTRAINT uq_questionnaire_versions_org_id_id UNIQUE (org_id, id),
    CONSTRAINT uq_questionnaire_versions_template_number UNIQUE (template_id, version_number),
    CONSTRAINT ck_questionnaire_versions_number CHECK (version_number >= 1),
    CONSTRAINT ck_questionnaire_versions_status
        CHECK (status IN ('draft', 'published', 'retired')),
    CONSTRAINT ck_questionnaire_versions_thresholds
        CHECK (0 < low_max AND low_max < medium_max AND medium_max < high_max AND high_max <= 100),
    CONSTRAINT ck_questionnaire_versions_published
        CHECK (status <> 'published' OR published_at IS NOT NULL)
);
-- at most one draft per template at a time
CREATE UNIQUE INDEX uq_questionnaire_versions_one_draft ON vt.questionnaire_versions (template_id)
    WHERE status = 'draft';

CREATE TABLE vt.questions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL,
    version_id  uuid NOT NULL,
    position    integer NOT NULL,
    category    text NOT NULL,
    prompt      text NOT NULL,
    guidance    text,
    weight      integer NOT NULL,
    status      text NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_questions_version FOREIGN KEY (org_id, version_id)
        REFERENCES vt.questionnaire_versions (org_id, id),
    CONSTRAINT uq_questions_org_id_id UNIQUE (org_id, id),
    CONSTRAINT ck_questions_position CHECK (position >= 1),
    CONSTRAINT ck_questions_category CHECK (category ~ '^[a-z][a-z0-9_]{1,59}$'),
    CONSTRAINT ck_questions_prompt CHECK (length(btrim(prompt)) BETWEEN 5 AND 1000),
    CONSTRAINT ck_questions_guidance CHECK (guidance IS NULL OR length(guidance) <= 2000),
    CONSTRAINT ck_questions_weight CHECK (weight BETWEEN 1 AND 10),
    CONSTRAINT ck_questions_status CHECK (status IN ('active', 'archived'))
);
CREATE INDEX ix_questions_version_id_position ON vt.questions (version_id, position);

CREATE TABLE vt.question_options (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL,
    question_id  uuid NOT NULL,
    position     integer NOT NULL,
    label        text NOT NULL,
    risk_points  integer,
    status       text NOT NULL DEFAULT 'active',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_question_options_question FOREIGN KEY (org_id, question_id)
        REFERENCES vt.questions (org_id, id),
    CONSTRAINT uq_question_options_org_id_id UNIQUE (org_id, id),
    CONSTRAINT uq_question_options_question_id_id UNIQUE (question_id, id),
    CONSTRAINT ck_question_options_position CHECK (position >= 1),
    CONSTRAINT ck_question_options_label CHECK (length(btrim(label)) BETWEEN 1 AND 200),
    CONSTRAINT ck_question_options_risk_points
        CHECK (risk_points IS NULL OR risk_points BETWEEN 0 AND 100),
    CONSTRAINT ck_question_options_status CHECK (status IN ('active', 'archived'))
);
CREATE INDEX ix_question_options_question_id_position ON vt.question_options (question_id, position);

CREATE TABLE vt.assessments (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL,
    vendor_id   uuid NOT NULL,
    version_id  uuid NOT NULL,
    status      text NOT NULL DEFAULT 'in_progress',
    created_by  uuid NOT NULL,
    score       numeric(4, 1),
    risk_level  text,
    scored_at   timestamptz,
    scored_by   uuid,
    breakdown   jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_assessments_vendor FOREIGN KEY (org_id, vendor_id)
        REFERENCES vt.vendors (org_id, id),
    CONSTRAINT fk_assessments_version FOREIGN KEY (org_id, version_id)
        REFERENCES vt.questionnaire_versions (org_id, id),
    CONSTRAINT fk_assessments_created_by FOREIGN KEY (created_by) REFERENCES vt.app_users (id),
    CONSTRAINT fk_assessments_scored_by FOREIGN KEY (scored_by) REFERENCES vt.app_users (id),
    CONSTRAINT uq_assessments_org_id_id UNIQUE (org_id, id),
    CONSTRAINT ck_assessments_status CHECK (status IN ('in_progress', 'scored')),
    CONSTRAINT ck_assessments_score CHECK (score IS NULL OR score BETWEEN 0 AND 100),
    CONSTRAINT ck_assessments_risk_level
        CHECK (risk_level IS NULL OR risk_level IN ('low', 'medium', 'high', 'critical')),
    -- a result exists if and only if the assessment is scored
    CONSTRAINT ck_assessments_result CHECK (
        (status = 'scored' AND score IS NOT NULL AND risk_level IS NOT NULL
            AND scored_at IS NOT NULL AND scored_by IS NOT NULL AND breakdown IS NOT NULL)
        OR (status = 'in_progress' AND score IS NULL AND risk_level IS NULL
            AND scored_at IS NULL AND scored_by IS NULL AND breakdown IS NULL))
);
CREATE INDEX ix_assessments_org_id_vendor_id ON vt.assessments (org_id, vendor_id);
CREATE INDEX ix_assessments_version_id ON vt.assessments (version_id);

CREATE TABLE vt.assessment_responses (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         uuid NOT NULL,
    assessment_id  uuid NOT NULL,
    question_id    uuid NOT NULL,
    option_id      uuid NOT NULL,
    comment        text,
    answered_by    uuid NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_assessment_responses_assessment FOREIGN KEY (org_id, assessment_id)
        REFERENCES vt.assessments (org_id, id),
    CONSTRAINT fk_assessment_responses_question FOREIGN KEY (org_id, question_id)
        REFERENCES vt.questions (org_id, id),
    -- the chosen option must belong to the answered question
    CONSTRAINT fk_assessment_responses_option FOREIGN KEY (question_id, option_id)
        REFERENCES vt.question_options (question_id, id),
    CONSTRAINT fk_assessment_responses_answered_by FOREIGN KEY (answered_by)
        REFERENCES vt.app_users (id),
    CONSTRAINT uq_assessment_responses_assessment_question UNIQUE (assessment_id, question_id),
    CONSTRAINT ck_assessment_responses_comment CHECK (comment IS NULL OR length(comment) <= 2000)
);
"""

DOWNGRADE = """
DROP TABLE vt.assessment_responses;
DROP TABLE vt.assessments;
DROP TABLE vt.question_options;
DROP TABLE vt.questions;
DROP TABLE vt.questionnaire_versions;
DROP TABLE vt.questionnaire_templates;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
