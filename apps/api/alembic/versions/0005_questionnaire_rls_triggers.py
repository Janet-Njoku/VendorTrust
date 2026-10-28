"""Row level security, grants and integrity triggers for questionnaires and assessments.

Same pattern as 0003, so nothing new can bypass the existing controls:

* ENABLE + FORCE row level security on all six tables, policies `TO vt_app` only.
* Every policy requires the claimed organization AND `claim_matches_active_membership()`.
  Read: org_admin, analyst, reviewer, auditor (a vendor_user sees none of this: internal risk
  data). Write: org_admin and analyst. Policies reference only claim readers and the definer
  function, never another table, so the policy graph stays acyclic.
* Column-level grants, no DELETE/TRUNCATE/REFERENCES/TRIGGER: rows are archived, not deleted,
  and identity columns (org_id, version_id, assessment_id ...) can never be changed.
* Triggers (SECURITY INVOKER, so they run under the caller's own RLS) enforce the integrity that
  makes assessments historically reliable:
  - questions and options can only change while their version is a DRAFT;
  - a version moves only draft -> published -> retired, a published version's thresholds are
    frozen, and publishing requires questions that each have at least two active options;
  - an assessment needs a published version of an active template and an active vendor, can be
    scored only when every active question is answered, and is final once scored;
  - a response must answer a question of the assessment's own version, with an active option of
    that question, and only while the assessment is in progress.

Error codes: VT010 not ready to publish, VT011 version not editable, VT012 assessment cannot be
created, VT013 assessment is final, VT014 invalid response, VT015 invalid version transition,
VT016 assessment incomplete.

Revision ID: 0005_questionnaire_rls_triggers
Revises: 0004_questionnaire_tables
"""

from alembic import op

revision = "0005_questionnaire_rls_triggers"
down_revision = "0004_questionnaire_tables"
branch_labels = None
depends_on = None

TABLES = (
    "questionnaire_templates",
    "questionnaire_versions",
    "questions",
    "question_options",
    "assessments",
    "assessment_responses",
)
STAFF = "('org_admin', 'analyst', 'reviewer', 'auditor')"
WRITERS = "('org_admin', 'analyst')"
LIVE = "(SELECT vt.claim_matches_active_membership())"

