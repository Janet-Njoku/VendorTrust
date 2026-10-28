"""Database credential lookup: the one place that asks for the two roles' connection URLs.

Credentials are loaded ON DEMAND, when database functionality (or a script) actually needs
them. Importing `app.main` or constructing `Settings` never touches a credential file.

Where each role's URL comes from, in priority order:
  1. an explicit file (a script's `--env-file`),
  2. the process environment variable (`VT_DATABASE_URL` / `VT_MIGRATION_DATABASE_URL`),
  3. an explicitly configured file (`VT_ENV_FILE` / `VT_MIGRATION_ENV_FILE`),
  4. the default file under ~/.config/vendortrust/ (never in production, which must configure
     its credentials explicitly).

The two roles are kept strictly apart: each role has its own file and its own allowed variable
names, a URL must belong to its own role family (vt_app vs vt_migrator), and a process that holds
one role's URL in its environment refuses to load the other's.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from app.dburl import DatabaseUrlError, Role, validate_database_url
from app.envfile import EnvFileError, EnvFileMissingError, load_env_file

APP_URL_VAR = "VT_DATABASE_URL"
MIGRATION_URL_VAR = "VT_MIGRATION_DATABASE_URL"

# Every variable the shipped API template (apps/api/.env.example) may define. Anything else in
# the API file is rejected, which is what keeps the migration credential out of it.
APP_ALLOWED_NAMES = frozenset(
    {
        "VT_ENVIRONMENT",
        "VT_LOG_LEVEL",
        "VT_CORS_ALLOWED_ORIGINS",
        APP_URL_VAR,
        "VT_SUPABASE_URL",
        "VT_SUPABASE_JWKS_URL",
        "VT_SUPABASE_JWT_ISSUER",
        "VT_SUPABASE_JWT_AUDIENCE",
        "VT_ENFORCE_MFA_INTERNAL",
        "VT_EVIDENCE_BUCKET",
        "VT_EVIDENCE_MAX_BYTES",
        "VT_SIGNED_URL_TTL_SECONDS",
        "VT_CLAMAV_HOST",
        "VT_CLAMAV_PORT",
    }
)
MIGRATION_ALLOWED_NAMES = frozenset({MIGRATION_URL_VAR})


class CredentialError(Exception):
    """Credentials are missing, unsafe or invalid. Messages never contain secret values."""


class CredentialsNotConfiguredError(CredentialError):
    """No credential source is configured (a safe default when nothing was asked for)."""


@dataclass(frozen=True)
class _RoleConfig:
    url_var: str
    other_url_var: str
    file_var: str
    file_name: str
    allowed: frozenset[str]
    label: str
    other_label: str


_ROLES: dict[Role, _RoleConfig] = {
    "app": _RoleConfig(
        url_var=APP_URL_VAR,
        other_url_var=MIGRATION_URL_VAR,
        file_var="VT_ENV_FILE",
        file_name="api.env",
        allowed=APP_ALLOWED_NAMES,
        label="application",
        other_label="migration",
    ),
    "migrator": _RoleConfig(
        url_var=MIGRATION_URL_VAR,
        other_url_var=APP_URL_VAR,
        file_var="VT_MIGRATION_ENV_FILE",
        file_name="migrations.env",
        allowed=MIGRATION_ALLOWED_NAMES,
        label="migration",
        other_label="application",
    ),
}


@dataclass(frozen=True, repr=False)
class DatabaseCredential:
    role: Role
    url: SecretStr
    source: str
    warnings: tuple[str, ...] = ()

    def __repr__(self) -> str:  # never show the URL
        return f"DatabaseCredential(role={self.role!r}, source={self.source!r})"


def _home(home: Path | None) -> Path:
    if home is not None:
        return home
    try:
        return Path.home()
    except RuntimeError:
        raise CredentialError("cannot determine the home folder") from None


def _expand(value: str, home: Path | None) -> Path:
    if value == "~" or value.startswith("~/"):
        return _home(home) / value[2:]
    return Path(value)


def _resolve_file(
    cfg: _RoleConfig, env: Mapping[str, str], home: Path | None, env_file: Path | None
) -> tuple[Path | None, bool]:
    """Returns (path, explicit). A missing explicit file is an error; a missing default is not."""
    if env_file is not None:
        return env_file, True
    configured = env.get(cfg.file_var)
    if configured:
        return _expand(configured, home), True
    if env.get("VT_ENVIRONMENT") == "production":
        return None, False  # production never reads the default credential folder
    return _home(home) / ".config" / "vendortrust" / cfg.file_name, False


def _validate(cfg: _RoleConfig, role: Role, url: str, where: str) -> None:
    try:
        validate_database_url(url, role)
    except DatabaseUrlError as exc:
        raise CredentialError(f"{where}: {cfg.url_var}: {exc}") from None


def load_database_credential(
    role: Role,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    env_file: Path | None = None,
    repo_root: Path | None = None,
) -> DatabaseCredential:
    """Find, load and validate one role's database URL. See the module docstring for the order."""
    cfg = _ROLES[role]
    env = os.environ if environ is None else environ

    if env.get(cfg.other_url_var):
        raise CredentialError(
            f"{cfg.other_url_var} is set in this process; the {cfg.label} process must not "
            f"hold the {cfg.other_label} credential"
        )

    direct = env.get(cfg.url_var, "").strip()
    if direct and env_file is None:
        source = f"environment variable {cfg.url_var}"
        _validate(cfg, role, direct, source)
        return DatabaseCredential(role=role, url=SecretStr(direct), source=source)

    path, explicit = _resolve_file(cfg, env, home, env_file)
    if path is None:
        raise CredentialsNotConfiguredError(
            f"{cfg.label} database credentials are not configured: in production set "
            f"{cfg.url_var} or {cfg.file_var} (the default credential folder is never read)"
        )
    try:
        loaded = load_env_file(path, allowed=cfg.allowed, repo_root=repo_root)
    except EnvFileMissingError as exc:
        if explicit:
            raise CredentialError(str(exc)) from None
        raise CredentialsNotConfiguredError(
            f"{cfg.label} database credentials are not configured: create {path} "
            f"(see infra/db/RUNBOOK.md) or set {cfg.url_var}"
        ) from None
    except EnvFileError as exc:
        raise CredentialError(str(exc)) from None

    url = loaded.values.get(cfg.url_var, "").strip()
    if not url:
        raise CredentialError(f"{path}: {cfg.url_var} is missing or empty")
    _validate(cfg, role, url, str(path))
    return DatabaseCredential(
        role=role, url=SecretStr(url), source=str(path), warnings=loaded.warnings
    )


def app_database_url(**kwargs: object) -> SecretStr:
    """The restricted application role's URL (vt_app)."""
    return load_database_credential("app", **kwargs).url  # type: ignore[arg-type]


def migration_database_url(**kwargs: object) -> SecretStr:
    """The migration role's URL (vt_migrator). Used only by migration tooling (Phase 1.3)."""
    return load_database_credential("migrator", **kwargs).url  # type: ignore[arg-type]
