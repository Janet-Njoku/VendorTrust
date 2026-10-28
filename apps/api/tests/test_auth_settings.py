"""Supabase JWT settings: derivation, https-only, and verifier construction (no network)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.auth import AuthUnavailable, JwtVerifier, verifier_from_settings
from app.config import Settings


def test_urls_default_from_the_project_url() -> None:
    settings = Settings(supabase_url="https://abc.supabase.co/")
    assert settings.jwks_url == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    assert settings.jwt_issuer == "https://abc.supabase.co/auth/v1"
    assert settings.supabase_jwt_audience == "authenticated"


def test_explicit_urls_win() -> None:
    settings = Settings(
        supabase_url="https://abc.supabase.co",
        supabase_jwks_url="https://keys.example.invalid/jwks.json",
        supabase_jwt_issuer="https://issuer.example.invalid",
    )
    assert settings.jwks_url == "https://keys.example.invalid/jwks.json"
    assert settings.jwt_issuer == "https://issuer.example.invalid"


@pytest.mark.parametrize(
    "bad",
    [
        "http://abc.supabase.co",
        "ftp://abc.supabase.co",
        "https://user:pw@abc.supabase.co",
        "https://",
    ],
)
def test_insecure_or_credentialed_urls_are_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        Settings(supabase_url=bad)


def test_plain_http_is_only_allowed_for_localhost() -> None:
    assert (
        Settings(supabase_url="http://localhost:54321").jwt_issuer
        == "http://localhost:54321/auth/v1"
    )


def test_blank_values_mean_not_configured() -> None:
    settings = Settings(supabase_url="  ")
    assert settings.jwks_url is None
    assert settings.jwt_issuer is None


def test_leeway_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(jwt_leeway_seconds=3600)


def test_verifier_needs_configuration_and_is_reused() -> None:
    with pytest.raises(AuthUnavailable):
        verifier_from_settings(Settings())
    configured = Settings(supabase_url="https://abc.supabase.co")
    first = verifier_from_settings(configured)
    assert isinstance(first, JwtVerifier)
    assert verifier_from_settings(configured) is first  # one JWKS cache per configuration
