from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from `VT_*` process environment variables only.

    Settings never reads a file and never holds a credential: database URLs come from
    `app.credentials`, on demand, from the environment or a file outside the repository.
    """

    model_config = SettingsConfigDict(env_prefix="VT_", extra="ignore", hide_input_in_errors=True)

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    # NoDecode: accept a plain comma-separated string from the environment instead of JSON.
    cors_allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # Supabase Auth. None of these is secret (the JWKS is public), so they are plain settings.
    # Only the project URL is needed; the others default from it.
    supabase_url: str | None = None
    supabase_jwks_url: str | None = None
    supabase_jwt_issuer: str | None = None
    supabase_jwt_audience: str = "authenticated"
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=120)

    # Internal roles (org_admin, analyst, reviewer, auditor) must present an aal2 token, i.e. have
    # completed MFA. Vendor users are exempt. It can only be switched off outside production.
    enforce_mfa_internal: bool = True

    @model_validator(mode="after")
    def _production_is_strict(self) -> "Settings":
        if self.environment != "production":
            return self
        if not self.enforce_mfa_internal:
            raise ValueError("MFA enforcement for internal roles cannot be disabled in production")
        for value in (self.supabase_url, self.supabase_jwks_url, self.supabase_jwt_issuer):
            if value and urlsplit(value).scheme != "https":
                raise ValueError(
                    "production authentication URLs must be https (no local stand-ins)"
                )
        return self

    @field_validator("supabase_url", "supabase_jwks_url", "supabase_jwt_issuer")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parts = urlsplit(value.strip())
        local = parts.hostname in {"localhost", "127.0.0.1"}
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValueError("must be an https URL")
        if not parts.hostname or parts.username or parts.password:
            raise ValueError("must have a host and no embedded credentials")
        return value.strip().rstrip("/")

    @property
    def jwks_url(self) -> str | None:
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json" if self.supabase_url else None

    @property
    def jwt_issuer(self) -> str | None:
        if self.supabase_jwt_issuer:
            return self.supabase_jwt_issuer
        return f"{self.supabase_url}/auth/v1" if self.supabase_url else None

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _reject_wildcard(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError("wildcard CORS origins are not permitted")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