FUNCTIONS = """
CREATE FUNCTION vt.guard_version_update() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NEW.org_id <> OLD.org_id OR NEW.template_id <> OLD.template_id
       OR NEW.version_number <> OLD.version_number THEN
        RAISE EXCEPTION 'vt: version identity is immutable' USING ERRCODE = 'VT011';
    END IF;
    IF OLD.status = 'retired' THEN
        RAISE EXCEPTION 'vt: a retired version cannot change' USING ERRCODE = 'VT015';
    ELSIF OLD.status = 'published' THEN
        IF NEW.status NOT IN ('published', 'retired') THEN
            RAISE EXCEPTION 'vt: invalid version transition' USING ERRCODE = 'VT015';
        END IF;
        IF NEW.low_max <> OLD.low_max OR NEW.medium_max <> OLD.medium_max
           OR NEW.high_max <> OLD.high_max OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
            RAISE EXCEPTION 'vt: a published version is immutable' USING ERRCODE = 'VT011';
        END IF;
    ELSE  -- draft
        IF NEW.status = 'published' THEN
            IF NOT EXISTS (SELECT 1 FROM vt.questions q
                           WHERE q.version_id = OLD.id AND q.status = 'active') THEN
                RAISE EXCEPTION 'vt: a version needs at least one active question'
                    USING ERRCODE = 'VT010';
            END IF;
            IF EXISTS (SELECT 1 FROM vt.questions q
                       WHERE q.version_id = OLD.id AND q.status = 'active'
                         AND (SELECT count(*) FROM vt.question_options o
                              WHERE o.question_id = q.id AND o.status = 'active') < 2) THEN
                RAISE EXCEPTION 'vt: every active question needs two active options'
                    USING ERRCODE = 'VT010';
            END IF;
            NEW.published_at := pg_catalog.now();
        ELSIF NEW.status NOT IN ('draft', 'retired') THEN
            RAISE EXCEPTION 'vt: invalid version transition' USING ERRCODE = 'VT015';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION vt.guard_question_write() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_status text;
BEGIN
    SELECT v.status INTO v_status FROM vt.questionnaire_versions v WHERE v.id = NEW.version_id;
    IF v_status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'vt: only a draft version can be edited' USING ERRCODE = 'VT011';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION vt.guard_option_write() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_status text;
BEGIN
    SELECT v.status INTO v_status
    FROM vt.questions q JOIN vt.questionnaire_versions v ON v.id = q.version_id
    WHERE q.id = NEW.question_id;
    IF v_status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'vt: only a draft version can be edited' USING ERRCODE = 'VT011';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION vt.guard_assessment_insert() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_status text;
    t_status text;
BEGIN
    SELECT v.status, t.status INTO v_status, t_status
    FROM vt.questionnaire_versions v
    JOIN vt.questionnaire_templates t ON t.id = v.template_id
    WHERE v.id = NEW.version_id;
    IF v_status IS DISTINCT FROM 'published' OR t_status IS DISTINCT FROM 'active'
       OR NOT EXISTS (SELECT 1 FROM vt.vendors d WHERE d.id = NEW.vendor_id AND d.status = 'active')
    THEN
        RAISE EXCEPTION 'vt: cannot start this assessment' USING ERRCODE = 'VT012';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION vt.guard_assessment_update() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF OLD.status = 'scored' THEN
        RAISE EXCEPTION 'vt: a scored assessment is final' USING ERRCODE = 'VT013';
    END IF;
    IF NEW.status = 'scored' AND EXISTS (
        SELECT 1 FROM vt.questions q
        WHERE q.version_id = OLD.version_id AND q.status = 'active'
          AND NOT EXISTS (SELECT 1 FROM vt.assessment_responses r
                          WHERE r.assessment_id = OLD.id AND r.question_id = q.id)
    ) THEN
        RAISE EXCEPTION 'vt: every question must be answered before scoring'
            USING ERRCODE = 'VT016';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION vt.guard_response_write() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    a_status   text;
    a_version  uuid;
    q_version  uuid;
    q_status   text;
    o_status   text;
BEGIN
    SELECT a.status, a.version_id INTO a_status, a_version
    FROM vt.assessments a WHERE a.id = NEW.assessment_id;
    IF a_status IS DISTINCT FROM 'in_progress' THEN
        RAISE EXCEPTION 'vt: the assessment is not open for responses' USING ERRCODE = 'VT013';
    END IF;
    SELECT q.version_id, q.status INTO q_version, q_status
    FROM vt.questions q WHERE q.id = NEW.question_id;
    SELECT o.status INTO o_status FROM vt.question_options o WHERE o.id = NEW.option_id;
    IF q_version IS DISTINCT FROM a_version OR q_status IS DISTINCT FROM 'active'
       OR o_status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'vt: invalid response' USING ERRCODE = 'VT014';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.assessment_id <> OLD.assessment_id
                             OR NEW.question_id <> OLD.question_id) THEN
        RAISE EXCEPTION 'vt: invalid response' USING ERRCODE = 'VT014';
    END IF;
    RETURN NEW;
END
$$;

REVOKE ALL ON FUNCTION
    vt.guard_version_update(), vt.guard_question_write(), vt.guard_option_write(),
    vt.guard_assessment_insert(), vt.guard_assessment_update(), vt.guard_response_write()
    FROM PUBLIC;
"""

GRANTS = """
GRANT SELECT, INSERT (org_id, name, description), UPDATE (name, description, status)
    ON vt.questionnaire_templates TO vt_app;
GRANT SELECT, INSERT (org_id, template_id, version_number, low_max, medium_max, high_max),
      UPDATE (status, low_max, medium_max, high_max) ON vt.questionnaire_versions TO vt_app;
GRANT SELECT, INSERT (org_id, version_id, position, category, prompt, guidance, weight),
      UPDATE (position, category, prompt, guidance, weight, status) ON vt.questions TO vt_app;
GRANT SELECT, INSERT (org_id, question_id, position, label, risk_points),
      UPDATE (position, label, risk_points, status) ON vt.question_options TO vt_app;
GRANT SELECT, INSERT (org_id, vendor_id, version_id, created_by),
      UPDATE (status, score, risk_level, scored_at, scored_by, breakdown)
    ON vt.assessments TO vt_app;
GRANT SELECT, INSERT (org_id, assessment_id, question_id, option_id, comment, answered_by),
      UPDATE (option_id, comment, answered_by) ON vt.assessment_responses TO vt_app;
"""

