"""Forged claims, suspended or revoked memberships, begin_request, and context cleanup."""

from __future__ import annotations

import random
import threading
import uuid

import psycopg
import pytest
from psycopg import IsolationLevel

from tests.db.conftest import SUPERUSER_ROLE, connect
from tests.db.rls_world import ALL_TABLES, World, app, begin, count, forge, ids, seed

pytestmark = pytest.mark.db

TABLES = ALL_TABLES


@pytest.fixture
def world(migrated_db: str) -> World:
    return seed(migrated_db)


def vendors(conn: psycopg.Connection) -> set[str]:
    return ids(conn, "SELECT id FROM vt.vendors")


def su_exec(world: World, sql: str, *params: object) -> None:
    with connect(SUPERUSER_ROLE, world.db) as su:
        su.execute(sql, params)


# --- forged claims (raw set_config, no begin_request) -----------------------------------------


def test_a_real_user_claiming_another_organization_gets_nothing(world: World) -> None:
    with app(world.db) as conn:
        forge(conn, world.a_admin, world.org_b, "org_admin")
        assert vendors(conn) == set()
        assert count(conn, "organizations") == 0
        assert count(conn, "audit_log") == 0
        # the only thing a bare user claim ever shows is that user's own rows
        assert ids(conn, "SELECT user_id FROM vt.memberships") == {world.a_admin}
        assert ids(conn, "SELECT id FROM vt.app_users") == {world.a_admin}


def test_claiming_a_higher_role_than_the_membership_has_is_refused(world: World) -> None:
    with app(world.db) as conn:
        forge(conn, world.a_analyst, world.org_a, "org_admin")
        assert vendors(conn) == set()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO vt.memberships (org_id, user_id, role) VALUES (%s, %s, 'org_admin')",
                (world.org_a, world.outsider),
            )


def test_claiming_a_role_that_does_not_exist_is_refused(world: World) -> None:
    with app(world.db) as conn:
        forge(conn, world.a_admin, world.org_a, "superuser")
        assert vendors(conn) == set()


@pytest.mark.parametrize(
    ("claimed", "vendor"),
    [
        ("vendor_user", "vendor_a2"),  # a vendor user claiming a vendor they are not bound to
        ("vendor_user", None),  # ... or no vendor at all
        ("analyst", "vendor_a1"),  # a staff role claiming a vendor scope
    ],
)
def test_vendor_scope_claims_must_match_the_membership(
    world: World, claimed: str, vendor: str | None
) -> None:
    user = world.a_vendor if claimed == "vendor_user" else world.a_analyst
    with app(world.db) as conn:
        forge(conn, user, world.org_a, claimed, getattr(world, vendor) if vendor else None)
        assert vendors(conn) == set()


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-uuid",
        "'; DROP TABLE vt.vendors; --",
        "00000000-0000-0000-0000-00000000000g",
        "0" * 500,
        "ÄÖÜ-‮-\x7f",
        "{12345678-1234-1234-1234-123456789abc}",
    ],
)
def test_malformed_claims_match_nothing_and_never_raise(world: World, bad: str) -> None:
    with app(world.db) as conn:
        forge(conn, bad, bad, bad, bad)
        for table in TABLES:
            assert count(conn, table) == 0
    with app(world.db) as conn:
        forge(conn, world.a_admin, bad, "org_admin")
        assert vendors(conn) == set()
    with app(world.db) as conn:
        forge(conn, bad, world.org_a, "org_admin")
        assert vendors(conn) == set()


@pytest.mark.parametrize("which", ["user", "org", "vendor"])
def test_uppercase_uuid_claims_are_not_accepted(world: World, which: str) -> None:
    user, org, vendor = world.a_vendor, world.org_a, world.vendor_a1
    with app(world.db) as conn:  # the exact claims are accepted ...
        forge(conn, user, org, "vendor_user", vendor)
        assert vendors(conn) == {world.vendor_a1}
    with app(world.db) as conn:  # ... but a single uppercase value is not
        forge(
            conn,
            user.upper() if which == "user" else user,
            org.upper() if which == "org" else org,
            "vendor_user",
            vendor.upper() if which == "vendor" else vendor,
        )
        assert vendors(conn) == set()


def test_an_unknown_user_gets_nothing(world: World) -> None:
    with app(world.db) as conn:
        forge(conn, str(uuid.uuid4()), world.org_a, "org_admin")
        assert vendors(conn) == set()


