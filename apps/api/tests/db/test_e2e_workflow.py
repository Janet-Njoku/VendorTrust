"""The whole product, end to end, with nothing mocked inside VendorTrust.

Real FastAPI app, real PostgreSQL (migrated by Alembic, roles from bootstrap.sql), the real bootstrap
command, and tokens from the local Supabase Auth stand-in over real HTTP, verified by the API's own
JWKS verifier. Only the identity provider itself is local. This is the same journey docs/local-e2e.md
walks through by hand.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.config import Settings
from app.db.session import build_engine, get_engine
from app.main import create_app
from scripts.bootstrap_org import main as bootstrap_cli
from tests._creds import CANARY
from tests.db.alembic_tools import socket_url
from tests.db.conftest import SUPERUSER_ROLE, connect

pytestmark = pytest.mark.db

PASSWORD = CANARY  # a fake, unique password for the local stand-in


def http(
    base: str, method: str, path: str, body: dict[str, Any] | None = None, token: str | None = None
) -> Any:
    headers = {"Content-Type": "application/json", "apikey": "publishable"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(  # noqa: S310
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise AssertionError(f"{method} {path} -> {error.code} {error.read()!r}") from None


class Identities:
    """Sign up, sign in and complete MFA against the local auth server, like the web app does."""

    def __init__(self, base: str) -> None:
        self.base = base

    def sign_in(self, email: str, register: bool = True) -> dict[str, Any]:
        if register:
            http(self.base, "POST", "/auth/v1/signup", {"email": email, "password": PASSWORD})
        session: dict[str, Any] = http(
            self.base,
            "POST",
            "/auth/v1/token?grant_type=password",
            {"email": email, "password": PASSWORD},
        )
        return session

    def complete_mfa(self, session: dict[str, Any], code_for: Callable[[], str]) -> dict[str, Any]:
        token = session["access_token"]
        factor = http(
            self.base,
            "POST",
            "/auth/v1/factors",
            {"factor_type": "totp", "friendly_name": "e2e"},
            token,
        )
        challenge = http(
            self.base, "POST", f"/auth/v1/factors/{factor['id']}/challenge", token=token
        )
        upgraded: dict[str, Any] = http(
            self.base,
            "POST",
            f"/auth/v1/factors/{factor['id']}/verify",
            {"challenge_id": challenge["id"], "code": code_for()},
            token,
        )
        return upgraded


def bearer(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['access_token']}"}


@pytest.fixture
def stack(
    make_db: Callable[..., str], dev_auth: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Any]]:
    db = make_db()
    base, auth = dev_auth
    engine: Engine = build_engine(socket_url("vt_app", db))
    application = create_app(Settings(environment="test", supabase_url=base))  # the real verifier
    application.dependency_overrides[get_engine] = lambda: engine
    monkeypatch.setenv("VT_SUPABASE_URL", base)
    monkeypatch.setenv("VT_SUPABASE_PUBLISHABLE_KEY", "publishable")
    monkeypatch.setenv("VT_MIGRATION_DATABASE_URL", socket_url("vt_migrator", db))
    yield {
        "db": db,
        "base": base,
        "auth": auth,
        "api": TestClient(application, raise_server_exceptions=False),
        "ids": Identities(base),
    }
    engine.dispose()


def run_bootstrap(monkeypatch: pytest.MonkeyPatch, email: str, *flags: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    return bootstrap_cli(
        ["--org-name", "Acme Ltd", "--admin-email", email, "--password-stdin", *flags]
    )


def db_rows(db: str, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with connect(SUPERUSER_ROLE, db) as su:
        return su.execute(sql, params).fetchall()


def test_from_empty_database_to_a_scored_vendor_assessment(
    stack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api: TestClient = stack["api"]
    ids: Identities = stack["ids"]
    auth = stack["auth"]
    db: str = stack["db"]

    # 1. The future administrator registers and signs in. First login provisions a user, nothing more.
    root = ids.sign_in("root@example.invalid")
    me = api.get("/me", headers=bearer(root)).json()
    assert (me["email"], me["has_access"], me["memberships"]) == ("root@example.invalid", False, [])
    assert api.get("/vendors", headers=bearer(root)).json() == {"detail": "No active membership"}
    assert db_rows(db, "SELECT count(*) FROM vt.organizations") == [(0,)]

    # 2. The operator bootstraps the organization: a dry run first, then for real.
    assert run_bootstrap(monkeypatch, "root@example.invalid") == 0
    assert "DRY RUN OK" in capsys.readouterr().out
    assert db_rows(db, "SELECT count(*) FROM vt.organizations") == [(0,)]
    assert run_bootstrap(monkeypatch, "root@example.invalid", "--commit") == 0
    assert "Bootstrap COMMITTED" in capsys.readouterr().out
    assert run_bootstrap(monkeypatch, "root@example.invalid", "--commit") == 1  # one-time
    assert "already bootstrapped" in capsys.readouterr().err
    assert CANARY not in capsys.readouterr().out

    # 3. The administrator now has a role, but the API insists on MFA before any data.
    me = api.get("/me", headers=bearer(root)).json()
    assert (me["has_access"], me["active"]["role"]) == (True, "org_admin")
    assert me["mfa"] == {"required": True, "satisfied": False, "level": "aal1"}
    blocked = api.get("/vendors", headers=bearer(root))
    assert (blocked.status_code, blocked.json()["detail"]) == (
        403,
        "Multi-factor authentication required",
    )

    # 4. They enroll an authenticator, verify a code, and the session becomes aal2.
    admin = ids.complete_mfa(root, lambda: auth.code_for("root@example.invalid"))
    assert api.get("/me", headers=bearer(admin)).json()["mfa"]["satisfied"] is True
    assert api.get("/vendors", headers=bearer(admin)).json()["items"] == []

    # 5. Somebody else registering gets a user row and no access, whatever they do.
    ana = ids.sign_in("ana@example.invalid")
    assert api.get("/me", headers=bearer(ana)).json()["has_access"] is False
    for path in ("/vendors", "/questionnaires", "/assessments"):
        assert api.get(path, headers=bearer(ana)).status_code == 403
    assert api.post("/vendors", json={"name": "Sneaky"}, headers=bearer(ana)).status_code == 403
    assert db_rows(db, "SELECT count(*) FROM vt.memberships") == [(1,)]  # only the administrator's

    # 6. An administrator grants Ana a membership (done by hand; invitations are out of scope here).
    ana_id = ana["user"]["id"]
    org_id = db_rows(db, "SELECT id FROM vt.organizations")[0][0]
    db_rows(
        db,
        "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'analyst') RETURNING id",
        org_id,
        ana_id,
    )
    me = api.get("/me", headers=bearer(ana)).json()
    assert me["active"]["role"] == "analyst" and me["mfa"]["satisfied"] is False
    assert api.get("/vendors", headers=bearer(ana)).status_code == 403  # still needs MFA
    ana2 = ids.complete_mfa(ana, lambda: auth.code_for("ana@example.invalid"))
    assert api.get("/vendors", headers=bearer(ana2)).status_code == 200

    # 7. The real work: vendor, questionnaire, assessment, answers, score.
    vendor = api.post("/vendors", json={"name": "Northwind Payments"}, headers=bearer(admin)).json()
    template = api.post("/questionnaires/from-starter", headers=bearer(admin)).json()
    version_id = template["versions"][0]["id"]
    published = api.post(f"/questionnaire-versions/{version_id}/publish", headers=bearer(admin))
    assert published.status_code == 200

    def assess(pick: Callable[[list[dict[str, Any]]], dict[str, Any]]) -> dict[str, Any]:
        created = api.post(
            f"/vendors/{vendor['id']}/assessments",
            json={"questionnaire_id": template["id"]},
            headers=bearer(ana2),  # the analyst does the assessment
        )
        assert created.status_code == 201, created.text
        assessment = created.json()
        detail = api.get(f"/assessments/{assessment['id']}", headers=bearer(ana2)).json()
        assert detail["progress"] == {"answered": 0, "total": 15}
        for question in detail["questions"]:
            option = pick([o for o in question["options"] if o["risk_points"] is not None])
            saved = api.put(
                f"/assessments/{assessment['id']}/responses/{question['id']}",
                json={"option_id": option["id"]},
                headers=bearer(ana2),
            )
            assert saved.status_code == 200, saved.text
        result = api.post(f"/assessments/{assessment['id']}/score", headers=bearer(ana2))
        assert result.status_code == 200, result.text
        return {"assessment": assessment, "result": result.json()}

    best = assess(lambda options: min(options, key=lambda o: o["risk_points"]))
    assert (best["result"]["score"], best["result"]["risk_level"]) == (0.0, "low")
    worst = assess(lambda options: max(options, key=lambda o: o["risk_points"]))
    assert (worst["result"]["score"], worst["result"]["risk_level"]) == (100.0, "critical")
    assert len(worst["result"]["breakdown"]["categories"]) == 8
    assert (
        api.get(f"/assessments/{worst['assessment']['id']}/result", headers=bearer(admin)).json()[
            "score"
        ]
        == 100.0
    )
    scored = api.get("/assessments?status=scored", headers=bearer(admin)).json()["items"]
    assert len(scored) == 2

    # 8. A token refresh keeps the session working at the same assurance level.
    refreshed = http(
        stack["base"],
        "POST",
        "/auth/v1/token?grant_type=refresh_token",
        {"refresh_token": admin["refresh_token"]},
    )
    assert api.get("/vendors", headers=bearer(refreshed)).status_code == 200

    # 9. Tenant isolation: a second organization (created by an operator) sees none of this.
    other_user = ids.sign_in("other@example.invalid")
    other = ids.complete_mfa(other_user, lambda: auth.code_for("other@example.invalid"))
    api.get("/me", headers=bearer(other))
    other_org = db_rows(db, "INSERT INTO vt.organizations (name) VALUES ('Globex') RETURNING id")[
        0
    ][0]
    db_rows(
        db,
        "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'org_admin') RETURNING id",
        other_org,
        other_user["user"]["id"],
    )
    assert api.get("/vendors", headers=bearer(other)).json()["items"] == []
    assert api.get(f"/vendors/{vendor['id']}", headers=bearer(other)).status_code == 404
    assert (
        api.get(f"/assessments/{worst['assessment']['id']}", headers=bearer(other)).status_code
        == 404
    )
    assert api.get("/questionnaires", headers=bearer(other)).json() == []

    # 10. The audit trail recorded the important events with the right actors.
    events = db_rows(db, "SELECT action, actor_role FROM vt.audit_log ORDER BY id")
    actions = [a for a, _ in events]
    assert actions[0] == "bootstrap.first_organization"
    for expected in (
        "vendor.create",
        "questionnaire.create_from_starter",
        "assessment.create",
        "assessment.score",
    ):
        assert expected in actions, expected
    assert actions.count("assessment.score") == 2