TRIGGERS = """
CREATE TRIGGER trg_questionnaire_templates_updated BEFORE UPDATE ON vt.questionnaire_templates
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();
CREATE TRIGGER trg_questionnaire_versions_updated BEFORE UPDATE ON vt.questionnaire_versions
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();
CREATE TRIGGER trg_questions_updated BEFORE UPDATE ON vt.questions
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();
CREATE TRIGGER trg_question_options_updated BEFORE UPDATE ON vt.question_options
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();
CREATE TRIGGER trg_assessments_updated BEFORE UPDATE ON vt.assessments
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();
CREATE TRIGGER trg_assessment_responses_updated BEFORE UPDATE ON vt.assessment_responses
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();

CREATE TRIGGER trg_questionnaire_versions_guard BEFORE UPDATE ON vt.questionnaire_versions
    FOR EACH ROW EXECUTE FUNCTION vt.guard_version_update();
CREATE TRIGGER trg_questions_guard BEFORE INSERT OR UPDATE ON vt.questions
    FOR EACH ROW EXECUTE FUNCTION vt.guard_question_write();
CREATE TRIGGER trg_question_options_guard BEFORE INSERT OR UPDATE ON vt.question_options
    FOR EACH ROW EXECUTE FUNCTION vt.guard_option_write();
CREATE TRIGGER trg_assessments_guard_insert BEFORE INSERT ON vt.assessments
    FOR EACH ROW EXECUTE FUNCTION vt.guard_assessment_insert();
CREATE TRIGGER trg_assessments_guard_update BEFORE UPDATE ON vt.assessments
    FOR EACH ROW EXECUTE FUNCTION vt.guard_assessment_update();
CREATE TRIGGER trg_assessment_responses_guard BEFORE INSERT OR UPDATE ON vt.assessment_responses
    FOR EACH ROW EXECUTE FUNCTION vt.guard_response_write();
"""

# the extra condition each table's INSERT/UPDATE must satisfy on top of org + role + live
EXTRA_WRITE_CHECK = {
    "assessments": " AND created_by = vt.claimed_user_id()",
    "assessment_responses": " AND answered_by = vt.claimed_user_id()",
}
# assessments.created_by is fixed at insert; scoring updates must not rewrite it, and a response
# may only be (re)written by the acting user
UPDATE_CHECK_OVERRIDE = {"assessments": ""}


def policies_sql() -> str:
    statements = []
    for table in TABLES:
        base = f"org_id = vt.claimed_org_id() AND {LIVE}"
        statements.append(f"ALTER TABLE vt.{table} ENABLE ROW LEVEL SECURITY;")
        statements.append(f"ALTER TABLE vt.{table} FORCE ROW LEVEL SECURITY;")
        statements.append(
            f"CREATE POLICY {table}_select ON vt.{table} FOR SELECT TO vt_app "
            f"USING ({base} AND vt.claimed_role() IN {STAFF});"
        )
        insert_check = base + f" AND vt.claimed_role() IN {WRITERS}"
        insert_check += EXTRA_WRITE_CHECK.get(table, "")
        statements.append(
            f"CREATE POLICY {table}_insert ON vt.{table} FOR INSERT TO vt_app "
            f"WITH CHECK ({insert_check});"
        )
        update_check = base + f" AND vt.claimed_role() IN {WRITERS}"
        update_check += UPDATE_CHECK_OVERRIDE.get(table, EXTRA_WRITE_CHECK.get(table, ""))
        statements.append(
            f"CREATE POLICY {table}_update ON vt.{table} FOR UPDATE TO vt_app "
            f"USING ({base} AND vt.claimed_role() IN {WRITERS}) WITH CHECK ({update_check});"
        )
    return "\n".join(statements)


def upgrade() -> None:
    op.execute(FUNCTIONS)
    op.execute(policies_sql())
    op.execute(GRANTS)
    op.execute(TRIGGERS)


def downgrade() -> None:
    for trigger, table in (
        ("trg_assessment_responses_guard", "assessment_responses"),
        ("trg_assessments_guard_update", "assessments"),
        ("trg_assessments_guard_insert", "assessments"),
        ("trg_question_options_guard", "question_options"),
        ("trg_questions_guard", "questions"),
        ("trg_questionnaire_versions_guard", "questionnaire_versions"),
        ("trg_assessment_responses_updated", "assessment_responses"),
        ("trg_assessments_updated", "assessments"),
        ("trg_question_options_updated", "question_options"),
        ("trg_questions_updated", "questions"),
        ("trg_questionnaire_versions_updated", "questionnaire_versions"),
        ("trg_questionnaire_templates_updated", "questionnaire_templates"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON vt.{table}")
    for table in TABLES:
        for command in ("select", "insert", "update"):
            op.execute(f"DROP POLICY {table}_{command} ON vt.{table}")
        op.execute(f"REVOKE ALL ON vt.{table} FROM vt_app")
        op.execute(f"ALTER TABLE vt.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE vt.{table} DISABLE ROW LEVEL SECURITY")
    for function in (
        "guard_response_write",
        "guard_assessment_update",
        "guard_assessment_insert",
        "guard_option_write",
        "guard_question_write",
        "guard_version_update",
    ):
        op.execute(f"DROP FUNCTION vt.{function}()")
