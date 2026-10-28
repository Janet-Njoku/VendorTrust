"""Tenant isolation and role restrictions, as the restricted application role (vt_app)."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import errors

from tests.db.conftest import SUPERUSER_ROLE, connect
from tests.db.rls_world import ALL_TABLES, World, app, begin, count, ids, seed

pytestmark = pytest.mark.db


@pytest.fixture
def world(migrated_db: str) -> World:
    return seed(migrated_db)


def vendor_ids(conn: psycopg.Connection) -> set[str]:
    return ids(conn, "SELECT id FROM vt.vendors")


def test_without_any_context_nothing_is_visible_or_writable(world: World) -> None:
    with app(world.db) as conn:
        for table in ALL_TABLES:
            assert count(conn, table) == 0, table
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO vt.vendors (org_id, name) VALUES (%s, 'x')", (world.org_a,))


def test_an_org_admin_sees_only_its_own_organization(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_admin)
        assert vendor_ids(conn) == world.a_vendors
        assert ids(conn, "SELECT id FROM vt.organizations") == {world.org_a}
        assert {r[0] for r in conn.execute("SELECT DISTINCT org_id::text FROM vt.memberships")} == {
            world.org_a
        }


def test_other_organizations_rows_cannot_be_read_changed_or_removed(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_admin)
        assert (
            conn.execute("SELECT 1 FROM vt.vendors WHERE id = %s", (world.vendor_b1,)).fetchall()
            == []
        )
        update = conn.execute(
            "UPDATE vt.vendors SET name = 'pwned' WHERE id = %s", (world.vendor_b1,)
        )
        assert update.rowcount == 0
        with pytest.raises(errors.InsufficientPrivilege):  # no DELETE grant at all
            conn.execute("DELETE FROM vt.vendors WHERE id = %s", (world.vendor_b1,))
    with connect(SUPERUSER_ROLE, world.db) as su:
        row = su.execute("SELECT name FROM vt.vendors WHERE id = %s", (world.vendor_b1,)).fetchone()
        assert row == ("vendor_b1",)


def test_a_row_cannot_be_inserted_into_or_moved_to_another_organization(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_admin)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO vt.vendors (org_id, name) VALUES (%s, 'x')", (world.org_b,))
        conn.rollback()
        begin(conn, world.a_admin)
        with pytest.raises(errors.InsufficientPrivilege):  # org_id is not an updatable column
            conn.execute(
                "UPDATE vt.vendors SET org_id = %s WHERE id = %s", (world.org_b, world.vendor_a1)
            )


def test_vendor_user_sees_only_its_vendor_and_cannot_write(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_vendor)
        assert vendor_ids(conn) == {world.vendor_a1}
        assert count(conn, "audit_log") == 0
        assert conn.execute("UPDATE vt.vendors SET name = 'x'").rowcount == 0
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO vt.vendors (org_id, name) VALUES (%s, 'x')", (world.org_a,))
        conn.rollback()
        begin(conn, world.a_vendor)
        # a vendor user cannot enumerate the roster or the organization's people
        assert ids(conn, "SELECT user_id FROM vt.memberships") == {world.a_vendor}
        assert ids(conn, "SELECT id FROM vt.app_users") == {world.a_vendor}


@pytest.mark.parametrize("who", ["a_reviewer", "a_auditor"])
def test_read_only_roles_read_but_never_write_vendors(world: World, who: str) -> None:
    with app(world.db) as conn:
        begin(conn, getattr(world, who))
        assert vendor_ids(conn) == world.a_vendors
        assert conn.execute("UPDATE vt.vendors SET name = 'x'").rowcount == 0
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO vt.vendors (org_id, name) VALUES (%s, 'x')", (world.org_a,))


def test_analyst_writes_vendors_but_not_memberships_or_the_audit_log_reads(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        conn.execute("INSERT INTO vt.vendors (org_id, name) VALUES (%s, 'new')", (world.org_a,))
        assert (
            conn.execute("UPDATE vt.vendors SET name = 'renamed' WHERE name = 'new'").rowcount == 1
        )
        assert count(conn, "audit_log") == 0
        assert (
            conn.execute(
                "UPDATE vt.memberships SET status = 'revoked' WHERE org_id = %s", (world.org_a,)
            ).rowcount
            == 0
        )
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'analyst')",
                (world.org_a, world.outsider),
            )


def test_org_admin_manages_memberships_in_its_own_organization_only(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_admin)
        conn.execute(
            "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'reviewer')",
            (world.org_a, world.outsider),
        )
        changed = conn.execute(
            "UPDATE vt.memberships SET role = 'auditor' WHERE user_id = %s", (world.outsider,)
        )
        assert changed.rowcount == 1
        # not into another organization, and no row deletion exists
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'analyst')",
                (world.org_b, world.outsider),
            )
        conn.rollback()
        begin(conn, world.a_admin)
        other = conn.execute(
            "UPDATE vt.memberships SET status = 'revoked' WHERE org_id = %s", (world.org_b,)
        )
        assert other.rowcount == 0
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("DELETE FROM vt.memberships WHERE user_id = %s", (world.outsider,))


def test_a_user_edits_only_their_own_display_name(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        own = conn.execute(
            "UPDATE vt.app_users SET display_name = 'Me' WHERE id = %s", (world.a_analyst,)
        )
        assert own.rowcount == 1
        other = conn.execute(
            "UPDATE vt.app_users SET display_name = 'X' WHERE id = %s", (world.a_admin,)
        )
        assert other.rowcount == 0
        with pytest.raises(errors.InsufficientPrivilege):  # not an updatable column
            conn.execute(
                "UPDATE vt.app_users SET status = 'active' WHERE id = %s", (world.a_analyst,)
            )
        conn.rollback()
        begin(conn, world.a_analyst)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE vt.app_users SET email = 'x@example.invalid' WHERE id = %s",
                (world.a_analyst,),
            )


def test_staff_see_their_organizations_people_and_nobody_elses(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_reviewer)
        seen = ids(conn, "SELECT id FROM vt.app_users")
        assert world.a_admin in seen and world.a_reviewer in seen and world.multi in seen
        assert world.b_admin not in seen and world.b_analyst not in seen
        assert world.outsider not in seen


def test_a_user_with_two_memberships_must_choose_and_each_role_applies(world: World) -> None:
    with app(world.db) as conn:
        with pytest.raises(psycopg.Error) as info:
            begin(conn, world.multi)
        assert info.value.sqlstate == "VT002"
        conn.rollback()
        begin(conn, world.multi, world.org_a)  # analyst in A: writes
        assert vendor_ids(conn) == world.a_vendors
        assert (
            conn.execute(
                "UPDATE vt.vendors SET name = 'a' WHERE id = %s", (world.vendor_a1,)
            ).rowcount
            == 1
        )
        conn.rollback()
        begin(conn, world.multi, world.org_b)  # auditor in B: read-only, and sees only B
        assert vendor_ids(conn) == world.b_vendors
        assert conn.execute("UPDATE vt.vendors SET name = 'b'").rowcount == 0
        assert vendor_ids(conn).isdisjoint(world.a_vendors)


def test_a_membership_cannot_point_at_another_organizations_vendor(world: World) -> None:
    with connect(SUPERUSER_ROLE, world.db) as su, pytest.raises(errors.ForeignKeyViolation):
        su.execute(
            "INSERT INTO vt.memberships (org_id, user_id, role, vendor_id) "
            "VALUES (%s, %s, 'vendor_user', %s)",
            (world.org_a, world.outsider, world.vendor_b1),
        )


def test_database_constraints_reject_inconsistent_memberships(world: World) -> None:
    with connect(SUPERUSER_ROLE, world.db) as su:
        with pytest.raises(errors.CheckViolation):  # a vendor user needs a vendor
            su.execute(
                "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'vendor_user')",
                (world.org_a, world.outsider),
            )
        su.rollback()
        with pytest.raises(errors.CheckViolation):  # an internal role must not have one
            su.execute(
                "INSERT INTO vt.memberships (org_id, user_id, role, vendor_id) "
                "VALUES (%s, %s, 'analyst', %s)",
                (world.org_a, world.outsider, world.vendor_a1),
            )
        su.rollback()
        with pytest.raises(errors.UniqueViolation):  # one live membership per user and org
            su.execute(
                "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'auditor')",
                (world.org_a, world.a_analyst),
            )


def test_audit_log_is_append_only_for_the_application_role(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        conn.execute(
            "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type) "
            "VALUES (%s, %s, 'analyst', 'vendor.create', 'vendor')",
            (world.org_a, world.a_analyst),
        )
        # an event about someone else, from another organization or in another role is refused
        for org, actor, role in (
            (world.org_a, world.a_admin, "analyst"),
            (world.org_b, world.a_analyst, "analyst"),
            (world.org_a, world.a_analyst, "org_admin"),
        ):
            conn.rollback()
            begin(conn, world.a_analyst)
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type) "
                    "VALUES (%s, %s, %s, 'x', 'y')",
                    (org, actor, role),
                )
        conn.rollback()
    with app(world.db) as conn:
        begin(conn, world.a_admin)  # admins and auditors read; nobody updates or deletes
        conn.execute(
            "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type) "
            "VALUES (%s, %s, 'org_admin', 'a', 'b')",
            (world.org_a, world.a_admin),
        )
        assert (
            count(conn, "audit_log") == 2
        )  # the seeded event and the one just written, org A only
        for statement in (
            "UPDATE vt.audit_log SET action = 'x'",
            "DELETE FROM vt.audit_log",
            "TRUNCATE vt.audit_log",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(statement)
            conn.rollback()
            begin(conn, world.a_admin)
        # nor can the caller supply the identity column
        with pytest.raises(psycopg.Error):
            conn.execute(
                "INSERT INTO vt.audit_log (id, org_id, actor_user_id, actor_role, action, entity_type) "
                "VALUES (1, %s, %s, 'org_admin', 'a', 'b')",
                (world.org_a, world.a_admin),
            )


def test_audit_log_triggers_block_even_the_superuser_until_they_are_disabled(world: World) -> None:
    """Honest limits: the guard stops accidents and the app role, not an owner or superuser."""
    with app(world.db) as conn:
        begin(conn, world.a_admin)
        conn.execute(
            "INSERT INTO vt.audit_log (org_id, actor_user_id, actor_role, action, entity_type) "
            "VALUES (%s, %s, 'org_admin', 'a', 'b')",
            (world.org_a, world.a_admin),
        )
        conn.commit()
    with connect(SUPERUSER_ROLE, world.db) as su:
        for statement in ("UPDATE vt.audit_log SET action = 'x'", "DELETE FROM vt.audit_log"):
            with pytest.raises(psycopg.Error) as info:
                su.execute(statement)
            assert info.value.sqlstate == "VT004"
            su.rollback()
        # A superuser (or the table owner) can switch the trigger off and rewrite history.
        su.execute("ALTER TABLE vt.audit_log DISABLE TRIGGER trg_audit_log_no_update")
        assert su.execute("UPDATE vt.audit_log SET action = 'rewritten'").rowcount >= 1
        su.rollback()


def test_last_org_admin_cannot_be_removed_even_by_themselves(world: World) -> None:
    org = str(uuid.uuid4())
    user = str(uuid.uuid4())
    with connect(SUPERUSER_ROLE, world.db) as su:
        su.execute("INSERT INTO vt.organizations (id, name) VALUES (%s, 'solo')", (org,))
        su.execute(
            "INSERT INTO vt.app_users (id, email) VALUES (%s, 'solo@example.invalid')", (user,)
        )
        su.execute(
            "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'org_admin')",
            (org, user),
        )
    with app(world.db) as conn:
        begin(conn, user)
        for change in ("role = 'analyst'", "status = 'suspended'", "status = 'revoked'"):
            with pytest.raises(psycopg.Error) as info:
                conn.execute(f"UPDATE vt.memberships SET {change} WHERE user_id = %s", (user,))
            assert info.value.sqlstate == "VT003"
            conn.rollback()
            begin(conn, user)
    with connect(SUPERUSER_ROLE, world.db) as su:  # with a second admin, demoting one is fine
        su.execute(
            "UPDATE vt.memberships SET role = 'analyst' WHERE user_id = %s", (world.a_admin2,)
        )
        with pytest.raises(psycopg.Error) as info:
            su.execute(
                "UPDATE vt.memberships SET role = 'analyst' WHERE user_id = %s", (world.a_admin,)
            )
        assert info.value.sqlstate == "VT003"
