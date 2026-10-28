"""Guards the committed environment templates against the mistakes Phase 1 must prevent.

The templates are parsed with the SAME strict parser the credential loader uses, so a template
that the loader would reject cannot be committed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.credentials import APP_ALLOWED_NAMES, MIGRATION_ALLOWED_NAMES
from app.envfile import parse_env_text

REPO_ROOT = Path(__file__).resolve().parents[3]
API_TEMPLATE = REPO_ROOT / "apps" / "api" / ".env.example"
MIGRATIONS_TEMPLATE = REPO_ROOT / "apps" / "api" / ".env.migrations.example"
WEB_TEMPLATE = REPO_ROOT / "apps" / "web" / ".env.example"
TEMPLATES = [API_TEMPLATE, MIGRATIONS_TEMPLATE, WEB_TEMPLATE]
FORBIDDEN_NAMES = re.compile(
    r"SERVICE_ROLE|SECRET_KEY|JWT_SECRET|ANON_KEY|POSTGRES_PASSWORD", re.IGNORECASE
)
REAL_SECRET_SHAPES = re.compile(r"(sb_secret_|sb_publishable_\w{8,}|eyJ[A-Za-z0-9_-]{20,})")


def _parse(path: Path) -> dict[str, str]:
    return parse_env_text(path.read_text(), source=path.name)


def _active_lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.parent.name + "/" + p.name)
def test_template_has_no_forbidden_variables_or_real_secrets(path: Path) -> None:
    assert path.is_file()
    for line in _active_lines(path):
        assert not FORBIDDEN_NAMES.search(line.split("=", 1)[0]), line
    assert not REAL_SECRET_SHAPES.search("\n".join(_active_lines(path)))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.parent.name + "/" + p.name)
def test_template_parses_with_the_strict_loader_parser(path: Path) -> None:
    assert _parse(path), "template must define variables"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.parent.name + "/" + p.name)
def test_template_passwords_are_placeholders(path: Path) -> None:
    for name, value in _parse(path).items():
        match = re.search(r"://[^/@\s]+:([^@\s]+)@", value)
        if match:
            assert match.group(1) == "CHANGE_ME", name


def test_only_public_names_use_next_public_prefix() -> None:
    names = _parse(WEB_TEMPLATE)
    assert all(name.startswith("NEXT_PUBLIC_") for name in names)


def test_api_template_defines_exactly_the_names_the_loader_allows() -> None:
    """Drift between the template and the loader's allowlist would break a copied template."""
    assert set(_parse(API_TEMPLATE)) == APP_ALLOWED_NAMES


def test_migration_template_defines_only_the_migration_credential() -> None:
    assert (
        set(_parse(MIGRATIONS_TEMPLATE)) == MIGRATION_ALLOWED_NAMES == {"VT_MIGRATION_DATABASE_URL"}
    )
    assert "VT_MIGRATION_DATABASE_URL" not in _parse(API_TEMPLATE)


def test_templates_say_to_keep_credentials_outside_the_repository() -> None:
    for path in (API_TEMPLATE, MIGRATIONS_TEMPLATE):
        header = "\n".join(path.read_text().splitlines()[:8])
        assert "~/.config/vendortrust/" in header, path.name
        assert "chmod 600" in header, path.name
