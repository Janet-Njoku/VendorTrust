"""RLS recursion safety: structural, and independent of policy order and function cost."""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest

from tests.db.conftest import connect
from tests.db.rls_world import ALL_TABLES, World, app, begin, count, forge, ids, seed

pytestmark = pytest.mark.db

TABLES = ALL_TABLES


def policies(db: str) -> list[tuple[str, str, str, str, str | None, str | None]]:
    with connect("vt_migrator", db) as conn:
        return conn.execute(
            "SELECT tablename, policyname, roles::text, cmd, qual, with_check FROM pg_policies "
            "WHERE schemaname = 'vt' ORDER BY tablename, policyname"
        ).fetchall()


def test_the_policy_graph_has_no_cycle_by_construction(migrated_db: str) -> None:
    """Checked from the catalog, not by trying queries and hoping none recurse."""
    rows = policies(migrated_db)
    assert rows
    for table, name, roles, _cmd, qual, check in rows:
        expression = f"{qual or ''} {check or ''}"
        if roles == "{vt_migrator}":
            if name.endswith("_bootstrap_insert"):
                # one-time first-organization bootstrap: INSERT only, gated by invoker helpers
                assert _cmd == "INSERT" and qual is None, name
                assert "bootstrap_" in (check or "") or table == "audit_log", name
                assert "SELECT" not in (check or ""), name  # no table subquery: no recursion
            elif name == "audit_log_bootstrap_read":
                assert _cmd == "SELECT" and "bootstrap.first_organization" in (qual or ""), name
            else:
                # The definer function's policies: unconditional read, calling nothing.
                assert qual == "true" and check is None, name
        else:
            assert roles == "{vt_app}", f"{table}.{name} must be TO vt_app or vt_migrator only"
        if table == "memberships":
            # memberships policies never read memberships (or app_users) themselves
            assert "vt.memberships" not in expression.replace("vt.memberships_", ""), name
            assert "app_users" not in expression, name
        if table != "app_users":
            assert "FROM vt.memberships" not in expression and "FROM memberships" not in expression
        # the only table subquery anywhere: app_users -> memberships (memberships never goes back)
        subqueries = expression.count("SELECT 1")
        assert subqueries == (1 if name == "app_users_select_org_staff" else 0), name


def observe(db: str, world: World) -> dict[str, object]:
    """Row sets visible to a fixed battery of principals and forged claims."""
    result: dict[str, object] = {}
    cases = {
        "admin": (world.a_admin, None),
        "analyst": (world.a_analyst, None),
        "auditor": (world.a_auditor, None),
        "vendor": (world.a_vendor, None),
        "b_admin": (world.b_admin, None),
        "multi_b": (world.multi, world.org_b),
    }
    for label, (user, org) in cases.items():
        with app(db) as conn:
            begin(conn, user, org)
            for table in TABLES:
                result[f"{label}:{table}"] = count(conn, table)
            result[f"{label}:vendors"] = sorted(ids(conn, "SELECT id FROM vt.vendors"))
            result[f"{label}:users"] = sorted(ids(conn, "SELECT id FROM vt.app_users"))
            result[f"{label}:members"] = sorted(ids(conn, "SELECT id FROM vt.memberships"))
            joined = conn.execute(
                "SELECT count(*) FROM vt.vendors v JOIN vt.organizations o ON o.id = v.org_id "
                "JOIN vt.memberships m ON m.org_id = o.id JOIN vt.app_users u ON u.id = m.user_id"
            ).fetchone()
            result[f"{label}:join"] = joined
    with app(db) as conn:
        forge(conn, world.a_analyst, world.org_a, "org_admin")
        result["forged:vendors"] = count(conn, "vendors")
        result["forged:members"] = count(conn, "memberships")
    with app(db) as conn:
        forge(conn, world.a_admin, world.org_b, "org_admin")
        result["cross:vendors"] = count(conn, "vendors")
    with app(db) as conn:
        result["none:vendors"] = count(conn, "vendors")
    return result


