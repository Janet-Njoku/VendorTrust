"""Questionnaire templates, versions, questions and options: configurable, versioned, isolated."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.db.api_support import STANDARD, auth, build_questionnaire, su_rows
from tests.db.rls_world import World

pytestmark = pytest.mark.db


def question_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "category": "mfa",
        "prompt": "Is multi-factor authentication enforced?",
        "weight": 4,
        "options": [{"label": "Yes", "risk_points": 0}, {"label": "No", "risk_points": 100}],
    }
    body.update(overrides)
    return body


# --- templates ----------------------------------------------------------------------------------


def test_create_list_get_update_and_archive_a_template(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    created = api.post(
        "/questionnaires",
        json={"name": "  Vendor onboarding  ", "description": "Baseline"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Vendor onboarding"
    assert body["org_id"] == world.org_a
    assert [(v["version_number"], v["status"], v["question_count"]) for v in body["versions"]] == [
        (1, "draft", 0)
    ]
    template_id = body["id"]
    assert (
        api.get(f"/questionnaires/{template_id}", headers=headers).json()["name"]
        == "Vendor onboarding"
    )
    renamed = api.patch(
        f"/questionnaires/{template_id}",
        json={"name": "Onboarding v2", "description": None},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert (renamed.json()["name"], renamed.json()["description"]) == ("Onboarding v2", None)
    assert template_id in {t["id"] for t in api.get("/questionnaires", headers=headers).json()}
    archived = api.patch(
        f"/questionnaires/{template_id}", json={"status": "archived"}, headers=headers
    )
    assert archived.json()["status"] == "archived"
    assert template_id not in {t["id"] for t in api.get("/questionnaires", headers=headers).json()}
    everything = api.get("/questionnaires?include_archived=true", headers=headers).json()
    assert template_id in {t["id"] for t in everything}


def test_an_organization_can_have_several_templates(api: TestClient, world: World) -> None:
    headers = auth(world.a_analyst)
    first = api.post("/questionnaires", json={"name": "Same name"}, headers=headers).json()
    second = api.post("/questionnaires", json={"name": "Same name"}, headers=headers).json()
    assert first["id"] != second["id"]
    listed = api.get("/questionnaires?limit=100", headers=headers).json()
    assert {first["id"], second["id"]} <= {t["id"] for t in listed}
    assert {t["org_id"] for t in listed} == {world.org_a}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 201},
        {"name": "a\nb"},
        {"name": "ok", "org_id": str(uuid.uuid4())},
        {"name": "ok", "x": 1},
    ],
)
def test_invalid_template_bodies_are_422(
    api: TestClient, world: World, body: dict[str, Any]
) -> None:
    assert api.post("/questionnaires", json=body, headers=auth(world.a_admin)).status_code == 422


@pytest.mark.parametrize("body", [{}, {"name": None}, {"status": "deleted"}, {"unknown": 1}])
def test_invalid_template_updates_are_422(
    api: TestClient, world: World, body: dict[str, Any]
) -> None:
    built = build_questionnaire(api, auth(world.a_admin), publish=False)
    assert (
        api.patch(
            f"/questionnaires/{built['template_id']}", json=body, headers=auth(world.a_admin)
        ).status_code
        == 422
    )


def test_the_starter_questionnaire_is_copied_as_an_editable_draft(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_analyst)
    created = api.post("/questionnaires/from-starter", headers=headers)
    assert created.status_code == 201
    body = created.json()
    draft = body["versions"][0]
    assert (draft["status"], draft["question_count"]) == ("draft", 15)
    version = api.get(f"/questionnaire-versions/{draft['id']}", headers=headers).json()
    assert {q["category"] for q in version["questions"]} == {
        "access_control",
        "mfa",
        "encryption",
        "vulnerability_management",
        "incident_response",
        "business_continuity",
        "data_protection",
        "vendor_governance",
    }
    # it is ordinary data: re-weight a question and publish
    first = version["questions"][0]
    assert (
        api.patch(f"/questions/{first['id']}", json={"weight": 9}, headers=headers).json()["weight"]
        == 9
    )
    assert (
        api.post(f"/questionnaire-versions/{draft['id']}/publish", headers=headers).status_code
        == 200
    )
    named = api.post("/questionnaires/from-starter", json={"name": "Our baseline"}, headers=headers)
    assert named.json()["name"] == "Our baseline"


# --- questions and options -----------------------------------------------------------------------


def test_questions_get_positions_options_and_can_be_edited_and_archived(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers, publish=False)
    version_id = built["version_id"]
    extra = api.post(
        f"/questionnaire-versions/{version_id}/questions",
        json=question_body(guidance="See policy"),
        headers=headers,
    )
    assert extra.status_code == 201
    created = extra.json()
    assert created["position"] == 4
    assert [o["position"] for o in created["options"]] == [1, 2]
    updated = api.patch(
        f"/questions/{created['id']}",
        json={
            "prompt": "Is MFA enforced for everyone?",
            "weight": 8,
            "guidance": None,
            "category": "access_control",
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert (updated.json()["weight"], updated.json()["guidance"], updated.json()["category"]) == (
        8,
        None,
        "access_control",
    )
    option = api.post(
        f"/questions/{created['id']}/options",
        json={"label": "Partly", "risk_points": 40},
        headers=headers,
    )
    assert option.status_code == 201
    assert option.json()["position"] == 3
    na = api.patch(
        f"/options/{option.json()['id']}",
        json={"label": "Not applicable", "risk_points": None},
        headers=headers,
    )
    assert na.json()["risk_points"] is None
    gone = api.patch(
        f"/options/{option.json()['id']}", json={"status": "archived"}, headers=headers
    )
    assert gone.json()["status"] == "archived"
    archived = api.patch(
        f"/questions/{created['id']}", json={"status": "archived"}, headers=headers
    )
    assert archived.json()["status"] == "archived"
    default_view = api.get(f"/questionnaire-versions/{version_id}", headers=headers).json()
    assert created["id"] not in {q["id"] for q in default_view["questions"]}
    full_view = api.get(
        f"/questionnaire-versions/{version_id}?include_archived=true", headers=headers
    ).json()
    assert created["id"] in {q["id"] for q in full_view["questions"]}


@pytest.mark.parametrize(
    "override",
    [
        {"options": [{"label": "Only", "risk_points": 0}]},
        {"options": []},
        {"options": [{"label": "A", "risk_points": 101}, {"label": "B", "risk_points": 0}]},
        {"options": [{"label": "A", "risk_points": -1}, {"label": "B", "risk_points": 0}]},
        {"options": [{"label": "A"}, {"label": "B", "risk_points": 0}]},
        {"weight": 0},
        {"weight": 11},
        {"category": "Not A Slug"},
        {"category": "x"},
        {"prompt": "Hi"},
        {"prompt": "x" * 1001},
        {"extra": True},
        {"options": [{"label": "", "risk_points": 0}, {"label": "B", "risk_points": 0}]},
    ],
)
def test_invalid_questions_are_422(api: TestClient, world: World, override: dict[str, Any]) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers, publish=False)
    response = api.post(
        f"/questionnaire-versions/{built['version_id']}/questions",
        json=question_body(**override),
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [{}, {"weight": None}, {"prompt": None}, {"status": "gone"}, {"nope": 1}, {"weight": 99}],
)
def test_invalid_question_updates_are_422(
    api: TestClient, world: World, body: dict[str, Any]
) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers, publish=False)
    assert (
        api.patch(
            f"/questions/{built['questions'][0]['id']}", json=body, headers=headers
        ).status_code
        == 422
    )


def test_reordering_questions(api: TestClient, world: World) -> None:
    headers = auth(world.a_analyst)
    built = build_questionnaire(api, headers, publish=False)
    ids = [q["id"] for q in built["questions"]]
    reordered = api.put(
        f"/questionnaire-versions/{built['version_id']}/order",
        json={"question_ids": ids[::-1]},
        headers=headers,
    )
    assert reordered.status_code == 200
    assert [q["id"] for q in reordered.json()["questions"]] == ids[::-1]
    assert [q["position"] for q in reordered.json()["questions"]] == [1, 2, 3]
    for bad in (ids[:2], ids + [str(uuid.uuid4())], ids + ids[:1], []):
        response = api.put(
            f"/questionnaire-versions/{built['version_id']}/order",
            json={"question_ids": bad},
            headers=headers,
        )
        assert response.status_code == 422
    kept = api.get(f"/questionnaire-versions/{built['version_id']}", headers=headers).json()
    assert [q["id"] for q in kept["questions"]] == ids[::-1]


def test_archived_questions_are_not_part_of_the_order(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers, publish=False)
    ids = [q["id"] for q in built["questions"]]
    api.patch(f"/questions/{ids[1]}", json={"status": "archived"}, headers=headers)
    ok = api.put(
        f"/questionnaire-versions/{built['version_id']}/order",
        json={"question_ids": [ids[2], ids[0]]},
        headers=headers,
    )
    assert ok.status_code == 200
    assert [q["id"] for q in ok.json()["questions"]] == [ids[2], ids[0]]


# --- publishing and immutability -------------------------------------------------------------------------


def test_publishing_needs_questions_with_two_active_options(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    empty = api.post("/questionnaires", json={"name": "Empty"}, headers=headers).json()
    refused = api.post(
        f"/questionnaire-versions/{empty['versions'][0]['id']}/publish", headers=headers
    )
    assert refused.status_code == 409
    assert "not ready to publish" in refused.json()["detail"]
    built = build_questionnaire(api, headers, publish=False)
    question = built["questions"][0]
    for label in ("Yes", "Partial", "No"):  # leave a single active option
        api.patch(
            f"/options/{question['options'][label]}", json={"status": "archived"}, headers=headers
        )
    again = api.post(f"/questionnaire-versions/{built['version_id']}/publish", headers=headers)
    assert again.status_code == 409
    assert "VT0" not in again.text and "vt." not in again.text


def test_a_published_version_can_never_change(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    version_id = built["version_id"]
    question = built["questions"][0]
    attempts = [
        api.post(
            f"/questionnaire-versions/{version_id}/questions", json=question_body(), headers=headers
        ),
        api.patch(f"/questions/{question['id']}", json={"weight": 1}, headers=headers),
        api.patch(f"/questions/{question['id']}", json={"status": "archived"}, headers=headers),
        api.post(
            f"/questions/{question['id']}/options",
            json={"label": "New", "risk_points": 10},
            headers=headers,
        ),
        api.patch(
            f"/options/{question['options']['Yes']}", json={"risk_points": 100}, headers=headers
        ),
        api.patch(
            f"/questionnaire-versions/{version_id}",
            json={"low_max": 10, "medium_max": 20, "high_max": 30},
            headers=headers,
        ),
    ]
    assert [a.status_code for a in attempts] == [409] * 6
    assert all("draft" in a.json()["detail"].lower() for a in attempts)
    reorder = api.put(
        f"/questionnaire-versions/{version_id}/order",
        json={"question_ids": [q["id"] for q in built["questions"]][::-1]},
        headers=headers,
    )
    assert reorder.status_code == 409
    assert (
        api.post(f"/questionnaire-versions/{version_id}/publish", headers=headers).status_code
        == 409
    )
    version = api.get(f"/questionnaire-versions/{version_id}", headers=headers).json()
    assert version["status"] == "published"
    assert [q["weight"] for q in version["questions"]] == [5, 3, 2]
    assert su_rows(
        world, "SELECT count(*) FROM vt.question_options WHERE question_id = %s", question["id"]
    ) == [(4,)]


def test_thresholds_are_configurable_on_a_draft_and_validated(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers, publish=False)
    url = f"/questionnaire-versions/{built['version_id']}"
    ok = api.patch(url, json={"low_max": 10, "medium_max": 40, "high_max": 90}, headers=headers)
    assert ok.status_code == 200
    assert (ok.json()["low_max"], ok.json()["medium_max"], ok.json()["high_max"]) == (10, 40, 90)
    for bad in (
        {"low_max": 50, "medium_max": 40, "high_max": 90},
        {"low_max": 0, "medium_max": 40, "high_max": 90},
        {"low_max": 10, "medium_max": 40, "high_max": 101},
        {"low_max": 10, "medium_max": 40},
    ):
        assert api.patch(url, json=bad, headers=headers).status_code == 422


def test_a_new_version_clones_the_latest_and_leaves_the_old_one_untouched(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_analyst)
    built = build_questionnaire(api, headers)
    v1 = built["version_id"]
    cloned = api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers)
    assert cloned.status_code == 201
    v2 = cloned.json()
    assert (v2["version_number"], v2["status"]) == (2, "draft")
    assert v2["id"] != v1
    old_ids = {q["id"] for q in built["questions"]}
    assert old_ids.isdisjoint({q["id"] for q in v2["questions"]})  # copies, not the same rows
    assert [(q["category"], q["weight"]) for q in v2["questions"]] == [
        ("mfa", 5),
        ("encryption", 3),
        ("mfa", 2),
    ]
    assert [[o["label"] for o in q["options"]] for q in v2["questions"]][0] == [
        label for label, _ in STANDARD
    ]
    # only one draft at a time
    assert (
        api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers).status_code
        == 409
    )
    # edit the draft freely; v1 does not move
    api.patch(
        f"/questions/{v2['questions'][0]['id']}",
        json={"weight": 10, "prompt": "Completely different question?"},
        headers=headers,
    )
    api.patch(
        f"/questionnaire-versions/{v2['id']}",
        json={"low_max": 5, "medium_max": 10, "high_max": 15},
        headers=headers,
    )
    old = api.get(f"/questionnaire-versions/{v1}", headers=headers).json()
    assert [q["weight"] for q in old["questions"]] == [5, 3, 2]
    assert old["questions"][0]["prompt"] == "Question number 1 about mfa?"
    assert (old["low_max"], old["medium_max"], old["high_max"]) == (25, 50, 75)
    summary = api.get(f"/questionnaires/{built['template_id']}", headers=headers).json()["versions"]
    assert [(v["version_number"], v["status"]) for v in summary] == [(2, "draft"), (1, "published")]


def test_archived_questions_are_not_carried_into_the_next_version(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers, publish=False)
    api.patch(
        f"/questions/{built['questions'][1]['id']}", json={"status": "archived"}, headers=headers
    )
    api.post(f"/questionnaire-versions/{built['version_id']}/publish", headers=headers)
    v2 = api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers).json()
    assert len(v2["questions"]) == 2


def test_retiring_and_archiving(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers)
    retired = api.post(f"/questionnaire-versions/{built['version_id']}/retire", headers=headers)
    assert retired.json()["status"] == "retired"
    assert (
        api.post(
            f"/questionnaire-versions/{built['version_id']}/retire", headers=headers
        ).status_code
        == 409
    )
    assert (
        api.post(
            f"/questionnaire-versions/{built['version_id']}/publish", headers=headers
        ).status_code
        == 409
    )
    assert (
        api.patch(
            f"/questions/{built['questions'][0]['id']}", json={"weight": 1}, headers=headers
        ).status_code
        == 409
    )
    api.patch(
        f"/questionnaires/{built['template_id']}", json={"status": "archived"}, headers=headers
    )
    assert (
        api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers).status_code
        == 409
    )


def test_a_draft_can_be_discarded_by_retiring_it(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers, publish=False)
    assert (
        api.post(
            f"/questionnaire-versions/{built['version_id']}/retire", headers=headers
        ).status_code
        == 200
    )
    # now a new draft can be started... from a retired version
    assert (
        api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers).status_code
        == 201
    )


# --- permissions and tenant isolation ------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["a_reviewer", "a_auditor"])
def test_read_only_roles_can_read_but_not_change_questionnaires(
    api: TestClient, world: World, who: str
) -> None:
    built = build_questionnaire(api, auth(world.a_admin), publish=False)
    headers = auth(getattr(world, who))
    assert api.get("/questionnaires", headers=headers).status_code == 200
    assert api.get(f"/questionnaires/{built['template_id']}", headers=headers).status_code == 200
    assert (
        api.get(f"/questionnaire-versions/{built['version_id']}", headers=headers).status_code
        == 200
    )
    writes = [
        api.post("/questionnaires", json={"name": "x"}, headers=headers),
        api.post("/questionnaires/from-starter", headers=headers),
        api.patch(f"/questionnaires/{built['template_id']}", json={"name": "x"}, headers=headers),
        api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers),
        api.post(
            f"/questionnaire-versions/{built['version_id']}/questions",
            json=question_body(),
            headers=headers,
        ),
        api.patch(f"/questions/{built['questions'][0]['id']}", json={"weight": 1}, headers=headers),
        api.post(
            f"/questions/{built['questions'][0]['id']}/options",
            json={"label": "x", "risk_points": 1},
            headers=headers,
        ),
        api.patch(
            f"/options/{built['questions'][0]['options']['Yes']}",
            json={"label": "x"},
            headers=headers,
        ),
        api.patch(
            f"/questionnaire-versions/{built['version_id']}",
            json={"low_max": 1, "medium_max": 2, "high_max": 3},
            headers=headers,
        ),
        api.put(
            f"/questionnaire-versions/{built['version_id']}/order",
            json={"question_ids": [q["id"] for q in built["questions"]]},
            headers=headers,
        ),
        api.post(f"/questionnaire-versions/{built['version_id']}/publish", headers=headers),
        api.post(f"/questionnaire-versions/{built['version_id']}/retire", headers=headers),
    ]
    assert [w.status_code for w in writes] == [403] * len(writes)
    still = api.get(f"/questionnaire-versions/{built['version_id']}", headers=headers).json()
    assert (still["status"], len(still["questions"])) == ("draft", 3)


def test_vendor_users_have_no_access_to_questionnaires(api: TestClient, world: World) -> None:
    built = build_questionnaire(api, auth(world.a_admin))
    headers = auth(world.a_vendor)
    assert api.get("/questionnaires", headers=headers).status_code == 403
    assert api.get(f"/questionnaires/{built['template_id']}", headers=headers).status_code == 403
    assert (
        api.get(f"/questionnaire-versions/{built['version_id']}", headers=headers).status_code
        == 403
    )
    assert api.post("/questionnaires", json={"name": "x"}, headers=headers).status_code == 403


def test_another_organization_cannot_see_or_change_a_questionnaire(
    api: TestClient, world: World
) -> None:
    built = build_questionnaire(api, auth(world.a_admin), publish=False)
    other = auth(world.b_admin)
    question = built["questions"][0]
    assert built["template_id"] not in {
        t["id"] for t in api.get("/questionnaires?limit=100", headers=other).json()
    }
    results = [
        api.get(f"/questionnaires/{built['template_id']}", headers=other),
        api.patch(f"/questionnaires/{built['template_id']}", json={"name": "pwned"}, headers=other),
        api.post(f"/questionnaires/{built['template_id']}/versions", headers=other),
        api.get(f"/questionnaire-versions/{built['version_id']}", headers=other),
        api.patch(
            f"/questionnaire-versions/{built['version_id']}",
            json={"low_max": 1, "medium_max": 2, "high_max": 3},
            headers=other,
        ),
        api.post(
            f"/questionnaire-versions/{built['version_id']}/questions",
            json=question_body(),
            headers=other,
        ),
        api.post(f"/questionnaire-versions/{built['version_id']}/publish", headers=other),
        api.post(f"/questionnaire-versions/{built['version_id']}/retire", headers=other),
        api.put(
            f"/questionnaire-versions/{built['version_id']}/order",
            json={"question_ids": [question["id"]]},
            headers=other,
        ),
        api.patch(f"/questions/{question['id']}", json={"weight": 1}, headers=other),
        api.post(
            f"/questions/{question['id']}/options",
            json={"label": "x", "risk_points": 1},
            headers=other,
        ),
        api.patch(f"/options/{question['options']['Yes']}", json={"label": "x"}, headers=other),
    ]
    assert [r.status_code for r in results] == [404] * len(results)
    assert su_rows(
        world,
        "SELECT name, status FROM vt.questionnaire_templates WHERE id = %s",
        built["template_id"],
    ) == [("Test questionnaire", "active")]
    assert su_rows(world, "SELECT weight FROM vt.questions WHERE id = %s", question["id"]) == [(5,)]


def test_the_organization_is_never_taken_from_the_request(api: TestClient, world: World) -> None:
    headers = auth(world.b_admin)
    created = api.post(
        "/questionnaires", json={"name": "B only"}, headers={**headers, "X-Org-Id": world.org_a}
    )
    assert created.status_code == 403  # B's admin is not a member of A
    mine = api.post("/questionnaires", json={"name": "B only"}, headers=headers).json()
    assert mine["org_id"] == world.org_b


def test_row_level_security_backstops_the_questionnaire_role_checks(
    api: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = build_questionnaire(api, auth(world.a_admin), publish=False)
    monkeypatch.setattr("app.routers.questionnaires.require_writer", lambda _context: None)
    headers = auth(world.a_reviewer)
    assert api.post("/questionnaires", json={"name": "sneaky"}, headers=headers).status_code == 403
    sneaky = api.post(
        f"/questionnaire-versions/{built['version_id']}/questions",
        json=question_body(),
        headers=headers,
    )
    assert sneaky.status_code in (403, 404)
    patched = api.patch(
        f"/questions/{built['questions'][0]['id']}", json={"weight": 1}, headers=headers
    )
    assert patched.status_code in (403, 404)
    assert su_rows(
        world, "SELECT count(*) FROM vt.questionnaire_templates WHERE name = 'sneaky'"
    ) == [(0,)]
    assert su_rows(
        world, "SELECT weight FROM vt.questions WHERE id = %s", built["questions"][0]["id"]
    ) == [(5,)]
    vendor_headers = auth(world.a_vendor)
    assert (
        api.post("/questionnaires", json={"name": "sneaky"}, headers=vendor_headers).status_code
        == 403
    )


# --- audit ----------------------------------------------------------------------------------------------------


def test_questionnaire_changes_are_audited(api: TestClient, world: World) -> None:
    headers = auth(world.a_analyst)
    built = build_questionnaire(api, headers)
    api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers)
    actions = su_rows(
        world,
        "SELECT action, actor_user_id::text, actor_role, org_id::text FROM vt.audit_log "
        "WHERE entity_type IN ('questionnaire', 'questionnaire_version', 'question') "
        "AND org_id = %s AND actor_user_id = %s ORDER BY id",
        world.org_a,
        world.a_analyst,
    )
    names = [a[0] for a in actions]
    assert "questionnaire.create" in names
    assert names.count("question.create") >= 3
    assert "questionnaire.version.publish" in names
    assert "questionnaire.version.create" in names
    assert {(a[1], a[2], a[3]) for a in actions} == {(world.a_analyst, "analyst", world.org_a)}


def test_failed_questionnaire_requests_commit_nothing(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    before = su_rows(
        world, "SELECT count(*) FROM vt.questionnaire_templates WHERE org_id = %s", world.org_a
    )
    api.post("/questionnaires", json={"name": ""}, headers=headers)
    api.post("/questionnaires", json={"name": "ok"}, headers=auth(world.a_reviewer))
    assert (
        su_rows(
            world, "SELECT count(*) FROM vt.questionnaire_templates WHERE org_id = %s", world.org_a
        )
        == before
    )


def test_a_new_version_keeps_the_thresholds_of_the_version_it_was_cloned_from(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_admin)
    built = build_questionnaire(api, headers, publish=False)
    api.patch(
        f"/questionnaire-versions/{built['version_id']}",
        json={"low_max": 10, "medium_max": 40, "high_max": 90},
        headers=headers,
    )
    api.post(f"/questionnaire-versions/{built['version_id']}/publish", headers=headers)
    v2 = api.post(f"/questionnaires/{built['template_id']}/versions", headers=headers).json()
    assert (v2["low_max"], v2["medium_max"], v2["high_max"]) == (10, 40, 90)
