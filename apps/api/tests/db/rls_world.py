"""A seeded two-organization world and helpers for the RLS tests (fake data only).

Seeding runs as the local superuser (it must bypass RLS; vt_app and vt_migrator cannot insert
organizations). Every call creates fresh random ids, so tests never depend on each other and
the shared database accumulates other tenants' rows that isolation tests must not see.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg

from tests.db.conftest import SUPERUSER_ROLE, Conn, connect, conninfo

ALL_TABLES = (
    "organizations",
    "app_users",
    "vendors",
    "memberships",
    "audit_log",
    "questionnaire_templates",
    "questionnaire_versions",
    "questions",
    "question_options",
    "assessments",
    "assessment_responses",
)


def uid() -> str:
    return str(uuid.uuid4())


@dataclass
class World:
    db: str
    org_a: str
    org_b: str
    vendor_a1: str
    vendor_a2: str
    vendor_b1: str
    vendor_b2: str
    a_admin: str
    a_admin2: str
    a_analyst: str
    a_reviewer: str
    a_auditor: str
    a_vendor: str  # vendor_user bound to vendor_a1
    b_admin: str
    b_analyst: str
    multi: str  # analyst in A, auditor in B
    outsider: str  # has an app_users row but no membership
    disabled: str  # disabled user whose membership is otherwise live in A
    mem: dict[str, str]  # user id -> membership id (the membership in org A unless noted)
    multi_b_membership: str

    @property
    def a_vendors(self) -> set[str]:
        return {self.vendor_a1, self.vendor_a2}

    @property
    def b_vendors(self) -> set[str]:
        return {self.vendor_b1, self.vendor_b2}


def seed(db: str) -> World:
    ids = {
        name: uid()
        for name in (
            "org_a",
            "org_b",
            "vendor_a1",
            "vendor_a2",
            "vendor_b1",
            "vendor_b2",
            "a_admin",
            "a_admin2",
            "a_analyst",
            "a_reviewer",
            "a_auditor",
            "a_vendor",
            "b_admin",
            "b_analyst",
            "multi",
            "outsider",
            "disabled",
        )
    }
    with connect(SUPERUSER_ROLE, db) as su:
        for key in ("org_a", "org_b"):
            su.execute("INSERT INTO vt.organizations (id, name) VALUES (%s, %s)", (ids[key], key))
        for name in (
            "a_admin",
            "a_admin2",
            "a_analyst",
            "a_reviewer",
            "a_auditor",
            "a_vendor",
            "b_admin",
            "b_analyst",
            "multi",
            "outsider",
            "disabled",
        ):
            su.execute(
                "INSERT INTO vt.app_users (id, email, status) VALUES (%s, %s, %s)",
                (
                    ids[name],
                    f"{name}-{ids[name][:8]}@example.invalid",
                    "disabled" if name == "disabled" else "active",
                ),
            )
        for key, org in (
            ("vendor_a1", "org_a"),
            ("vendor_a2", "org_a"),
            ("vendor_b1", "org_b"),
            ("vendor_b2", "org_b"),
        ):
            su.execute(
                "INSERT INTO vt.vendors (id, org_id, name) VALUES (%s, %s, %s)",
                (ids[key], ids[org], key),
            )
        mem: dict[str, str] = {}
        rows = [
            ("a_admin", "org_a", "org_admin", None),
            ("a_admin2", "org_a", "org_admin", None),
            ("a_analyst", "org_a", "analyst", None),
            ("a_reviewer", "org_a", "reviewer", None),
            ("a_auditor", "org_a", "auditor", None),
            ("a_vendor", "org_a", "vendor_user", "vendor_a1"),
            ("b_admin", "org_b", "org_admin", None),
            ("b_analyst", "org_b", "analyst", None),
            ("multi", "org_a", "analyst", None),
            ("disabled", "org_a", "analyst", None),
        ]
        for user, org, role, vendor in rows:
            row = su.execute(
                "INSERT INTO vt.memberships (org_id, user_id, role, vendor_id) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (ids[org], ids[user], role, ids[vendor] if vendor else None),
            ).fetchone()
            assert row is not None
            mem[ids[user]] = str(row[0])
        row = su.execute(
            "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'auditor') "
            "RETURNING id",
            (ids["org_b"], ids["multi"]),
        ).fetchone()
        assert row is not None
        for org, actor, role in (
            ("org_a", "a_admin", "org_admin"),
            ("org_b", "b_admin", "org_admin"),
        ):
            su.execute(
                "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type) "
                "VALUES (%s, %s, %s, 'seed.event', 'seed')",
                (ids[org], ids[actor], role),
            )
    return World(db=db, mem=mem, multi_b_membership=str(row[0]), **ids)


@contextmanager
def app(db: str, *, autocommit: bool = False) -> Iterator[Conn]:
    """A vt_app connection. Autocommit is OFF by default so each test controls transactions."""
    with psycopg.connect(conninfo("vt_app", db), autocommit=autocommit) as conn:
        yield conn


def begin(conn: Conn, user: str, org: str | None = None) -> None:
    conn.execute("SELECT vt.begin_request(%s::uuid, %s::uuid)", (user, org))


def forge(
    conn: Conn,
    user: str | None,
    org: str | None,
    role: str | None,
    vendor: str | None = None,
    *,
    local: bool = True,
) -> None:
    """Set raw claims with set_config, exactly what a compromised or buggy caller could do."""
    for name, value in (("user_id", user), ("org_id", org), ("role", role), ("vendor_id", vendor)):
        conn.execute("SELECT set_config(%s, %s, %s)", (f"vt.claim_{name}", value or "", local))


def ids(conn: Conn, sql: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(sql).fetchall()}


def count(conn: Conn, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM vt.{table}").fetchone()
    assert row is not None
    return int(row[0])


def sqlstate(exc: BaseException) -> str | None:
    return getattr(exc, "sqlstate", None)
