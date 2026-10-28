"""First-login provisioning (GET /me) and MFA (aal2) enforcement for internal roles."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine

from app.auth import JwtVerifier
from app.config import Settings
from app.db.session import get_engine
from app.deps import get_verifier
from app.main import create_app
from tests.db.api_support import (
    AUDIENCE,
    ISSUER,
    OTHER_KEY,
    PRIVATE_KEY,
    auth,
    make_token,
    su_rows,
)
from tests.db.conftest import SUPERUSER_ROLE, connect
from tests.db.rls_world import World, app, forge

pytestmark = pytest.mark.db


def client_with(settings: Settings, engine: Engine) -> TestClient:
    application = create_app(settings)
    verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: PRIVATE_KEY.public_key()
    )
    application.dependency_overrides[get_verifier] = lambda: verifier
    application.dependency_overrides[get_engine] = lambda: engine
    return TestClient(application, raise_server_exceptions=False)


def new_user_headers(email: str | None = None, **claims: Any) -> tuple[str, dict[str, str]]:
    user = str(uuid.uuid4())
    token = make_token(user, email=email or f"{user[:8]}@example.invalid", **claims)
    return user, {"Authorization": f"Bearer {token}"}


def user_rows(world: World, user: str) -> list[tuple[Any, ...]]:
    return su_rows(world, "SELECT email, status FROM vt.app_users WHERE id = %s", user)


def membership_count(world: World, user: str) -> int:
    return int(su_rows(world, "SELECT count(*) FROM vt.memberships WHERE user_id = %s", user)[0][0])


# --- first-login provisioning ------------------------------------------------------------------------------


def test_me_needs_a_valid_token(api: TestClient) -> None:
    assert api.get("/me").status_code == 401
    assert api.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_a_new_user_is_provisioned_without_any_access(api: TestClient, world: World) -> None:
    user, headers = new_user_headers("  New.Person@Example.INVALID ")
    assert user_rows(world, user) == []
    body = api.get("/me", headers=headers).json()
    assert user_rows(world, user) == [("new.person@example.invalid", "active")]
    assert body["user_id"] == user
    assert body["email"] == "new.person@example.invalid"
    assert (
        body["has_access"],
        body["active"],
        body["memberships"],
        body["needs_organization"],
    ) == (False, None, [], False)
    assert body["mfa"] == {"required": False, "satisfied": True, "level": "aal2"}
    assert membership_count(world, user) == 0


def test_provisioning_is_idempotent_and_never_creates_a_membership(
    api: TestClient, world: World
) -> None:
    user, headers = new_user_headers()
    for _ in range(3):
        assert api.get("/me", headers=headers).status_code == 200
    assert len(user_rows(world, user)) == 1
    assert membership_count(world, user) == 0


def test_a_provisioned_user_still_gets_403_everywhere_and_sees_nothing(
    api: TestClient, world: World
) -> None:
    _, headers = new_user_headers()
    api.get("/me", headers=headers)
    for path in ("/vendors", "/questionnaires", "/assessments"):
        response = api.get(path, headers=headers)
        assert (response.status_code, response.json()["detail"]) == (403, "No active membership"), (
            path
        )
    assert api.post("/vendors", json={"name": "x"}, headers=headers).status_code == 403
    assert api.post("/questionnaires", json={"name": "x"}, headers=headers).status_code == 403


def test_signing_in_alone_grants_nothing_even_for_a_user_who_registered_long_ago(
    api: TestClient, world: World
) -> None:
    user, headers = new_user_headers()
    api.get("/me", headers=headers)
    api.get("/me", headers=headers)
    assert api.get("/vendors", headers=headers).status_code == 403
    assert su_rows(world, "SELECT count(*) FROM vt.memberships WHERE user_id = %s", user) == [(0,)]


def test_an_account_without_an_email_cannot_be_provisioned(api: TestClient, world: World) -> None:
    user = str(uuid.uuid4())
    headers = {"Authorization": f"Bearer {make_token(user, email=None)}"}
    response = api.get("/me", headers=headers)
    assert (response.status_code, response.json()["detail"]) == (
        403,
        "Your account has no email address",
    )
    assert user_rows(world, user) == []


def test_provisioning_cannot_create_a_row_for_anyone_else(world: World, migrated_db: str) -> None:
    """The database, not the API, limits provisioning to the caller's own row."""
    mine, other = str(uuid.uuid4()), str(uuid.uuid4())
    with app(migrated_db, autocommit=True) as conn:
        with conn.transaction():
            forge(conn, mine, None, None)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO vt.app_users (id, email) VALUES (%s, 'x@example.invalid')",
                    (other,),
                )
        with conn.transaction():
            forge(conn, mine, None, None)
            with pytest.raises(
                psycopg.errors.InsufficientPrivilege
            ):  # a disabled row, even for oneself
                conn.execute(
                    "INSERT INTO vt.app_users (id, email, status) VALUES (%s, 'x@example.invalid', 'disabled')",
                    (mine,),
                )
        with conn.transaction():
            forge(conn, mine, None, None)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):  # and never a membership
                conn.execute(
                    "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'org_admin')",
                    (world.org_a, mine),
                )


