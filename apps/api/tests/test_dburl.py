"""Database URL validation: role families, transport and safe messages (fake URLs only)."""

from __future__ import annotations

import pytest

from app import dburl
from app.dburl import DatabaseUrlError, describe, parse_target, redact, validate_database_url
from tests._creds import APP_URL, CANARY, HOST, MIGRATION_URL, assert_no_canary, full_text


def test_valid_urls_pass_for_their_own_role() -> None:
    validate_database_url(APP_URL, "app")
    validate_database_url(MIGRATION_URL, "migrator")
    validate_database_url(
        f"postgresql://vt_app:{CANARY}@{HOST}/postgres?sslmode=verify-full", "app"
    )
    validate_database_url("postgresql://vt_app@/postgres?host=/tmp/sock&port=5432", "app")
    validate_database_url("postgresql://vt_migrator@localhost/postgres", "migrator")


def test_roles_cannot_be_swapped() -> None:
    with pytest.raises(DatabaseUrlError, match="role family"):
        validate_database_url(MIGRATION_URL, "app")
    with pytest.raises(DatabaseUrlError, match="role family"):
        validate_database_url(APP_URL, "migrator")


@pytest.mark.parametrize(
    "user",
    ["postgres", "postgres.fakeproj", "vt_application", "vt_app_extra", "vt_apps.x", "VT_APP"],
)
def test_lookalike_and_privileged_users_are_rejected(user: str) -> None:
    with pytest.raises(DatabaseUrlError, match="role family"):
        validate_database_url(f"postgresql://{user}:pw@{HOST}/postgres?sslmode=require", "app")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("mysql://vt_app:pw@h/db", "scheme"),
        ("http://vt_app:pw@h/db", "scheme"),
        ("vt_app:pw@h/db", "scheme"),
        ("", "empty"),
        ("   ", "empty"),
        (f"postgresql://:pw@{HOST}/db?sslmode=require", "no user"),
        (f"postgresql://vt_app:pw@{HOST}/db", "sslmode"),
        (f"postgresql://vt_app:pw@{HOST}/db?sslmode=disable", "sslmode"),
        (f"postgresql://vt_app:pw@{HOST}/db?sslmode=prefer", "sslmode"),
        ("postgresql://vt_app:pw@h/db\n?sslmode=require", "control"),
        ("postgresql://vt_app:pw@[bad-host/db?sslmode=require", "parsed"),
    ],
)
def test_malformed_or_unsafe_urls_are_rejected(url: str, reason: str) -> None:
    with pytest.raises(DatabaseUrlError, match=reason):
        validate_database_url(url, "app")


@pytest.mark.parametrize(
    "url",
    [
        f"mysql://vt_app:{CANARY}@h/db",
        f"postgresql://postgres:{CANARY}@{HOST}/db?sslmode=require",
        f"postgresql://vt_app:{CANARY}@{HOST}/db",
        f"postgresql://vt_app:{CANARY}@[bad/db",
        f"postgresql://:{CANARY}@{HOST}/db",
    ],
)
def test_error_messages_never_contain_the_url_user_or_password(url: str) -> None:
    with pytest.raises(DatabaseUrlError) as info:
        validate_database_url(url, "app")
    text = full_text(info.value)
    assert_no_canary(text)
    assert HOST not in text


def test_describe_never_includes_the_password() -> None:
    summary = describe(parse_target(APP_URL))
    assert_no_canary(summary)
    assert summary == f"vt_app.fakeproj@{HOST}:5432/postgres"


def test_redact_removes_url_and_password_from_driver_messages() -> None:
    cleaned = redact(f'connection to "{APP_URL}" failed: password {CANARY} rejected', APP_URL)
    assert_no_canary(cleaned)
    assert HOST not in cleaned


def test_redact_survives_an_unparseable_url() -> None:
    assert CANARY not in redact(f"oops {CANARY}", f"postgresql://x:{CANARY}@[bad")


def test_local_connections_do_not_need_tls() -> None:
    assert dburl.tls_acceptable(parse_target("postgresql://u@/db?host=/tmp/s"))
    assert dburl.tls_acceptable(parse_target("postgresql://u@localhost/db"))
    assert not dburl.tls_acceptable(parse_target(f"postgresql://u@{HOST}/db"))