def shape(result: dict[str, object], world: World) -> dict[str, object]:
    """Make results comparable across databases: replace the random ids with stable labels."""
    labels = {
        getattr(world, name): name
        for name in [
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
        ]
    }
    labels.update({v: f"membership[{labels.get(k, '?')}]" for k, v in world.mem.items()})
    labels[world.multi_b_membership] = "membership[multi@b]"

    def clean(value: object) -> object:
        if isinstance(value, list):
            return sorted(labels.get(str(item), "other") for item in value)
        return value

    return {key: clean(value) for key, value in result.items()}


def renamed_in_reverse(db: str) -> None:
    """Reverse the alphabetical order of every table's policies (the order PostgreSQL ORs them)."""
    with connect("vt_migrator", db) as conn:
        for table in TABLES:
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT policyname FROM pg_policies WHERE schemaname = 'vt' "
                    "AND tablename = %s ORDER BY policyname",
                    (table,),
                ).fetchall()
            ]
            for index, name in enumerate(reversed(names)):
                conn.execute(f'ALTER POLICY "{name}" ON vt.{table} RENAME TO "{index:02d}_{name}"')


def set_all_function_costs(db: str, cost: int) -> None:
    with connect("vt_migrator", db) as conn:
        signatures = conn.execute(
            "SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid = p.pronamespace WHERE n.nspname = 'vt'"
        ).fetchall()
        assert signatures
        for (signature,) in signatures:
            conn.execute(f"ALTER FUNCTION {signature} COST {cost}")


@pytest.mark.parametrize(
    "variant",
    ["policies_reversed", "cost_1", "cost_1000000", "reversed_and_cost_1", "reversed_and_cost_big"],
)
def test_results_do_not_depend_on_policy_order_or_function_cost(
    make_db: Callable[..., str], variant: str
) -> None:
    baseline_db = make_db()
    baseline_world = seed(baseline_db)
    baseline = shape(observe(baseline_db, baseline_world), baseline_world)
    assert baseline["admin:vendors"] == [
        "vendor_a1",
        "vendor_a2",
    ]  # the battery is not trivially empty

    db = make_db()
    if variant.startswith("reversed") or variant == "policies_reversed":
        renamed_in_reverse(db)
    if "cost_1000000" in variant or variant.endswith("cost_big"):
        set_all_function_costs(db, 1_000_000)
    elif "cost_1" in variant:
        set_all_function_costs(db, 1)
    world = seed(db)
    assert shape(observe(db, world), world) == baseline


def test_negative_control_a_self_referencing_policy_is_caught_by_postgresql(
    make_db: Callable[..., str],
) -> None:
    """Proves the structural claim is not vacuous: a naive policy would recurse and fail."""
    db = make_db()
    world = seed(db)
    with connect("vt_migrator", db) as conn:
        conn.execute(
            "CREATE POLICY naive_recursive ON vt.memberships FOR SELECT TO vt_app "
            "USING (EXISTS (SELECT 1 FROM vt.memberships m WHERE m.user_id = vt.claimed_user_id()))"
        )
    with app(db) as conn:
        begin_error = None
        try:
            forge(conn, world.a_admin, world.org_a, "org_admin")
            count(conn, "memberships")
        except psycopg.Error as exc:
            begin_error = exc
        assert begin_error is not None
        assert begin_error.sqlstate == "42P17"  # infinite recursion detected in policy


def test_without_its_read_policy_the_definer_function_fails_closed(
    make_db: Callable[..., str],
) -> None:
    db = make_db()
    world = seed(db)
    with connect("vt_migrator", db) as conn:
        conn.execute("DROP POLICY memberships_ctx_read ON vt.memberships")
    with app(db) as conn, pytest.raises(psycopg.Error) as info:
        begin(conn, world.a_admin)
    assert info.value.sqlstate == "VT001"
    with app(db) as conn:
        forge(conn, world.a_admin, world.org_a, "org_admin")
        assert count(conn, "vendors") == 0


def test_the_definer_function_is_called_once_per_statement_not_per_row(migrated_db: str) -> None:
    world = seed(migrated_db)
    with app(migrated_db) as conn:
        begin(conn, world.a_admin)
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                "EXPLAIN (COSTS OFF) SELECT * FROM vt.vendors v JOIN vt.memberships m "
                "ON m.org_id = v.org_id"
            ).fetchall()
        )
        assert "InitPlan" in plan  # evaluated once, then reused for every row
        assert "SubPlan" not in plan  # a SubPlan would run once per row