@pytest.mark.parametrize("status", ["suspended", "revoked"])
def test_a_suspended_or_revoked_membership_loses_access(world: World, status: str) -> None:
    su_exec(
        world,
        "UPDATE vt.memberships SET status = %s WHERE id = %s",
        status,
        world.mem[world.a_analyst],
    )
    with app(world.db) as conn:
        forge(conn, world.a_analyst, world.org_a, "analyst")  # the claims were once correct
        assert vendors(conn) == set()
        conn.rollback()
        with pytest.raises(psycopg.Error) as info:
            begin(conn, world.a_analyst)
        assert info.value.sqlstate == "VT001"


def test_a_disabled_user_loses_access_even_with_a_live_membership(world: World) -> None:
    with app(world.db) as conn:
        forge(conn, world.disabled, world.org_a, "analyst")
        assert vendors(conn) == set()
        conn.rollback()
        with pytest.raises(psycopg.Error) as info:
            begin(conn, world.disabled)
        assert info.value.sqlstate == "VT001"


def test_a_suspended_organization_locks_everyone_out(world: World) -> None:
    su_exec(world, "UPDATE vt.organizations SET status = 'suspended' WHERE id = %s", world.org_b)
    with app(world.db) as conn:
        forge(conn, world.b_admin, world.org_b, "org_admin")
        assert vendors(conn) == set()
        assert count(conn, "organizations") == 0
        conn.rollback()
        with pytest.raises(psycopg.Error) as info:
            begin(conn, world.b_admin)
        assert info.value.sqlstate == "VT001"
    with app(world.db) as conn:  # the other organization is unaffected
        begin(conn, world.a_admin)
        assert vendors(conn) == world.a_vendors


# --- begin_request ------------------------------------------------------------------------------


def test_begin_request_derives_role_and_vendor_from_the_database(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_vendor)
        row = conn.execute(
            "SELECT current_setting('vt.claim_role'), current_setting('vt.claim_vendor_id'), "
            "current_setting('vt.claim_org_id')"
        ).fetchone()
        assert row == ("vendor_user", world.vendor_a1, world.org_a)


@pytest.mark.parametrize("case", ["not_a_member", "no_such_org", "no_such_user", "null_user"])
def test_denials_look_identical_so_they_cannot_be_used_to_probe(world: World, case: str) -> None:
    args = {
        "not_a_member": (world.a_admin, world.org_b),
        "no_such_org": (world.a_admin, str(uuid.uuid4())),
        "no_such_user": (str(uuid.uuid4()), world.org_a),
        "null_user": (None, world.org_a),
    }[case]
    with app(world.db) as conn, pytest.raises(psycopg.Error) as info:
        conn.execute("SELECT vt.begin_request(%s::uuid, %s::uuid)", args)
    assert info.value.sqlstate == "VT001"
    assert "access denied" in str(info.value)


def test_a_user_without_a_membership_gets_no_context(world: World) -> None:
    with app(world.db) as conn, pytest.raises(psycopg.Error) as info:
        begin(conn, world.outsider)
    assert info.value.sqlstate == "VT001"


def test_begin_request_cannot_be_used_to_borrow_another_users_membership(world: World) -> None:
    with app(world.db) as conn, pytest.raises(psycopg.Error):
        begin(conn, world.a_analyst, world.org_b)  # the analyst has no membership in B


def test_a_failed_begin_request_leaves_no_usable_claims(world: World) -> None:
    with app(world.db) as conn:
        conn.execute("SAVEPOINT s")
        with pytest.raises(psycopg.Error):
            begin(conn, world.outsider)
        conn.execute("ROLLBACK TO SAVEPOINT s")
        assert vendors(conn) == set()
        assert conn.execute("SELECT current_setting('vt.claim_org_id', true)").fetchone() in {
            ("",),
            (None,),
        }


# --- revocation during a request ----------------------------------------------------------------


def test_revocation_applies_to_the_next_statement_of_an_open_transaction(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_analyst)
        assert vendors(conn) == world.a_vendors
        su_exec(
            world,
            "UPDATE vt.memberships SET status = 'suspended' WHERE id = %s",
            world.mem[world.a_analyst],
        )
        assert vendors(conn) == set()  # READ COMMITTED: the very next statement sees it


def test_under_repeatable_read_the_snapshot_stays_stale_until_the_transaction_ends(
    world: World,
) -> None:
    """Documented limit: use READ COMMITTED (the default) when immediate revocation matters."""
    with app(world.db) as conn:
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        begin(conn, world.a_analyst)
        assert vendors(conn) == world.a_vendors
        su_exec(
            world,
            "UPDATE vt.memberships SET status = 'suspended' WHERE id = %s",
            world.mem[world.a_analyst],
        )
        assert vendors(conn) == world.a_vendors
        conn.rollback()
        with pytest.raises(psycopg.Error):
            begin(conn, world.a_analyst)


# --- context cleanup ------------------------------------------------------------------------------


