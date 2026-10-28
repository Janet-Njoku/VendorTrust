"""Vendor API end to end: mocked Supabase JWTs (local signing key), the local migrated database,
real RLS. Nothing here talks to Supabase or reads a credential."""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.exceptions import PyJWKClientConnectionError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.auth import JwtVerifier
from app.config import Settings
from app.db.session import UnsafeDatabaseRole, build_engine, get_engine
from app.deps import get_verifier
from app.errors import install_error_handlers
from app.main import create_app
from tests._creds import CANARY
from tests.db.alembic_tools import socket_url
from tests.db.api_support import (
    AUDIENCE,
    ISSUER,
    KID,
    OTHER_KEY,
    PUBLIC_PEM,
    auth,
    forge_hs256,
    make_token,
    su_rows,
)
from tests.db.conftest import SUPERUSER_ROLE, connect
from tests.db.rls_world import World

pytestmark = pytest.mark.db


# --- authentication ----------------------------------------------------------------------------


def test_missing_or_malformed_authorization_is_401(api: TestClient) -> None:
    for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}):
        response = api.get("/vendors", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json() == {"detail": "Not authenticated"}


@pytest.mark.parametrize(
    "case",
    [
        "garbage",
        "expired",
        "wrong_issuer",
        "wrong_audience",
        "missing_sub",
        "sub_not_uuid",
        "wrong_signing_key",
        "hs256_with_public_key",
        "alg_none",
        "no_kid",
        "not_yet_valid",
        "anonymous_user",
        "missing_exp",
    ],
)
def test_bad_tokens_are_401_and_never_echoed(api: TestClient, world: World, case: str) -> None:
    user = world.a_admin
    now = int(time.time())
    tokens = {
        "garbage": "not.a.jwt",
        "expired": make_token(user, exp=now - 3600, iat=now - 7200),
        "wrong_issuer": make_token(user, iss="https://evil.invalid/auth/v1"),
        "wrong_audience": make_token(user, aud="anon"),
        "missing_sub": make_token(None),
        "sub_not_uuid": make_token("admin"),
        "wrong_signing_key": make_token(user, key=OTHER_KEY),
        "hs256_with_public_key": forge_hs256(
            PUBLIC_PEM, {"sub": user, "iss": ISSUER, "aud": AUDIENCE, "exp": now + 600}
        ),
        "alg_none": jwt.encode(
            {"sub": user, "iss": ISSUER, "aud": AUDIENCE, "exp": now + 600},
            None,  # type: ignore[arg-type]
            algorithm="none",
            headers={"kid": KID},
        ),
        "no_kid": make_token(user, kid=None),
        "not_yet_valid": make_token(user, nbf=now + 3600),
        "anonymous_user": make_token(user, is_anonymous=True),
        "missing_exp": make_token(user, exp=None),
    }
    token = tokens[case]
    response = api.get("/vendors", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401, case
    assert token not in response.text
    assert response.json() == {"detail": "Not authenticated"}


def test_clock_skew_within_the_leeway_is_tolerated(api: TestClient, world: World) -> None:
    now = int(time.time())
    headers = {"Authorization": f"Bearer {make_token(world.a_admin, exp=now - 5, iat=now - 600)}"}
    assert api.get("/vendors", headers=headers).status_code == 200


def test_unreachable_key_server_is_503_not_401(engine: Engine, world: World) -> None:
    def failing(_token: str) -> Any:
        raise PyJWKClientConnectionError("down")

    app = create_app(Settings(environment="test"))
    verifier = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=failing)
    app.dependency_overrides[get_verifier] = lambda: verifier
    app.dependency_overrides[get_engine] = lambda: engine
    response = TestClient(app).get("/vendors", headers=auth(world.a_admin))
    assert response.status_code == 503


def test_authentication_not_configured_is_503(world: World) -> None:
    client = TestClient(create_app(Settings(environment="test")), raise_server_exceptions=False)
    assert client.get("/vendors", headers=auth(world.a_admin)).status_code == 503


# --- membership resolution --------------------------------------------------------------------


def test_a_valid_token_without_a_membership_is_403(api: TestClient, world: World) -> None:
    for user in (world.outsider, str(uuid.uuid4()), world.disabled):
        response = api.get("/vendors", headers=auth(user))
        assert response.status_code == 403
        assert response.json() == {"detail": "No active membership"}


def test_a_suspended_membership_is_403_immediately(api: TestClient, world: World) -> None:
    assert api.get("/vendors", headers=auth(world.a_analyst)).status_code == 200
    with connect(SUPERUSER_ROLE, world.db) as su:
        su.execute(
            "UPDATE vt.memberships SET status = 'suspended' WHERE id = %s",
            (world.mem[world.a_analyst],),
        )
    assert api.get("/vendors", headers=auth(world.a_analyst)).status_code == 403


def test_users_in_several_organizations_must_choose_and_can_only_choose_their_own(
    api: TestClient, world: World
) -> None:
    assert api.get("/vendors", headers=auth(world.multi)).status_code == 400
    in_a = api.get("/vendors", headers=auth(world.multi, world.org_a))
    in_b = api.get("/vendors", headers=auth(world.multi, world.org_b))
    assert {v["org_id"] for v in in_a.json()["items"]} == {world.org_a}
    assert {v["org_id"] for v in in_b.json()["items"]} == {world.org_b}
    # an organization the user does not belong to is refused, exactly like no membership
    assert api.get("/vendors", headers=auth(world.a_admin, world.org_b)).status_code == 403
    assert api.get("/vendors", headers=auth(world.a_admin, str(uuid.uuid4()))).status_code == 403
    assert (
        api.get("/vendors", headers={**auth(world.a_admin), "X-Org-Id": "nope"}).status_code == 400
    )


def test_the_role_is_never_taken_from_the_request(api: TestClient, world: World) -> None:
    # JWT claims that look like permissions are ignored: this reviewer stays read-only
    token = make_token(
        world.a_reviewer,
        role="org_admin",
        app_metadata={"role": "org_admin", "org_id": world.org_a},
        user_metadata={"role": "org_admin"},
    )
    headers = {"Authorization": f"Bearer {token}", "X-Role": "org_admin"}
    assert api.post("/vendors", json={"name": "x"}, headers=headers).status_code == 403


# --- authorization (role matrix) --------------------------------------------------------------


@pytest.mark.parametrize("who", ["a_admin", "a_analyst"])
def test_writers_create_and_update(api: TestClient, world: World, who: str) -> None:
    headers = auth(getattr(world, who))
    created = api.post("/vendors", json={"name": f"  New vendor by {who}  "}, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == f"New vendor by {who}"
    assert body["status"] == "active"
    assert body["org_id"] == world.org_a
    updated = api.patch(
        f"/vendors/{body['id']}", json={"name": "Renamed", "status": "archived"}, headers=headers
    )
    assert updated.status_code == 200
    assert (updated.json()["name"], updated.json()["status"]) == ("Renamed", "archived")
    assert updated.json()["updated_at"] >= body["updated_at"]


@pytest.mark.parametrize("who", ["a_reviewer", "a_auditor"])
def test_read_only_roles_read_but_cannot_write(api: TestClient, world: World, who: str) -> None:
    headers = auth(getattr(world, who))
    listed = api.get("/vendors", headers=headers)
    assert {v["id"] for v in listed.json()["items"]} == world.a_vendors
    assert api.get(f"/vendors/{world.vendor_a1}", headers=headers).status_code == 200
    assert api.post("/vendors", json={"name": "x"}, headers=headers).status_code == 403
    patch = api.patch(f"/vendors/{world.vendor_a1}", json={"name": "x"}, headers=headers)
    assert patch.status_code == 403
    assert su_rows(world, "SELECT name FROM vt.vendors WHERE id = %s", world.vendor_a1) == [
        ("vendor_a1",)
    ]


def test_vendor_user_sees_only_the_assigned_vendor_and_cannot_write(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_vendor)
    assert [v["id"] for v in api.get("/vendors", headers=headers).json()["items"]] == [
        world.vendor_a1
    ]
    assert api.get(f"/vendors/{world.vendor_a1}", headers=headers).status_code == 200
    assert api.get(f"/vendors/{world.vendor_a2}", headers=headers).status_code == 404
    assert api.post("/vendors", json={"name": "x"}, headers=headers).status_code == 403
    assert (
        api.patch(f"/vendors/{world.vendor_a1}", json={"name": "x"}, headers=headers).status_code
        == 403
    )


def test_row_level_security_is_the_backstop_if_the_role_check_is_bypassed(
    api: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.routers.vendors.require_writer", lambda _context: None)
    headers = auth(world.a_reviewer)
    assert api.post("/vendors", json={"name": "sneaky"}, headers=headers).status_code == 403
    patch = api.patch(f"/vendors/{world.vendor_a1}", json={"name": "sneaky"}, headers=headers)
    assert patch.status_code in (403, 404)
    assert su_rows(world, "SELECT count(*) FROM vt.vendors WHERE name = 'sneaky'") == [(0,)]
    vendor_headers = auth(world.a_vendor)
    assert api.post("/vendors", json={"name": "sneaky"}, headers=vendor_headers).status_code == 403


# --- tenant isolation -------------------------------------------------------------------------


def test_another_organizations_vendors_are_invisible_and_untouchable(
    api: TestClient, world: World
) -> None:
    headers = auth(world.a_admin)
    listed = {v["id"] for v in api.get("/vendors?limit=100", headers=headers).json()["items"]}
    assert listed == world.a_vendors
    assert api.get(f"/vendors/{world.vendor_b1}", headers=headers).status_code == 404
    patch = api.patch(f"/vendors/{world.vendor_b1}", json={"name": "pwned"}, headers=headers)
    assert patch.status_code == 404
    assert su_rows(world, "SELECT name FROM vt.vendors WHERE id = %s", world.vendor_b1) == [
        ("vendor_b1",)
    ]
    # and org B's admin is equally blind to org A
    other = auth(world.b_admin)
    assert api.get(f"/vendors/{world.vendor_a1}", headers=other).status_code == 404


def test_the_organization_cannot_be_chosen_in_the_body(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    smuggled = api.post("/vendors", json={"name": "x", "org_id": world.org_b}, headers=headers)
    assert smuggled.status_code == 422
    created = api.post("/vendors", json={"name": "ok"}, headers=headers).json()
    assert created["org_id"] == world.org_a
    patched = api.patch(
        f"/vendors/{world.vendor_a1}", json={"org_id": world.org_b}, headers=headers
    )
    assert patched.status_code == 422
    assert (
        api.patch(
            f"/vendors/{world.vendor_a1}", json={"id": str(uuid.uuid4())}, headers=headers
        ).status_code
        == 422
    )


def test_context_never_leaks_into_the_pooled_connection(
    api: TestClient, engine: Engine, world: World
) -> None:
    for _ in range(6):
        assert api.get("/vendors", headers=auth(world.a_admin)).status_code == 200
        with engine.connect() as plain:
            assert plain.execute(text("SELECT count(*) FROM vt.vendors")).scalar_one() == 0
            assert plain.execute(
                text("SELECT current_setting('vt.claim_user_id', true)")
            ).scalar_one() in ("", None)


# --- CRUD, validation, errors -----------------------------------------------------------------


def test_create_get_list_update_round_trip_and_audit(api: TestClient, world: World) -> None:
    headers = auth(world.a_analyst)
    created = api.post("/vendors", json={"name": "Acme Hosting"}, headers=headers).json()
    assert set(created) == {"id", "org_id", "name", "status", "created_at", "updated_at"}
    fetched = api.get(f"/vendors/{created['id']}", headers=headers)
    assert fetched.status_code == 200 and fetched.json() == created
    api.patch(f"/vendors/{created['id']}", json={"status": "archived"}, headers=headers)
    archived = api.get("/vendors?status=archived", headers=headers).json()["items"]
    assert [v["id"] for v in archived] == [created["id"]]
    active = {v["id"] for v in api.get("/vendors?status=active", headers=headers).json()["items"]}
    assert created["id"] not in active
    events = su_rows(
        world,
        "SELECT action, actor_user_id::text, actor_role, org_id::text, before_data IS NULL "
        "FROM vt.audit_log WHERE entity_id = %s ORDER BY id",
        created["id"],
    )
    assert events == [
        ("vendor.create", world.a_analyst, "analyst", world.org_a, True),
        ("vendor.update", world.a_analyst, "analyst", world.org_a, False),
    ]


def test_a_vendor_can_be_created_archived(api: TestClient, world: World) -> None:
    created = api.post(
        "/vendors", json={"name": "Legacy Co", "status": "archived"}, headers=auth(world.a_admin)
    )
    assert created.status_code == 201
    assert created.json()["status"] == "archived"


def test_list_pagination_and_ordering(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    # mixed case and underscores: the order is byte order ("C" collation), not the platform's locale
    for name in ("Zeta", "vendor_a2", "Alpha", "vendor_a1", "Mid", "Mid"):
        api.post("/vendors", json={"name": name}, headers=headers)
    everything = api.get("/vendors?limit=100", headers=headers).json()["items"]
    names = [v["name"] for v in everything]
    assert names == sorted(names, key=lambda n: n.encode())
    assert "Zeta" in names and names.index("Zeta") < names.index("vendor_a1")
    ties = [v["id"] for v in everything if v["name"] == "Mid"]
    assert ties == sorted(ties)  # equal names are ordered by id
    page = api.get("/vendors?limit=2&offset=1", headers=headers).json()
    assert page["limit"] == 2 and page["offset"] == 1
    assert [v["id"] for v in page["items"]] == [v["id"] for v in everything[1:3]]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 201},
        {"name": "bad\x00name"},
        {"name": "line\nbreak"},
        {"name": 5},
        {"name": "ok", "status": "deleted"},
        {"name": "ok", "unexpected": True},
        {"name": None},
    ],
)
def test_invalid_create_bodies_are_422(api: TestClient, world: World, body: dict[str, Any]) -> None:
    response = api.post("/vendors", json=body, headers=auth(world.a_admin))
    assert response.status_code == 422


@pytest.mark.parametrize("body", [{}, {"name": None}, {"name": ""}, {"status": "x"}, {"nope": 1}])
def test_invalid_update_bodies_are_422(api: TestClient, world: World, body: dict[str, Any]) -> None:
    response = api.patch(f"/vendors/{world.vendor_a1}", json=body, headers=auth(world.a_admin))
    assert response.status_code == 422


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "status=bogus", "limit=x"])
def test_invalid_list_parameters_are_422(api: TestClient, world: World, query: str) -> None:
    assert api.get(f"/vendors?{query}", headers=auth(world.a_admin)).status_code == 422


