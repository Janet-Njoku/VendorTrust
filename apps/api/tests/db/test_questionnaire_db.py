"""The database enforces questionnaire integrity and tenant isolation by itself, without the API:
triggers, composite foreign keys, row level security and grants, driven directly as vt_app."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from psycopg import errors

from tests.db.conftest import SUPERUSER_ROLE, connect
from tests.db.rls_world import World, app, begin, count, forge

pytestmark = pytest.mark.db

NEW_TABLES = (
    "questionnaire_templates",
    "questionnaire_versions",
    "questions",
    "question_options",
    "assessments",
    "assessment_responses",
)


def seed_questionnaire(world: World, org: str, *, publish: bool = True) -> dict[str, Any]:
    """Two questions with three options each (superuser, so it bypasses RLS)."""
    with connect(SUPERUSER_ROLE, world.db) as su:
        template = su.execute(
            "INSERT INTO vt.questionnaire_templates (org_id, name) VALUES (%s, 'seed') RETURNING id",
            (org,),
        ).fetchone()
        assert template is not None
        version = su.execute(
            "INSERT INTO vt.questionnaire_versions (org_id, template_id, version_number) "
            "VALUES (%s, %s, 1) RETURNING id",
            (org, template[0]),
        ).fetchone()
        assert version is not None
        questions = []
        for position in (1, 2):
            question = su.execute(
                "INSERT INTO vt.questions (org_id, version_id, position, category, prompt, weight) "
                "VALUES (%s, %s, %s, 'mfa', 'Is this seeded question answered?', %s) RETURNING id",
                (org, version[0], position, position * 2),
            ).fetchone()
            assert question is not None
            options = {}
            for index, (label, points) in enumerate(
                (("Yes", 0), ("Partial", 50), ("No", 100)), start=1
            ):
                row = su.execute(
                    "INSERT INTO vt.question_options (org_id, question_id, position, label, risk_points) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (org, question[0], index, label, points),
                ).fetchone()
                assert row is not None
                options[label] = str(row[0])
            questions.append({"id": str(question[0]), "options": options})
        if publish:
            su.execute(
                "UPDATE vt.questionnaire_versions SET status = 'published' WHERE id = %s",
                (version[0],),
            )
    return {"template": str(template[0]), "version": str(version[0]), "questions": questions}


def new_assessment(
    conn: Any, world: World, seeded: dict[str, Any], vendor: str | None = None
) -> str:
    row = conn.execute(
        "INSERT INTO vt.assessments (org_id, vendor_id, version_id, created_by) "
        "VALUES (%s, %s, %s, current_setting('vt.claim_user_id')::uuid) RETURNING id",
        (world.org_a, vendor or world.vendor_a1, seeded["version"]),
    ).fetchone()
    assert row is not None
    return str(row[0])


def answer(conn: Any, assessment: str, question: dict[str, Any], label: str) -> None:
    conn.execute(
        "INSERT INTO vt.assessment_responses (org_id, assessment_id, question_id, option_id, answered_by) "
        "VALUES (current_setting('vt.claim_org_id')::uuid, %s, %s, %s, current_setting('vt.claim_user_id')::uuid)",
        (assessment, question["id"], question["options"][label]),
    )


def sqlstate_of(conn: Any, sql: str, *params: object) -> str | None:
    try:
        conn.execute(sql, params)
    except psycopg.Error as exc:
        conn.rollback()
        return exc.sqlstate
    return None


# --- published versions are immutable ------------------------------------------------------------


def test_a_published_version_rejects_every_change_at_the_database(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a)
    question = seeded["questions"][0]
    statements = [
        ("UPDATE vt.questions SET weight = 1 WHERE id = %s", (question["id"],)),
        ("UPDATE vt.questions SET status = 'archived' WHERE id = %s", (question["id"],)),
        (
            "INSERT INTO vt.questions (org_id, version_id, position, category, prompt, weight) "
            "VALUES (%s, %s, 9, 'mfa', 'A brand new question?', 1)",
            (world.org_a, seeded["version"]),
        ),
        (
            "UPDATE vt.question_options SET risk_points = 100 WHERE id = %s",
            (question["options"]["Yes"],),
        ),
        (
            "INSERT INTO vt.question_options (org_id, question_id, position, label, risk_points) "
            "VALUES (%s, %s, 9, 'Sneaky', 0)",
            (world.org_a, question["id"]),
        ),
        (
            "UPDATE vt.questionnaire_versions SET low_max = 5, medium_max = 10, high_max = 20 WHERE id = %s",
            (seeded["version"],),
        ),
    ]
    with app(world.db) as conn:
        for sql, params in statements:
            begin(conn, world.a_admin)
            assert sqlstate_of(conn, sql, *params) == "VT011", sql
    with connect(SUPERUSER_ROLE, world.db) as su:
        assert su.execute(
            "SELECT weight, status FROM vt.questions WHERE id = %s", (question["id"],)
        ).fetchone() == (2, "active")


def test_version_status_transitions_are_one_way(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a)
    draft = seed_questionnaire(world, world.org_a, publish=False)
    with app(world.db) as conn:
        begin(conn, world.a_admin)
        assert (
            sqlstate_of(
                conn,
                "UPDATE vt.questionnaire_versions SET status = 'draft' WHERE id = %s",
                seeded["version"],
            )
            == "VT015"
        )
        begin(conn, world.a_admin)
        conn.execute(
            "UPDATE vt.questionnaire_versions SET status = 'retired' WHERE id = %s",
            (seeded["version"],),
        )
        conn.commit()
        begin(conn, world.a_admin)
        assert (
            sqlstate_of(
                conn,
                "UPDATE vt.questionnaire_versions SET status = 'published' WHERE id = %s",
                seeded["version"],
            )
            == "VT015"
        )
        begin(conn, world.a_admin)
        assert sqlstate_of(
            conn,
            "UPDATE vt.questionnaire_versions SET status = 'bogus' WHERE id = %s",
            draft["version"],
        ) in {"23514", "VT015"}
        begin(conn, world.a_admin)
        conn.execute(
            "UPDATE vt.questionnaire_versions SET status = 'published' WHERE id = %s",
            (draft["version"],),
        )
        row = conn.execute(
            "SELECT published_at IS NOT NULL FROM vt.questionnaire_versions WHERE id = %s",
            (draft["version"],),
        ).fetchone()
        assert row == (True,)


def test_publishing_requires_questions_with_two_active_options(world: World) -> None:
    with connect(SUPERUSER_ROLE, world.db) as su:
        template = su.execute(
            "INSERT INTO vt.questionnaire_templates (org_id, name) VALUES (%s, 'x') RETURNING id",
            (world.org_a,),
        ).fetchone()
        assert template is not None
        version = su.execute(
            "INSERT INTO vt.questionnaire_versions (org_id, template_id, version_number) VALUES (%s, %s, 1) RETURNING id",
            (world.org_a, template[0]),
        ).fetchone()
        assert version is not None
        assert (
            sqlstate_of(
                su,
                "UPDATE vt.questionnaire_versions SET status = 'published' WHERE id = %s",
                version[0],
            )
            == "VT010"
        )
        question = su.execute(
            "INSERT INTO vt.questions (org_id, version_id, position, category, prompt, weight) "
            "VALUES (%s, %s, 1, 'mfa', 'Only one option here?', 1) RETURNING id",
            (world.org_a, version[0]),
        ).fetchone()
        assert question is not None
        su.execute(
            "INSERT INTO vt.question_options (org_id, question_id, position, label, risk_points) VALUES (%s, %s, 1, 'Yes', 0)",
            (world.org_a, question[0]),
        )
        assert (
            sqlstate_of(
                su,
                "UPDATE vt.questionnaire_versions SET status = 'published' WHERE id = %s",
                version[0],
            )
            == "VT010"
        )


def test_only_one_draft_per_template_and_unique_version_numbers(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a, publish=False)
    with connect(SUPERUSER_ROLE, world.db) as su:
        with pytest.raises(errors.UniqueViolation):
            su.execute(
                "INSERT INTO vt.questionnaire_versions (org_id, template_id, version_number) VALUES (%s, %s, 2)",
                (world.org_a, seeded["template"]),
            )
        su.rollback()
        with pytest.raises(errors.UniqueViolation):
            su.execute(
                "INSERT INTO vt.questionnaire_versions (org_id, template_id, version_number, status, published_at) "
                "VALUES (%s, %s, 1, 'retired', now())",
                (world.org_a, seeded["template"]),
            )


# --- assessments ----------------------------------------------------------------------------------


def test_assessments_need_a_published_version_and_an_active_vendor(world: World) -> None:
    published = seed_questionnaire(world, world.org_a)
    draft = seed_questionnaire(world, world.org_a, publish=False)
    with connect(SUPERUSER_ROLE, world.db) as su:
        su.execute("UPDATE vt.vendors SET status = 'archived' WHERE id = %s", (world.vendor_a2,))
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        assert new_assessment(conn, world, published)
        conn.commit()
        for seeded, vendor in ((draft, None), (published, world.vendor_a2)):
            begin(conn, world.a_analyst)
            with pytest.raises(psycopg.Error) as info:
                new_assessment(conn, world, seeded, vendor)
            assert info.value.sqlstate == "VT012"
            conn.rollback()


def test_an_assessment_cannot_cross_organizations(world: World) -> None:
    b_version = seed_questionnaire(world, world.org_b)
    a_version = seed_questionnaire(world, world.org_a)
    with connect(SUPERUSER_ROLE, world.db) as su:
        with pytest.raises(errors.ForeignKeyViolation):  # A's vendor with B's version
            su.execute(
                "INSERT INTO vt.assessments (org_id, vendor_id, version_id, created_by) VALUES (%s, %s, %s, %s)",
                (world.org_a, world.vendor_a1, b_version["version"], world.a_admin),
            )
        su.rollback()
        with pytest.raises(errors.ForeignKeyViolation):  # B's vendor with A's version
            su.execute(
                "INSERT INTO vt.assessments (org_id, vendor_id, version_id, created_by) VALUES (%s, %s, %s, %s)",
                (world.org_a, world.vendor_b1, a_version["version"], world.a_admin),
            )
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        with pytest.raises(psycopg.Error):  # org B's ids through org A's context
            conn.execute(
                "INSERT INTO vt.assessments (org_id, vendor_id, version_id, created_by) VALUES (%s, %s, %s, %s)",
                (world.org_b, world.vendor_b1, b_version["version"], world.a_analyst),
            )


def test_created_by_must_be_the_acting_user(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a)
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO vt.assessments (org_id, vendor_id, version_id, created_by) VALUES (%s, %s, %s, %s)",
                (world.org_a, world.vendor_a1, seeded["version"], world.a_admin),
            )


def test_responses_are_checked_against_the_assessments_own_version(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a)
    other = seed_questionnaire(world, world.org_a)
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        assessment = new_assessment(conn, world, seeded)
        q1, q2 = seeded["questions"]
        answer(conn, assessment, q1, "Yes")
        conn.commit()
        begin(conn, world.a_analyst)
        with pytest.raises(psycopg.Error) as foreign:  # a question of another version
            answer(conn, assessment, other["questions"][0], "Yes")
        assert foreign.value.sqlstate == "VT014"
        conn.rollback()
        begin(conn, world.a_analyst)
        with pytest.raises(
            errors.ForeignKeyViolation
        ):  # an option that belongs to a different question
            conn.execute(
                "INSERT INTO vt.assessment_responses (org_id, assessment_id, question_id, option_id, answered_by) "
                "VALUES (%s, %s, %s, %s, %s)",
                (world.org_a, assessment, q2["id"], q1["options"]["Yes"], world.a_analyst),
            )
        conn.rollback()
        begin(conn, world.a_analyst)
        with pytest.raises(errors.UniqueViolation):  # one response per question
            answer(conn, assessment, q1, "No")


def test_scoring_requires_every_question_and_the_result_is_final(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a)
    q1, q2 = seeded["questions"]
    score = (
        "UPDATE vt.assessments SET status = 'scored', score = 10, risk_level = 'low', scored_at = now(), "
        "scored_by = current_setting('vt.claim_user_id')::uuid, breakdown = '{}' WHERE id = %s"
    )
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        assessment = new_assessment(conn, world, seeded)
        answer(conn, assessment, q1, "Yes")
        conn.commit()
        begin(conn, world.a_analyst)
        assert sqlstate_of(conn, score, assessment) == "VT016"  # q2 unanswered
        begin(conn, world.a_analyst)
        answer(conn, assessment, q2, "No")
        conn.commit()
        begin(conn, world.a_analyst)
        # complete now, but a scored assessment must carry a result (CHECK constraint)
        no_result = "UPDATE vt.assessments SET status = 'scored' WHERE id = %s"
        assert sqlstate_of(conn, no_result, assessment) == "23514"
        begin(conn, world.a_analyst)
        conn.execute(score, (assessment,))
        conn.commit()
        begin(conn, world.a_analyst)
        assert sqlstate_of(conn, score, assessment) == "VT013"  # already scored
        begin(conn, world.a_analyst)
        assert (
            sqlstate_of(conn, "UPDATE vt.assessments SET score = 99 WHERE id = %s", assessment)
            == "VT013"
        )
        begin(conn, world.a_analyst)
        assert (
            sqlstate_of(
                conn,
                "UPDATE vt.assessment_responses SET option_id = %s WHERE assessment_id = %s AND question_id = %s",
                q1["options"]["No"],
                assessment,
                q1["id"],
            )
            == "VT013"
        )
        begin(conn, world.a_analyst)
        assert sqlstate_of(
            conn,
            "INSERT INTO vt.assessment_responses (org_id, assessment_id, question_id, option_id, answered_by) VALUES (%s, %s, %s, %s, %s)",
            world.org_a,
            assessment,
            q1["id"],
            q1["options"]["No"],
            world.a_analyst,
        ) in {"VT013", "23505"}


def test_an_archived_option_cannot_be_chosen(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a, publish=False)
    q1 = seeded["questions"][0]
    with app(world.db) as conn:
        begin(conn, world.a_admin)
        conn.execute(
            "UPDATE vt.question_options SET status = 'archived' WHERE id = %s",
            (q1["options"]["Partial"],),
        )
        conn.execute(
            "UPDATE vt.questionnaire_versions SET status = 'published' WHERE id = %s",
            (seeded["version"],),
        )
        conn.commit()
        begin(conn, world.a_analyst)
        assessment = new_assessment(conn, world, seeded)
        with pytest.raises(psycopg.Error) as info:
            answer(conn, assessment, q1, "Partial")
        assert info.value.sqlstate == "VT014"


# --- row level security --------------------------------------------------------------------------------


def test_rls_scopes_every_new_table_to_the_organization_and_role(world: World) -> None:
    a = seed_questionnaire(world, world.org_a)
    seed_questionnaire(world, world.org_b)
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        assessment = new_assessment(conn, world, a)
        answer(conn, assessment, a["questions"][0], "Yes")
        conn.commit()
    expected_orgs = {world.org_a}
    for user in ("a_analyst", "a_reviewer", "a_auditor", "a_admin"):
        with app(world.db) as conn:
            begin(conn, getattr(world, user))
            for table in NEW_TABLES:
                assert count(conn, table) >= 1, (user, table)
                seen = {
                    str(r[0])
                    for r in conn.execute(f"SELECT DISTINCT org_id FROM vt.{table}").fetchall()
                }
                assert seen == expected_orgs, (user, table)
    with app(world.db) as conn:  # org B sees its own questionnaire, never org A's
        begin(conn, world.b_admin)
        assert count(conn, "assessments") == 0
        assert {
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT org_id FROM vt.questionnaire_templates"
            ).fetchall()
        } == {world.org_b}
    with app(world.db) as conn:  # a vendor user (live membership) sees none of it
        begin(conn, world.a_vendor)
        for table in NEW_TABLES:
            assert count(conn, table) == 0, table


@pytest.mark.parametrize("role", ["a_reviewer", "a_auditor", "a_vendor"])
def test_only_org_admin_and_analyst_can_write_the_new_tables(world: World, role: str) -> None:
    seeded = seed_questionnaire(world, world.org_a, publish=False)
    statements = [
        ("INSERT INTO vt.questionnaire_templates (org_id, name) VALUES (%s, 'x')", (world.org_a,)),
        (
            "INSERT INTO vt.questionnaire_versions (org_id, template_id, version_number) VALUES (%s, %s, 5)",
            (world.org_a, seeded["template"]),
        ),
        (
            "INSERT INTO vt.questions (org_id, version_id, position, category, prompt, weight) VALUES (%s, %s, 9, 'mfa', 'Sneaky question?', 1)",
            (world.org_a, seeded["version"]),
        ),
        (
            "INSERT INTO vt.question_options (org_id, question_id, position, label, risk_points) VALUES (%s, %s, 9, 'x', 0)",
            (world.org_a, seeded["questions"][0]["id"]),
        ),
    ]
    with app(world.db) as conn:
        for sql, params in statements:
            begin(conn, getattr(world, role))
            # a vendor user cannot even see versions, so the draft-only trigger answers first
            refused = {"42501", "VT011"} if role == "a_vendor" else {"42501"}
            assert sqlstate_of(conn, sql, *params) in refused, sql
        begin(conn, getattr(world, role))
        changed = conn.execute(
            "UPDATE vt.questionnaire_templates SET name = 'pwned' WHERE id = %s",
            (seeded["template"],),
        )
        assert changed.rowcount == 0
        changed = conn.execute(
            "UPDATE vt.questions SET weight = 1 WHERE id = %s", (seeded["questions"][0]["id"],)
        )
        assert changed.rowcount == 0


def test_forged_claims_reach_nothing_in_the_new_tables(world: World) -> None:
    a = seed_questionnaire(world, world.org_a)
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        new_assessment(conn, world, a)
        conn.commit()
    forgeries = [
        (world.a_analyst, world.org_b, "analyst", None),  # another organization
        (world.a_analyst, world.org_a, "org_admin", None),  # a role it does not hold
        (world.a_reviewer, world.org_a, "analyst", None),
        (str(uuid.uuid4()), world.org_a, "org_admin", None),  # an unknown user
        (world.a_vendor, world.org_a, "analyst", None),
        (world.a_vendor, world.org_a, "vendor_user", world.vendor_a2),
    ]
    for user, org, role, vendor in forgeries:
        with app(world.db) as conn:
            forge(conn, user, org, role, vendor)
            for table in NEW_TABLES:
                assert count(conn, table) == 0, (user, org, role, table)


def test_no_delete_or_truncate_on_the_new_tables(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a)
    with app(world.db) as conn:
        for table in NEW_TABLES:
            begin(conn, world.a_admin)
            assert sqlstate_of(conn, f"DELETE FROM vt.{table}") == "42501", table
            begin(conn, world.a_admin)
            assert sqlstate_of(conn, f"TRUNCATE vt.{table}") == "42501", table
    with connect(SUPERUSER_ROLE, world.db) as su:
        assert su.execute(
            "SELECT count(*) FROM vt.questions WHERE version_id = %s", (seeded["version"],)
        ).fetchone() == (2,)


def test_identity_columns_cannot_be_rewritten(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a, publish=False)
    with app(world.db) as conn:
        for sql, params in (
            (
                "UPDATE vt.questions SET version_id = %s WHERE id = %s",
                (str(uuid.uuid4()), seeded["questions"][0]["id"]),
            ),
            (
                "UPDATE vt.questions SET org_id = %s WHERE id = %s",
                (world.org_b, seeded["questions"][0]["id"]),
            ),
            (
                "UPDATE vt.questionnaire_templates SET org_id = %s WHERE id = %s",
                (world.org_b, seeded["template"]),
            ),
            (
                "UPDATE vt.question_options SET question_id = %s WHERE id = %s",
                (seeded["questions"][1]["id"], seeded["questions"][0]["options"]["Yes"]),
            ),
        ):
            begin(conn, world.a_admin)
            assert sqlstate_of(conn, sql, *params) == "42501", sql


def test_a_response_cannot_be_attributed_to_someone_else(world: World) -> None:
    seeded = seed_questionnaire(world, world.org_a)
    q1 = seeded["questions"][0]
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        assessment = new_assessment(conn, world, seeded)
        conn.commit()
        begin(conn, world.a_analyst)
        assert (
            sqlstate_of(
                conn,
                "INSERT INTO vt.assessment_responses (org_id, assessment_id, question_id, option_id, "
                "answered_by) VALUES (%s, %s, %s, %s, %s)",
                world.org_a,
                assessment,
                q1["id"],
                q1["options"]["Yes"],
                world.a_admin,
            )
            == "42501"
        )
        begin(conn, world.a_analyst)
        answer(conn, assessment, q1, "Yes")
        begin(conn, world.a_analyst)
        assert (
            sqlstate_of(
                conn,
                "UPDATE vt.assessment_responses SET answered_by = %s WHERE assessment_id = %s",
                world.a_admin,
                assessment,
            )
            == "42501"
        )