def test_me_reports_the_role_and_organization_from_the_database(
    api: TestClient, world: World
) -> None:
    body = api.get("/me", headers=auth(world.a_analyst)).json()
    assert body["has_access"] is True
    assert body["active"] == {"org_id": world.org_a, "role": "analyst", "vendor_id": None}
    assert body["memberships"] == [body["active"]]
    assert body["mfa"] == {"required": True, "satisfied": True, "level": "aal2"}
    vendor = api.get("/me", headers=auth(world.a_vendor)).json()
    assert vendor["active"]["role"] == "vendor_user"
    assert vendor["active"]["vendor_id"] == world.vendor_a1
    assert vendor["mfa"]["required"] is False


def test_a_user_in_several_organizations_must_choose_one(api: TestClient, world: World) -> None:
    body = api.get("/me", headers=auth(world.multi)).json()
    assert (body["has_access"], body["needs_organization"], body["active"]) == (False, True, None)
    assert {m["org_id"] for m in body["memberships"]} == {world.org_a, world.org_b}
    chosen = api.get("/me", headers=auth(world.multi, world.org_b)).json()
    assert (chosen["has_access"], chosen["active"]["role"]) == (True, "auditor")
    assert (
        api.get("/me", headers=auth(world.multi, str(uuid.uuid4()))).json()["has_access"] is False
    )
    assert api.get("/me", headers={**auth(world.multi), "X-Org-Id": "nope"}).status_code == 400


def test_disabled_users_and_suspended_memberships_have_no_access(
    api: TestClient, world: World
) -> None:
    disabled = api.get("/me", headers=auth(world.disabled)).json()
    assert (disabled["status"], disabled["has_access"]) == ("disabled", False)
    with connect(SUPERUSER_ROLE, world.db) as su:
        su.execute(
            "UPDATE vt.memberships SET status = 'suspended' WHERE id = %s",
            (world.mem[world.a_reviewer],),
        )
    body = api.get("/me", headers=auth(world.a_reviewer)).json()
    assert (body["has_access"], body["memberships"]) == (False, [])


# --- MFA ----------------------------------------------------------------------------------------------------


INTERNAL = ["a_admin", "a_analyst", "a_reviewer", "a_auditor"]


@pytest.mark.parametrize("who", INTERNAL)
def test_internal_roles_need_aal2_on_every_data_endpoint(
    api: TestClient, world: World, who: str
) -> None:
    user = getattr(world, who)
    weak = auth(user, aal="aal1")
    strong = auth(user)
    requests: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", "/vendors", None),
        ("GET", f"/vendors/{world.vendor_a1}", None),
        ("POST", "/vendors", {"name": "Blocked Ltd"}),
        ("PATCH", f"/vendors/{world.vendor_a1}", {"name": "Blocked"}),
        ("GET", "/questionnaires", None),
        ("POST", "/questionnaires", {"name": "Blocked"}),
        ("GET", "/assessments", None),
        (
            "POST",
            f"/vendors/{world.vendor_a1}/assessments",
            {"questionnaire_id": str(uuid.uuid4())},
        ),
    ]
    for method, path, body in requests:
        response = api.request(method, path, json=body, headers=weak)
        assert response.status_code == 403, (method, path)
        assert response.json() == {"detail": "Multi-factor authentication required"}
        assert "insufficient_user_authentication" in response.headers["www-authenticate"]
    assert api.get("/vendors", headers=strong).status_code == 200
    assert su_rows(
        world, "SELECT count(*) FROM vt.vendors WHERE name IN ('Blocked Ltd', 'Blocked')"
    ) == [(0,)]
    assert su_rows(
        world, "SELECT count(*) FROM vt.questionnaire_templates WHERE name = 'Blocked'"
    ) == [(0,)]


def test_the_mfa_check_comes_after_membership_and_leaks_nothing(
    api: TestClient, world: World
) -> None:
    _, headers = new_user_headers(aal="aal1")
    assert api.get("/vendors", headers=headers).json() == {"detail": "No active membership"}
    weak = api.get("/vendors", headers=auth(world.a_admin, aal="aal1"))
    assert weak.json() == {"detail": "Multi-factor authentication required"}
    assert world.vendor_a1 not in weak.text


