"""Shared helpers for the API tests: a local EC signing key standing in for Supabase's, token
builders, and a superuser query helper. Nothing here is a real credential."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tests.db.conftest import SUPERUSER_ROLE, connect
from tests.db.rls_world import World

ISSUER = "https://example.invalid/auth/v1"
AUDIENCE = "authenticated"
KID = "test-key-1"
PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())
PUBLIC_PEM = PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)


def make_token(
    sub: str | None,
    *,
    key: Any = PRIVATE_KEY,
    alg: str = "ES256",
    kid: str | None = KID,
    **claims: Any,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 600,
        "aal": "aal2",  # the default test user has completed MFA; tests override for aal1
        "email": f"{(sub or 'nobody')[:8]}@example.invalid",  # real tokens always carry one
        **claims,
    }
    if sub is not None:
        payload["sub"] = sub
    payload = {k: v for k, v in payload.items() if v is not None}
    headers = {"kid": kid} if kid else {}
    return jwt.encode(payload, key, algorithm=alg, headers=headers)


def forge_hs256(secret: bytes, payload: dict[str, Any]) -> str:
    """The classic algorithm-confusion forgery, built by hand (PyJWT refuses to make it)."""

    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    body = b64(json.dumps(payload).encode())
    signature = hmac.new(secret, header + b"." + body, hashlib.sha256).digest()
    return (header + b"." + body + b"." + b64(signature)).decode()


def auth(user: str, org: str | None = None, **claims: Any) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {make_token(user, **claims)}"}
    if org:
        headers["X-Org-Id"] = org
    return headers


def su_rows(world: World, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with connect(SUPERUSER_ROLE, world.db) as su:
        return su.execute(sql, params).fetchall()


# --- questionnaire helpers ------------------------------------------------------------------------

STANDARD = [("Yes", 0), ("Partial", 50), ("No", 100), ("N/A", None)]
DEFAULT_SPEC = [  # (category, weight)
    ("mfa", 5),
    ("encryption", 3),
    ("mfa", 2),
]


def build_questionnaire(
    api: Any,
    headers: dict[str, str],
    spec: list[tuple[str, int]] | None = None,
    *,
    publish: bool = True,
    name: str = "Test questionnaire",
) -> dict[str, Any]:
    """Create a template with questions through the API (optionally publishing version 1).

    Returns {"template_id", "version_id", "questions": [{"id", "weight", "options": {label: id}}]}.
    """
    created = api.post("/questionnaires", json={"name": name}, headers=headers)
    assert created.status_code == 201, created.text
    template = created.json()
    version_id = template["versions"][0]["id"]
    questions = []
    for index, (category, weight) in enumerate(spec or DEFAULT_SPEC, start=1):
        body = {
            "category": category,
            "prompt": f"Question number {index} about {category}?",
            "weight": weight,
            "options": [{"label": label, "risk_points": points} for label, points in STANDARD],
        }
        response = api.post(
            f"/questionnaire-versions/{version_id}/questions", json=body, headers=headers
        )
        assert response.status_code == 201, response.text
        data = response.json()
        questions.append(
            {
                "id": data["id"],
                "weight": weight,
                "options": {o["label"]: o["id"] for o in data["options"]},
            }
        )
    if publish:
        published = api.post(f"/questionnaire-versions/{version_id}/publish", headers=headers)
        assert published.status_code == 200, published.text
    return {"template_id": template["id"], "version_id": version_id, "questions": questions}


def answer_all(
    api: Any, headers: dict[str, str], assessment_id: str, built: dict[str, Any], labels: list[str]
) -> None:
    for question, label in zip(built["questions"], labels, strict=True):
        response = api.put(
            f"/assessments/{assessment_id}/responses/{question['id']}",
            json={"option_id": question["options"][label]},
            headers=headers,
        )
        assert response.status_code == 200, response.text
