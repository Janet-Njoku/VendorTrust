import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_returns_ok() -> None:
    client = TestClient(create_app(Settings(environment="test")))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs_disabled_in_production() -> None:
    client = TestClient(create_app(Settings(environment="production")))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_available_outside_production() -> None:
    client = TestClient(create_app(Settings(environment="development")))
    assert client.get("/openapi.json").status_code == 200


def test_cors_origins_parsed_from_comma_separated_string() -> None:
    settings = Settings.model_validate({"cors_allowed_origins": "http://a.test, http://b.test"})
    assert settings.cors_allowed_origins == ["http://a.test", "http://b.test"]


def test_wildcard_cors_origin_rejected() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        Settings.model_validate({"cors_allowed_origins": "*"})


def test_cors_origins_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VT_CORS_ALLOWED_ORIGINS", "http://a.test,http://b.test")
    assert Settings().cors_allowed_origins == ["http://a.test", "http://b.test"]


def test_single_cors_origin_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VT_CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    assert Settings().cors_allowed_origins == ["http://localhost:3000"]


def test_env_example_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented template must load unchanged (inline comments, list values)."""
    for name in list(os.environ):
        if name.startswith("VT_"):
            monkeypatch.delenv(name)
    example = Path(__file__).resolve().parents[1] / ".env.example"
    settings = Settings(_env_file=example)
    assert settings.environment == "development"
    assert settings.cors_allowed_origins == ["http://localhost:3000"]