def test_context_does_not_survive_commit_or_rollback_on_a_reused_connection(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_admin)
        assert vendors(conn) == world.a_vendors
        conn.commit()
        assert vendors(conn) == set()  # a new transaction, no begin_request
        begin(conn, world.a_admin)
        assert vendors(conn) == world.a_vendors
        conn.rollback()
        assert vendors(conn) == set()


def test_context_does_not_survive_an_error_or_an_aborted_transaction(world: World) -> None:
    with app(world.db) as conn:
        begin(conn, world.a_admin)
        with pytest.raises(psycopg.Error):
            conn.execute("SELECT 1/0")
        conn.rollback()
        assert vendors(conn) == set()
        begin(conn, world.a_admin)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM vt.vendors")
        conn.rollback()
        assert vendors(conn) == set()


def test_context_set_inside_a_savepoint_is_reverted_with_it(world: World) -> None:
    with app(world.db) as conn:
        conn.execute("SAVEPOINT s")
        begin(conn, world.a_admin)
        assert vendors(conn) == world.a_vendors
        conn.execute("ROLLBACK TO SAVEPOINT s")
        assert vendors(conn) == set()


def test_in_autocommit_mode_the_context_cannot_outlive_its_statement(world: World) -> None:
    with app(world.db, autocommit=True) as conn:
        begin(conn, world.a_admin)  # its own one-statement transaction
        assert vendors(conn) == set()
        with conn.transaction():  # an explicit transaction is the way to use it
            begin(conn, world.a_admin)
            assert vendors(conn) == world.a_vendors
        assert vendors(conn) == set()


def test_session_level_settings_leak_between_transactions_so_the_api_must_never_use_them(
    world: World,
) -> None:
    """NEGATIVE CONTROL: shows why `set_config(..., true)` is mandatory (see trust-boundary.md).

    A session-level claim survives COMMIT on a pooled connection. Only DISCARD ALL / RESET ALL
    removes it, which is why the API sets context solely through vt.begin_request().
    """
    with app(world.db) as conn:
        forge(conn, world.a_admin, world.org_a, "org_admin", local=False)
        conn.commit()
        assert vendors(conn) == world.a_vendors  # leaked into the next transaction
        conn.commit()
        conn.execute("RESET ALL")
        conn.commit()
        assert vendors(conn) == set()
    with app(world.db, autocommit=True) as conn:  # DISCARD cannot run inside a transaction block
        forge(conn, world.a_admin, world.org_a, "org_admin", local=False)
        conn.execute("DISCARD ALL")
        with conn.transaction():
            assert vendors(conn) == set()


def test_a_reused_connection_never_shows_one_request_another_requests_data(world: World) -> None:
    rng = random.Random(1234)
    people = {
        world.a_admin: world.a_vendors,
        world.a_vendor: {world.vendor_a1},
        world.b_admin: world.b_vendors,
        world.b_analyst: world.b_vendors,
        world.outsider: None,  # no membership: begin_request fails
        world.disabled: None,
    }
    with app(world.db) as conn:
        for _ in range(120):
            user = rng.choice(list(people))
            expected = people[user]
            if expected is None:
                with pytest.raises(psycopg.Error):
                    begin(conn, user)
                conn.rollback()
                assert vendors(conn) == set()
            else:
                begin(conn, user)
                assert vendors(conn) == expected
            (conn.commit if rng.random() < 0.5 else conn.rollback)()
            assert vendors(conn) == set()
            conn.rollback()


def test_concurrent_connections_do_not_see_each_others_context(world: World) -> None:
    expectations = [
        (world.a_admin, world.a_vendors),
        (world.b_admin, world.b_vendors),
        (world.a_vendor, {world.vendor_a1}),
        (world.b_analyst, world.b_vendors),
    ]
    failures: list[str] = []

    def worker(user: str, expected: set[str]) -> None:
        try:
            with app(world.db) as conn:
                for _ in range(40):
                    begin(conn, user)
                    if vendors(conn) != expected:
                        failures.append(user)
                    conn.rollback()
        except Exception as exc:
            failures.append(repr(exc))

    threads = [threading.Thread(target=worker, args=item) for item in expectations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []


def test_a_prepared_statement_is_re_evaluated_for_each_context(world: World) -> None:
    with app(world.db) as conn:
        conn.prepare_threshold = 0  # prepare on first use
        query = "SELECT id FROM vt.vendors ORDER BY name"
        seen = []
        for user in (world.a_admin, world.b_admin, world.a_admin, world.b_admin, world.outsider):
            try:
                begin(conn, user)
            except psycopg.Error:
                conn.rollback()
            seen.append({str(r[0]) for r in conn.execute(query, prepare=True).fetchall()})
            conn.rollback()
        assert seen == [world.a_vendors, world.b_vendors, world.a_vendors, world.b_vendors, set()]