def test_vendor_users_are_not_required_to_use_mfa(api: TestClient, world: World) -> None:
    response = api.get("/vendors", headers=auth(world.a_vendor, aal="aal1"))
    assert response.status_code == 200
    assert [v["id"] for v in response.json()["items"]] == [world.vendor_a1]


def test_me_works_without_mfa_and_says_what_is_missing(api: TestClient, world: World) -> None:
    body = api.get("/me", headers=auth(world.a_admin, aal="aal1")).json()
    assert body["has_access"] is True
    assert body["mfa"] == {"required": True, "satisfied": False, "level": "aal1"}
    chooser = api.get("/me", headers=auth(world.multi, aal="aal1")).json()
    assert chooser["mfa"]["required"] is True  # decided from the memberships that need a choice


@pytest.mark.parametrize("claim", ["AAL2", "aal3", "aal", "", None, 2, True, ["aal2"]])
def test_only_the_exact_value_aal2_counts(api: TestClient, world: World, claim: Any) -> None:
    response = api.get("/vendors", headers=auth(world.a_admin, aal=claim))
    assert response.status_code == 403


def test_a_missing_aal_claim_means_aal1(api: TestClient, world: World) -> None:
    import jwt as pyjwt

    from tests.db.api_support import KID

    token = pyjwt.encode(
        {
            "sub": world.a_admin,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": 4_000_000_000,
            "email": "a@example.invalid",
        },
        PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": KID},
    )
    assert api.get("/vendors", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_a_forged_aal2_claim_does_not_survive_signature_checking(
    api: TestClient, world: World
) -> None:
    forged = make_token(world.a_admin, key=OTHER_KEY, aal="aal2")
    assert api.get("/vendors", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_enforcement_can_be_switched_off_only_outside_production(
    engine: Engine, world: World
) -> None:
    relaxed = client_with(Settings(environment="test", enforce_mfa_internal=False), engine)
    assert relaxed.get("/vendors", headers=auth(world.a_admin, aal="aal1")).status_code == 200
    assert (
        relaxed.get("/me", headers=auth(world.a_admin, aal="aal1")).json()["mfa"]["required"]
        is False
    )
    with pytest.raises(ValidationError, match="cannot be disabled in production"):
        Settings(environment="production", enforce_mfa_internal=False)
    assert Settings(environment="production").enforce_mfa_internal is True


def test_production_refuses_plain_http_authentication_urls() -> None:
    for url in ("http://127.0.0.1:54330", "http://localhost:54330"):
        with pytest.raises(ValidationError, match="must be https"):
            Settings(environment="production", supabase_url=url)
    assert Settings(environment="production", supabase_url="https://abc.supabase.co").jwt_issuer
    assert Settings(environment="development", supabase_url="http://127.0.0.1:54330").jwt_issuer


def test_a_provisioned_internal_user_is_asked_for_mfa_only_once_they_have_a_role(
    api: TestClient, world: World
) -> None:
    user, headers = new_user_headers(aal="aal1")
    assert api.get("/me", headers=headers).json()["mfa"]["required"] is False
    with connect(SUPERUSER_ROLE, world.db) as su:
        su.execute(
            "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'analyst')",
            (world.org_a, user),
        )
    assert api.get("/me", headers=headers).json()["mfa"] == {
        "required": True,
        "satisfied": False,
        "level": "aal1",
    }
    assert api.get("/vendors", headers=headers).status_code == 403
    upgraded = {"Authorization": f"Bearer {make_token(user, email='x@example.invalid')}"}
    assert api.get("/vendors", headers=upgraded).status_code == 200


def test_every_data_route_goes_through_the_mfa_check(api: TestClient) -> None:
    """A route added later without the request context would be a hole: list them all."""
    public = {"/health", "/me", "/docs", "/openapi.json", "/docs/oauth2-redirect"}
    paths = set(api.get("/openapi.json").json()["paths"]) - public
    assert paths, "no data routes found"
    for path in sorted(paths):
        urls = path.replace("{vendor_id}", str(uuid.uuid4()))
        urls = urls.replace("{version_id}", str(uuid.uuid4())).replace(
            "{template_id}", str(uuid.uuid4())
        )
        urls = urls.replace("{assessment_id}", str(uuid.uuid4())).replace(
            "{question_id}", str(uuid.uuid4())
        )
        urls = urls.replace("{option_id}", str(uuid.uuid4())).replace("{id}", str(uuid.uuid4()))
        # no token at all: every data route must refuse before touching data
        for method in api.get("/openapi.json").json()["paths"][path]:
            response = api.request(method.upper(), urls)
            assert response.status_code == 401, (method, path)
