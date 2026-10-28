from __future__ import annotations

from typing import Any

import pytest

from scripts import check_db_connection as dbc
from scripts import check_jwks as jwks

PASSWORD = "s3cr3t-Pa55word"
URL = f"postgresql+psycopg://vt_app.abcd:{PASSWORD}@aws-0-eu.pooler.supabase.com:5432/postgres"


def test_normalize_url_strips_sqlalchemy_driver() -> None:
    assert dbc.normalize_url("postgresql+psycopg://u@h/db") == "postgresql://u@h/db"


def test_describe_never_includes_password() -> None:
    summary = dbc.describe(dbc.parse_target(URL))
    assert PASSWORD not in summary
    assert summary == "vt_app.abcd@aws-0-eu.pooler.supabase.com:5432/postgres"


def test_redact_removes_password_and_url_from_errors() -> None:
    message = f'connection to "{URL}" failed for password {PASSWORD}'
    cleaned = dbc.redact(message, URL)
    assert PASSWORD not in cleaned
    assert "pooler.supabase.com" not in cleaned


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("postgresql://u@h.example.com/db", False),  # remote, default sslmode=prefer
        ("postgresql://u@h.example.com/db?sslmode=disable", False),
        ("postgresql://u@h.example.com/db?sslmode=require", True),
        ("postgresql://u@h.example.com/db?sslmode=verify-full", True),
        ("postgresql://u@localhost/db", True),
        ("postgresql://u@/db?host=/tmp/sock", True),
    ],
)
def test_tls_policy(url: str, ok: bool) -> None:
    assert dbc.tls_policy(dbc.parse_target(url)).ok is ok


def test_run_refuses_non_tls_remote_without_connecting() -> None:
    checks = dbc.run("postgresql://u@db.invalid.example/db", "app")
    assert [c.ok for c in checks] == [False]


def test_main_exits_2_when_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VT_DATABASE_URL", raising=False)
    assert dbc.main(["--kind", "app"]) == 2


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://abc.supabase.co/auth/v1/.well-known/jwks.json", True),
        ("http://abc.supabase.co/auth/v1/.well-known/jwks.json", False),
        ("https://user:pw@abc.supabase.co/x", False),
        ("https:///nohost", False),
    ],
)
def test_jwks_url_validation(url: str, ok: bool) -> None:
    assert jwks.validate_url(url).ok is ok


def test_jwks_url_derived_from_project_url() -> None:
    assert (
        jwks.build_jwks_url({"VT_SUPABASE_URL": "https://abc.supabase.co/"})
        == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    )
    assert jwks.build_jwks_url({}) is None


def _key(**overrides: Any) -> dict[str, Any]:
    key: dict[str, Any] = {
        "kty": "EC",
        "crv": "P-256",
        "alg": "ES256",
        "kid": "k1",
        "x": "a",
        "y": "b",
    }
    return key | overrides


def test_jwks_valid_keyset_passes() -> None:
    assert all(c.ok for c in jwks.assess({"keys": [_key()]}))


@pytest.mark.parametrize(
    "bad",
    [
        {"keys": []},  # legacy shared-secret project publishes no keys
        {},
        {"keys": [_key(kty="oct", k="c2VjcmV0")]},  # symmetric key in JWKS
        {"keys": [_key(d="private-scalar")]},  # private key material published
        {"keys": [_key(kid="")]},
        {"keys": [_key(alg="HS256")]},  # no accepted algorithm
    ],
)
def test_jwks_unsafe_keysets_fail(bad: dict[str, Any]) -> None:
    assert not all(c.ok for c in jwks.assess(bad))
