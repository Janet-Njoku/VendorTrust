"""Assessments: workflow, scoring accuracy, authorization, tenant isolation, audit, and the
historical integrity of questionnaire versions."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.db.api_support import answer_all, auth, build_questionnaire, su_rows
from tests.db.rls_world import World

pytestmark = pytest.mark.db


def start(api: TestClient, headers: dict[str, str], vendor: str, built: dict[str, Any]) -> str:
    response = api.post(
        f"/vendors/{vendor}/assessments", json={"version_id": built["version_id"]}, headers=headers
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# --- workflow ---------------------------------------------------------------------------------------------


def test_the_full_workflow_and_an_exact_score(api: TestClient, world: World) -> None:
    headers = auth(world.a_analyst)
    built = build_questionnaire(api, headers)  # weights 5, 3, 2
    created = api.post(
        f"/vendors/{world.vendor_a1}/assessments",
        json={"questionnaire_id": built["template_id"]},
        headers=headers,
    )
    assert created.status_code == 201
    assessment = created.json()
    assert assessment["status"] == "in_progress"
    assert assessment["version_id"] == built["version_id"]
    assert assessment["org_id"] == world.org_a
    assert assessment["created_by"] == world.a_analyst
    assert (assessment["score"], assessment["risk_level"]) == (None, None)
    aid = assessment["id"]
    detail = api.get(f"/assessments/{aid}", headers=headers).json()
    assert detail["progress"] == {"answered": 0, "total": 3}
    assert [q["response"] for q in detail["questions"]] == [None, None, None]
    answer_all(api, headers, aid, built, ["Yes", "Partial", "No"])
    assert api.get(f"/assessments/{aid}", headers=headers).json()["progress"] == {
        "answered": 3,
        "total": 3,
    }
    scored = api.post(f"/assessments/{aid}/score", headers=headers)
    assert scored.status_code == 200
    result = scored.json()
    # (5 x 0 + 3 x 50 + 2 x 100) / 10 = 35.0
    assert (result["score"], result["risk_level"]) == (35.0, "medium")
    assert result["breakdown"]["method"] == "weighted_average_risk_v1"
    assert result["breakdown"]["applicable_weight"] == 10
    assert result["breakdown"]["categories"]["mfa"]["score"] == pytest.approx(
        28.6, abs=0.05
    )  # (5x0 + 2x100) / 7
    assert result["breakdown"]["categories"]["encryption"]["score"] == 50.0
    fetched = api.get(f"/assessments/{aid}/result", headers=headers).json()
    assert fetched["score"] == 35.0 and fetched["breakdown"] == result["breakdown"]
    final = api.get(f"/assessments/{aid}", headers=headers).json()
    assert (final["status"], final["score"], final["risk_level"]) == ("scored", 35.0, "medium")


@pytest.mark.parametrize(
    ("labels", "score", "level"),
    [
        (["Yes", "Yes", "Yes"], 0.0, "low"),
        (["No", "No", "No"], 100.0, "critical"),
        (["N/A", "Partial", "No"], 70.0, "high"),  # (3x50 + 2x100) / 5
        (["Yes", "N/A", "Partial"], 14.3, "low"),  # (5x0 + 2x50) / 7
        (["Partial", "Partial", "Partial"], 50.0, "high"),
    ],
)
def test_scores_and_classification(
    api: TestClient, world: World, labels: list[str], score: float, level: str
) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    aid = start(api, headers, world.vendor_a1, built)
    answer_all(api, headers, aid, built, labels)
    result = api.post(f"/assessments/{aid}/score", headers=headers).json()
    assert (result["score"], result["risk_level"]) == (score, level)


def test_all_not_applicable_cannot_be_scored(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    aid = start(api, headers, world.vendor_a1, built)
    answer_all(api, headers, aid, built, ["N/A", "N/A", "N/A"])
    refused = api.post(f"/assessments/{aid}/score", headers=headers)
    assert refused.status_code == 409
    assert api.get(f"/assessments/{aid}", headers=headers).json()["status"] == "in_progress"


def test_scoring_needs_every_question_answered(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    aid = start(api, headers, world.vendor_a1, built)
    answer_all(api, headers, aid, {**built, "questions": built["questions"][:2]}, ["Yes", "Yes"])
    refused = api.post(f"/assessments/{aid}/score", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["unanswered_question_ids"] == [built["questions"][2]["id"]]
    assert api.get(f"/assessments/{aid}", headers=headers).json()["status"] == "in_progress"


def test_responses_can_be_changed_until_scoring_and_never_after(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_analyst)
    built = build_questionnaire(api, headers)
    aid = start(api, headers, world.vendor_a1, built)
    q = built["questions"][0]
    url = f"/assessments/{aid}/responses/{q['id']}"
    first = api.put(
        url, json={"option_id": q["options"]["No"], "comment": "No MFA yet"}, headers=headers
    )
    assert first.status_code == 200
    assert (first.json()["comment"], first.json()["answered_by"]) == ("No MFA yet", world.a_analyst)
    second = api.put(url, json={"option_id": q["options"]["Yes"]}, headers=auth(world.a_admin))
    assert second.json()["id"] == first.json()["id"]  # same response, updated in place
    assert (second.json()["option_id"], second.json()["comment"], second.json()["answered_by"]) == (
        q["options"]["Yes"],
        None,
        world.a_admin,
    )
    answer_all(api, headers, aid, built, ["Yes", "Yes", "Yes"])
    assert api.post(f"/assessments/{aid}/score", headers=headers).status_code == 200
    final = api.put(url, json={"option_id": q["options"]["No"]}, headers=headers)
    assert final.status_code == 409
    assert api.post(f"/assessments/{aid}/score", headers=headers).status_code == 409
    assert su_rows(
        world,
        "SELECT o.label FROM vt.assessment_responses r JOIN vt.question_options o ON o.id = r.option_id WHERE r.id = %s",
        first.json()["id"],
    ) == [("Yes",)]


def test_list_and_filter_assessments(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    one = start(api, headers, world.vendor_a1, built)
    two = start(api, headers, world.vendor_a2, built)
    answer_all(api, headers, two, built, ["Yes", "Yes", "Yes"])
    api.post(f"/assessments/{two}/score", headers=headers)
    everything = {
        a["id"] for a in api.get("/assessments?limit=100", headers=headers).json()["items"]
    }
    assert {one, two} <= everything
    only_a1 = api.get(f"/assessments?vendor_id={world.vendor_a1}", headers=headers).json()["items"]
    assert {a["vendor_id"] for a in only_a1} == {world.vendor_a1}
    scored = api.get("/assessments?status=scored&limit=100", headers=headers).json()["items"]
    assert two in {a["id"] for a in scored} and one not in {a["id"] for a in scored}
    assert api.get("/assessments?limit=0", headers=headers).status_code == 422
    assert api.get("/assessments?status=bogus", headers=headers).status_code == 422


def test_result_before_scoring_is_409_and_unknown_is_404(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    aid = start(api, headers, world.vendor_a1, built)
    assert api.get(f"/assessments/{aid}/result", headers=headers).status_code == 409
    assert api.get(f"/assessments/{uuid.uuid4()}/result", headers=headers).status_code == 404
    assert api.get(f"/assessments/{uuid.uuid4()}", headers=headers).status_code == 404
    assert api.get("/assessments/not-a-uuid", headers=headers).status_code == 422


# --- starting an assessment ---------------------------------------------------------------------------------


def test_only_a_published_version_of_an_active_questionnaire_for_an_active_vendor(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_admin)
    draft = build_questionnaire(api, headers, publish=False)
    refused = api.post(
        f"/vendors/{world.vendor_a1}/assessments",
        json={"version_id": draft["version_id"]},
        headers=headers,
    )
    assert refused.status_code == 409
    assert (
        api.post(
            f"/vendors/{world.vendor_a1}/assessments",
            json={"questionnaire_id": draft["template_id"]},
            headers=headers,
        ).status_code
        == 409
    )
    live = build_questionnaire(api, headers)
    api.post(f"/questionnaire-versions/{live['version_id']}/retire", headers=headers)
    assert (
        api.post(
            f"/vendors/{world.vendor_a1}/assessments",
            json={"version_id": live["version_id"]},
            headers=headers,
        ).status_code
        == 409
    )
    fresh = build_questionnaire(api, headers)
    api.patch(
        f"/questionnaires/{fresh['template_id']}", json={"status": "archived"}, headers=headers
    )
    assert (
        api.post(
            f"/vendors/{world.vendor_a1}/assessments",
            json={"version_id": fresh["version_id"]},
            headers=headers,
        ).status_code
        == 409
    )
    usable = build_questionnaire(api, headers)
    api.patch(f"/vendors/{world.vendor_a2}", json={"status": "archived"}, headers=headers)
    assert (
        api.post(
            f"/vendors/{world.vendor_a2}/assessments",
            json={"version_id": usable["version_id"]},
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        api.post(
            f"/vendors/{world.vendor_a1}/assessments",
            json={"version_id": usable["version_id"]},
            headers=headers,
        ).status_code
        == 201
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"version_id": str(uuid.uuid4()), "questionnaire_id": str(uuid.uuid4())},
        {"version_id": "nope"},
        {"org_id": "x", "version_id": str(uuid.uuid4())},
    ],
)
def test_invalid_assessment_requests_are_422(
    api: TestClient, world: World, body: dict[str, Any]
) -> None:
    assert (
        api.post(
            f"/vendors/{world.vendor_a1}/assessments", json=body, headers=auth(world.a_admin)
        ).status_code
        == 422
    )


def test_unknown_vendor_or_version_is_404(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    assert (
        api.post(
            f"/vendors/{uuid.uuid4()}/assessments",
            json={"version_id": built["version_id"]},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/vendors/{world.vendor_a1}/assessments",
            json={"version_id": str(uuid.uuid4())},
            headers=headers,
        ).status_code
        == 404
    )
    unknown = api.post(
        f"/vendors/{world.vendor_a1}/assessments",
        json={"questionnaire_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert unknown.status_code == 409


def test_responses_must_belong_to_the_assessments_own_questions_and_options(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_admin)
    one = build_questionnaire(api, headers)
    other = build_questionnaire(api, headers)
    aid = start(api, headers, world.vendor_a1, one)
    q1, q2 = one["questions"][0], one["questions"][1]
    url = f"/assessments/{aid}/responses/{q1['id']}"
    assert (
        api.put(url, json={"option_id": q2["options"]["Yes"]}, headers=headers).status_code == 422
    )  # another question's option
    assert (
        api.put(
            url, json={"option_id": other["questions"][0]["options"]["Yes"]}, headers=headers
        ).status_code
        == 422
    )  # another version
    assert api.put(url, json={"option_id": str(uuid.uuid4())}, headers=headers).status_code == 422
    foreign_question = other["questions"][0]
    assert (
        api.put(
            f"/assessments/{aid}/responses/{foreign_question['id']}",
            json={"option_id": foreign_question["options"]["Yes"]},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        api.put(
            f"/assessments/{aid}/responses/{uuid.uuid4()}",
            json={"option_id": q1["options"]["Yes"]},
            headers=headers,
        ).status_code
        == 422
    )
    for bad in (
        {},
        {"option_id": "x"},
        {"option_id": q1["options"]["Yes"], "comment": "x" * 2001},
        {"option_id": q1["options"]["Yes"], "score": 0},
    ):
        assert api.put(url, json=bad, headers=headers).status_code == 422
    assert su_rows(
        world, "SELECT count(*) FROM vt.assessment_responses WHERE assessment_id = %s", aid
    ) == [(0,)]


# --- permissions and isolation ----------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["a_reviewer", "a_auditor"])
def test_read_only_roles_can_read_assessments_but_not_change_them(
    api: TestClient, world: World, who: str
) -> None:
    admin = auth(world.a_admin)
    built = build_questionnaire(api, admin)
    aid = start(api, admin, world.vendor_a1, built)
    answer_all(api, admin, aid, built, ["Yes", "Yes", "Yes"])
    api.post(f"/assessments/{aid}/score", headers=admin)
    headers = auth(getattr(world, who))
    assert api.get(f"/assessments/{aid}", headers=headers).status_code == 200
    assert api.get(f"/assessments/{aid}/result", headers=headers).status_code == 200
    assert api.get("/assessments", headers=headers).status_code == 200
    other_built = build_questionnaire(api, admin)
    open_one = start(api, admin, world.vendor_a1, other_built)
    q = other_built["questions"][0]
    writes = [
        api.post(
            f"/vendors/{world.vendor_a1}/assessments",
            json={"version_id": other_built["version_id"]},
            headers=headers,
        ),
        api.put(
            f"/assessments/{open_one}/responses/{q['id']}",
            json={"option_id": q["options"]["Yes"]},
            headers=headers,
        ),
        api.post(f"/assessments/{open_one}/score", headers=headers),
    ]
    assert [w.status_code for w in writes] == [403, 403, 403]
    assert api.get(f"/assessments/{open_one}", headers=headers).json()["progress"]["answered"] == 0


def test_vendor_users_cannot_see_or_start_assessments(api: TestClient, world: World) -> None:
    admin = auth(world.a_admin)
    built = build_questionnaire(api, admin)
    aid = start(api, admin, world.vendor_a1, built)
    headers = auth(world.a_vendor)  # bound to vendor_a1, the vendor being assessed
    assert api.get("/assessments", headers=headers).status_code == 403
    assert api.get(f"/assessments/{aid}", headers=headers).status_code == 403
    assert api.get(f"/assessments/{aid}/result", headers=headers).status_code == 403
    assert (
        api.post(
            f"/vendors/{world.vendor_a1}/assessments",
            json={"version_id": built["version_id"]},
            headers=headers,
        ).status_code
        == 403
    )
    q = built["questions"][0]
    assert (
        api.put(
            f"/assessments/{aid}/responses/{q['id']}",
            json={"option_id": q["options"]["Yes"]},
            headers=headers,
        ).status_code
        == 403
    )
    assert api.post(f"/assessments/{aid}/score", headers=headers).status_code == 403


def test_another_organization_cannot_reach_an_assessment_in_any_way(
    api: TestClient, world: World
) -> None:
    admin = auth(world.a_admin)
    built = build_questionnaire(api, admin)
    aid = start(api, admin, world.vendor_a1, built)
    answer_all(api, admin, aid, built, ["Yes", "Yes", "Yes"])
    api.post(f"/assessments/{aid}/score", headers=admin)
    open_id = start(api, admin, world.vendor_a1, built)
    q = built["questions"][0]
    other = auth(world.b_admin)
    results = [
        api.get(f"/assessments/{aid}", headers=other),
        api.get(f"/assessments/{aid}/result", headers=other),
        api.put(
            f"/assessments/{open_id}/responses/{q['id']}",
            json={"option_id": q["options"]["No"]},
            headers=other,
        ),
        api.post(f"/assessments/{open_id}/score", headers=other),
    ]
    assert [r.status_code for r in results] == [404] * 4
    assert aid not in {
        a["id"] for a in api.get("/assessments?limit=100", headers=other).json()["items"]
    }
    assert api.get(f"/assessments?vendor_id={world.vendor_a1}", headers=other).json()["items"] == []
    # org B cannot assess org A's vendor, nor with org A's questionnaire
    b_built = build_questionnaire(api, other)
    assert (
        api.post(
            f"/vendors/{world.vendor_a1}/assessments",
            json={"version_id": b_built["version_id"]},
            headers=other,
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/vendors/{world.vendor_b1}/assessments",
            json={"version_id": built["version_id"]},
            headers=other,
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/vendors/{world.vendor_b1}/assessments",
            json={"questionnaire_id": built["template_id"]},
            headers=other,
        ).status_code
        == 409
    )
    assert su_rows(
        world, "SELECT count(*) FROM vt.assessment_responses WHERE assessment_id = %s", open_id
    ) == [(0,)]


def test_row_level_security_backstops_the_assessment_role_checks(
    api: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = auth(world.a_admin)
    built = build_questionnaire(api, admin)
    aid = start(api, admin, world.vendor_a1, built)
    q = built["questions"][0]
    monkeypatch.setattr("app.routers.assessments.require_writer", lambda _context: None)
    for who in ("a_reviewer", "a_auditor", "a_vendor"):
        headers = auth(getattr(world, who))
        statuses = [
            api.post(
                f"/vendors/{world.vendor_a1}/assessments",
                json={"version_id": built["version_id"]},
                headers=headers,
            ).status_code,
            api.put(
                f"/assessments/{aid}/responses/{q['id']}",
                json={"option_id": q["options"]["Yes"]},
                headers=headers,
            ).status_code,
            api.post(f"/assessments/{aid}/score", headers=headers).status_code,
        ]
        assert all(s in (403, 404, 409) for s in statuses), (who, statuses)
        assert 201 not in statuses and 200 not in statuses
    assert su_rows(
        world, "SELECT count(*) FROM vt.assessment_responses WHERE assessment_id = %s", aid
    ) == [(0,)]
    assert su_rows(world, "SELECT status FROM vt.assessments WHERE id = %s", aid) == [
        ("in_progress",)
    ]
    assert su_rows(
        world, "SELECT count(*) FROM vt.assessments WHERE vendor_id = %s", world.vendor_a1
    ) == [(1,)]


def test_a_suspended_user_can_no_longer_work_on_assessments(api: TestClient, world: World) -> None:
    built = build_questionnaire(api, auth(world.a_admin))
    aid = start(api, auth(world.a_analyst), world.vendor_a1, built)
    su_rows(
        world,
        "UPDATE vt.memberships SET status = 'suspended' WHERE id = %s RETURNING 1",
        world.mem[world.a_analyst],
    )
    assert api.get(f"/assessments/{aid}", headers=auth(world.a_analyst)).status_code == 403


# --- audit -----------------------------------------------------------------------------------------------------------------


def test_creation_response_changes_and_the_scoring_decision_are_audited(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_analyst)
    built = build_questionnaire(api, headers)
    aid = start(api, headers, world.vendor_a1, built)
    q = built["questions"][0]
    url = f"/assessments/{aid}/responses/{q['id']}"
    api.put(url, json={"option_id": q["options"]["No"], "comment": "first"}, headers=headers)
    api.put(url, json={"option_id": q["options"]["Yes"], "comment": "second"}, headers=headers)
    answer_all(api, headers, aid, built, ["Yes", "Yes", "No"])
    api.post(f"/assessments/{aid}/score", headers=headers)
    created = su_rows(
        world,
        "SELECT action, actor_user_id::text, actor_role, org_id::text, after_data->>'vendor_id', after_data->>'version_id' FROM vt.audit_log WHERE entity_id = %s",
        aid,
    )
    assert (
        "assessment.create",
        world.a_analyst,
        "analyst",
        world.org_a,
        world.vendor_a1,
        built["version_id"],
    ) in created
    changes = su_rows(
        world,
        "SELECT action, before_data->>'option_id', after_data->>'option_id', before_data->>'comment', after_data->>'comment' "
        "FROM vt.audit_log WHERE entity_type = 'assessment_response' AND after_data->>'assessment_id' = %s "
        "AND after_data->>'question_id' = %s ORDER BY id",
        aid,
        q["id"],
    )
    assert changes[0] == ("response.create", None, q["options"]["No"], None, "first")
    assert changes[1] == (
        "response.update",
        q["options"]["No"],
        q["options"]["Yes"],
        "first",
        "second",
    )
    assert [c[0] for c in changes[2:]] == ["response.update"]
    ((action, before, after),) = su_rows(
        world,
        "SELECT action, before_data->>'status', after_data::text FROM vt.audit_log "
        "WHERE entity_id = %s AND action = 'assessment.score'",
        aid,
    )
    assert (action, before) == ("assessment.score", "in_progress")
    import json

    decision = json.loads(after)
    assert decision["score"] == 20.0  # (5x0 + 3x0 + 2x100) / 10
    assert decision["risk_level"] == "low"
    assert decision["thresholds"] == {"low_max": 25, "medium_max": 50, "high_max": 75}
    assert decision["version_id"] == built["version_id"]
    assert decision["method"] == "weighted_average_risk_v1"


def test_failed_assessment_requests_write_no_audit_or_data(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    aid = start(api, headers, world.vendor_a1, built)
    before = su_rows(world, "SELECT count(*) FROM vt.audit_log WHERE org_id = %s", world.org_a)
    q = built["questions"][0]
    api.put(
        f"/assessments/{aid}/responses/{q['id']}",
        json={"option_id": str(uuid.uuid4())},
        headers=headers,
    )
    api.post(f"/assessments/{aid}/score", headers=headers)  # incomplete
    assert (
        su_rows(world, "SELECT count(*) FROM vt.audit_log WHERE org_id = %s", world.org_a) == before
    )


# --- historical integrity ------------------------------------------------------------------------------------------------------------


def test_editing_a_questionnaire_never_changes_an_assessment_already_started(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)  # v1: weights 5, 3, 2; thresholds 25 / 50 / 75
    old = start(api, headers, world.vendor_a1, built)
    answer_all(api, headers, old, built, ["Yes", "No", "No"])
    # create v2 with different weights and much laxer thresholds, publish it, retire v1
    v2 = api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers).json()
    q_ids = [q["id"] for q in v2["questions"]]
    for question_id, weight in zip(q_ids, (1, 3, 2), strict=True):
        api.patch(f"/questions/{question_id}", json={"weight": weight}, headers=headers)
    api.patch(
        f"/questionnaire-versions/{v2['id']}",
        json={"low_max": 90, "medium_max": 95, "high_max": 99},
        headers=headers,
    )
    api.post(f"/questionnaire-versions/{v2['id']}/publish", headers=headers)
    api.post(f"/questionnaire-versions/{built['version_id']}/retire", headers=headers)
    # the in-progress v1 assessment still shows v1's questions and scores with v1's rules
    detail = api.get(f"/assessments/{old}", headers=headers).json()
    assert detail["version_id"] == built["version_id"]
    assert [q["weight"] for q in detail["questions"]] == [5, 3, 2]
    assert [q["id"] for q in detail["questions"]] == [q["id"] for q in built["questions"]]
    result = api.post(f"/assessments/{old}/score", headers=headers).json()
    assert result["score"] == 50.0  # (5x0 + 3x100 + 2x100) / 10
    assert result["risk_level"] == "high"  # v1 thresholds: 50 is not below 50
    assert result["breakdown"]["thresholds"] == {"low_max": 25, "medium_max": 50, "high_max": 75}
    # a new assessment uses v2 (the latest published), with v2's weights and thresholds
    fresh = api.post(
        f"/vendors/{world.vendor_a2}/assessments",
        json={"questionnaire_id": built["template_id"]},
        headers=headers,
    ).json()
    assert fresh["version_id"] == v2["id"]
    new_detail = api.get(f"/assessments/{fresh['id']}", headers=headers).json()
    v2_questions = {q["id"]: q for q in new_detail["questions"]}
    for question_id, label in zip(q_ids, ("Yes", "No", "No"), strict=True):
        option = v2_questions[question_id]["options"]
        chosen = next(o for o in option if o["label"] == label)
        api.put(
            f"/assessments/{fresh['id']}/responses/{question_id}",
            json={"option_id": chosen["id"]},
            headers=headers,
        )
    second = api.post(f"/assessments/{fresh['id']}/score", headers=headers).json()
    assert second["score"] == 83.3  # (1x0 + 3x100 + 2x100) / 6
    assert second["risk_level"] == "low"  # v2 thresholds 90 / 95 / 99
    # and the stored v1 result is unchanged
    assert api.get(f"/assessments/{old}/result", headers=headers).json()["score"] == 50.0


def test_a_scored_assessment_stays_exactly_as_it_was(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    aid = start(api, headers, world.vendor_a1, built)
    answer_all(api, headers, aid, built, ["Partial", "Partial", "Partial"])
    first = api.post(f"/assessments/{aid}/score", headers=headers).json()
    draft = api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers).json()
    api.patch(f"/questions/{draft['questions'][0]['id']}", json={"weight": 10}, headers=headers)
    api.post(f"/questionnaire-versions/{draft['id']}/publish", headers=headers)
    api.patch(
        f"/questionnaires/{built['template_id']}", json={"status": "archived"}, headers=headers
    )
    again = api.get(f"/assessments/{aid}/result", headers=headers).json()
    assert (again["score"], again["risk_level"], again["breakdown"]) == (
        first["score"],
        first["risk_level"],
        first["breakdown"],
    )
    assert api.get(f"/assessments/{aid}", headers=headers).status_code == 200


def test_archived_questions_are_not_part_of_an_assessment(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers, publish=False)  # weights 5, 3, 2
    api.patch(
        f"/questions/{built['questions'][0]['id']}", json={"status": "archived"}, headers=headers
    )
    api.post(f"/questionnaire-versions/{built['version_id']}/publish", headers=headers)
    aid = start(api, headers, world.vendor_a1, built)
    detail = api.get(f"/assessments/{aid}", headers=headers).json()
    assert detail["progress"] == {"answered": 0, "total": 2}
    assert [q["weight"] for q in detail["questions"]] == [3, 2]
    remaining = {**built, "questions": built["questions"][1:]}
    answer_all(api, headers, aid, remaining, ["Partial", "No"])
    result = api.post(f"/assessments/{aid}/score", headers=headers).json()
    assert result["score"] == 70.0  # (3 x 50 + 2 x 100) / 5, the archived question is ignored
    assert len(result["breakdown"]["items"]) == 2