def test_bad_ids_and_unknown_vendors(api: TestClient, world: World) -> None:
    headers = auth(world.a_admin)
    assert api.get("/vendors/not-a-uuid", headers=headers).status_code == 422
    assert api.get(f"/vendors/{uuid.uuid4()}", headers=headers).status_code == 404
    assert (
        api.patch(f"/vendors/{uuid.uuid4()}", json={"name": "x"}, headers=headers).status_code
        == 404
    )


def test_validation_errors_do_not_echo_secrets_or_internals(api: TestClient, world: World) -> None:
    response = api.post(
        "/vendors", json={"name": "ok", "password": CANARY}, headers=auth(world.a_admin)
    )
    assert response.status_code == 422
    assert "vt." not in response.text and "SELECT" not in response.text


def test_database_errors_become_generic_responses_without_details() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise DBAPIError(
            f"SELECT {CANARY} FROM secret_table", {"p": CANARY}, Exception("secret_table")
        )

    @app.get("/denied")
    def denied() -> None:
        raise DBAPIError("INSERT", {}, psycopg.errors.InsufficientPrivilege("new row violates RLS"))

    client = TestClient(app, raise_server_exceptions=False)
    failure = client.get("/boom")
    assert failure.status_code == 500
    assert failure.json() == {"detail": "Internal server error"}
    assert CANARY not in failure.text and "secret_table" not in failure.text
    refused = client.get("/denied")
    assert refused.status_code == 403
    assert refused.json() == {"detail": "Insufficient permissions"}


def test_a_failed_request_commits_nothing(api: TestClient, world: World) -> None:
    before = su_rows(world, "SELECT count(*) FROM vt.vendors WHERE org_id = %s", world.org_a)
    api.post("/vendors", json={"name": "x" * 500}, headers=auth(world.a_admin))
    api.post("/vendors", json={"name": "fine"}, headers=auth(world.a_reviewer))
    assert (
        su_rows(world, "SELECT count(*) FROM vt.vendors WHERE org_id = %s", world.org_a) == before
    )


# --- the application must connect as the restricted role --------------------------------------


@pytest.mark.parametrize("user", ["vt_migrator", "vt_dev_admin"])
def test_the_engine_refuses_privileged_roles(migrated_db: str, user: str) -> None:
    engine = build_engine(socket_url(user, migrated_db))
    try:
        with pytest.raises(UnsafeDatabaseRole):
            engine.connect()
    finally:
        engine.dispose()
