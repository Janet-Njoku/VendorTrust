from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from `VT_*` environment variables.

    Phase 0 only needs a handful of values; database, Supabase and storage
    settings are added with the phases that use them.
    """

    model_config = SettingsConfigDict(env_prefix="VT_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    # NoDecode: accept a plain comma-separated string from the environment instead of JSON.
    cors_allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

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
