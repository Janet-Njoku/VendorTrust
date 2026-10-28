"""Supabase JWT verification against the project's published JWKS.

Only asymmetric algorithms are accepted (never HS256 or `none`), so a token cannot be forged
with a public key or a shared secret. Signature, expiry, issuer and audience are all checked,
and `sub` must be a UUID. This module proves WHO the caller is. It says nothing about what they
may do: organization and role always come from the database (see app.db.context).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError

from app.config import Settings

ALGORITHMS = ["ES256", "RS256"]
KeyResolver = Callable[[str], Any]


class AuthError(Exception):
    """The token is missing, malformed, untrusted or expired. Never carries token content."""


class AuthUnavailable(Exception):
    """The verification keys could not be fetched (a server-side problem, not the caller's)."""


@dataclass(frozen=True)
class Identity:
    user_id: uuid.UUID
    email: str | None = None
    #: Authenticator assurance level from the signed token: "aal2" only after MFA, else "aal1".
    aal: str = "aal1"


class JwtVerifier:
    def __init__(
        self, *, issuer: str, audience: str, key_resolver: KeyResolver, leeway: int = 30
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._resolve_key = key_resolver
        self._leeway = leeway

    def verify(self, token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError from None
        if header.get("alg") not in ALGORITHMS or not header.get("kid"):
            raise AuthError
        try:
            key = self._resolve_key(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except PyJWKClientConnectionError:
            raise AuthUnavailable from None
        except jwt.PyJWTError:
            raise AuthError from None
        if claims.get("is_anonymous") is True:
            raise AuthError
        try:
            user_id = uuid.UUID(str(claims["sub"]))
        except ValueError:
            raise AuthError from None
        email = claims.get("email")
        return Identity(
            user_id=user_id,
            email=email.strip().lower() if isinstance(email, str) and email.strip() else None,
            aal="aal2" if claims.get("aal") == "aal2" else "aal1",
        )


def jwks_key_resolver(jwks_url: str) -> KeyResolver:
    client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)

    def resolve(token: str) -> Any:
        return client.get_signing_key_from_jwt(token).key

    return resolve


@lru_cache
def _verifier_for(jwks_url: str, issuer: str, audience: str, leeway: int) -> JwtVerifier:
    """One JWKS client (and key cache) per configuration."""
    return JwtVerifier(
        issuer=issuer, audience=audience, key_resolver=jwks_key_resolver(jwks_url), leeway=leeway
    )


def verifier_from_settings(settings: Settings) -> JwtVerifier:
    if not settings.jwks_url or not settings.jwt_issuer:
        raise AuthUnavailable
    return _verifier_for(
        settings.jwks_url,
        settings.jwt_issuer,
        settings.supabase_jwt_audience,
        settings.jwt_leeway_seconds,
    )
