"""Row level security (ENABLE + FORCE), policies, explicit grants and triggers.

Policy design, stated so that nothing depends on evaluation order or function cost:

* Every policy is `TO vt_app` or `TO vt_migrator`. Never PUBLIC.
* vt_migrator (the owner, bound by FORCE) gets ONE read policy per table the definer function
  needs, `USING (true)`, and calls no function. The definer function runs as vt_migrator, so it
  only ever meets those policies. vt_app's policies (which do call the function) never apply
  inside it. The policy graph therefore has no cycle, whatever order policies run in.
* `memberships` policies reference only claim readers and the definer function, never
  `memberships` itself and never `app_users`.
* A claim alone grants nothing beyond the user's own rows: everything else also requires the
  definer function to confirm a live membership.
* vt_app gets column-level INSERT/UPDATE and no DELETE, TRUNCATE, REFERENCES or TRIGGER.

Revision ID: 0003_rls_grants_triggers
Revises: 0002_request_context
"""

from alembic import op

revision = "0003_rls_grants_triggers"
down_revision = "0002_request_context"
branch_labels = None
depends_on = None

TABLES = ("organizations", "app_users", "vendors", "memberships", "audit_log")
STAFF = "('org_admin', 'analyst', 'reviewer', 'auditor')"
LIVE = "(SELECT vt.claim_matches_active_membership())"

UPGRADE = f"""
ALTER TABLE vt.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE vt.organizations FORCE ROW LEVEL SECURITY;
ALTER TABLE vt.app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE vt.app_users FORCE ROW LEVEL SECURITY;
ALTER TABLE vt.vendors ENABLE ROW LEVEL SECURITY;
ALTER TABLE vt.vendors FORCE ROW LEVEL SECURITY;
ALTER TABLE vt.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE vt.memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE vt.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE vt.audit_log FORCE ROW LEVEL SECURITY;

-- Read access for the owner role, used only inside vt.claim_matches_active_membership().
CREATE POLICY organizations_ctx_read ON vt.organizations FOR SELECT TO vt_migrator USING (true);
CREATE POLICY app_users_ctx_read ON vt.app_users FOR SELECT TO vt_migrator USING (true);
CREATE POLICY memberships_ctx_read ON vt.memberships FOR SELECT TO vt_migrator USING (true);

-- organizations: read-only for members, and only the active organization.
CREATE POLICY organizations_select_member ON vt.organizations FOR SELECT TO vt_app
    USING (id = vt.claimed_org_id() AND {LIVE});

-- app_users: yourself; staff also see people who hold a membership in their organization.
CREATE POLICY app_users_select_self ON vt.app_users FOR SELECT TO vt_app
    USING (id = vt.claimed_user_id());
CREATE POLICY app_users_select_org_staff ON vt.app_users FOR SELECT TO vt_app
    USING (vt.claimed_role() IN {STAFF} AND {LIVE}
           AND EXISTS (SELECT 1 FROM vt.memberships m
                       WHERE m.user_id = app_users.id AND m.org_id = vt.claimed_org_id()));
CREATE POLICY app_users_insert_self ON vt.app_users FOR INSERT TO vt_app
    WITH CHECK (id = vt.claimed_user_id() AND status = 'active');
CREATE POLICY app_users_update_self ON vt.app_users FOR UPDATE TO vt_app
    USING (id = vt.claimed_user_id() AND status = 'active')
    WITH CHECK (id = vt.claimed_user_id() AND status = 'active');

-- memberships: your own rows (needed to resolve a context), staff read their organization,
-- only an org_admin writes. No reference to memberships or app_users here.
CREATE POLICY memberships_select_self ON vt.memberships FOR SELECT TO vt_app
    USING (user_id = vt.claimed_user_id());
CREATE POLICY memberships_select_staff ON vt.memberships FOR SELECT TO vt_app
    USING (org_id = vt.claimed_org_id() AND vt.claimed_role() IN {STAFF} AND {LIVE});
CREATE POLICY memberships_insert_admin ON vt.memberships FOR INSERT TO vt_app
    WITH CHECK (org_id = vt.claimed_org_id() AND vt.claimed_role() = 'org_admin' AND {LIVE});
CREATE POLICY memberships_update_admin ON vt.memberships FOR UPDATE TO vt_app
    USING (org_id = vt.claimed_org_id() AND vt.claimed_role() = 'org_admin' AND {LIVE})
    WITH CHECK (org_id = vt.claimed_org_id() AND vt.claimed_role() = 'org_admin' AND {LIVE});

-- vendors: staff see the organization's vendors, a vendor user only their own.
CREATE POLICY vendors_select ON vt.vendors FOR SELECT TO vt_app
    USING (org_id = vt.claimed_org_id() AND {LIVE}
           AND (vt.claimed_role() IN {STAFF} OR id = vt.claimed_vendor_id()));
CREATE POLICY vendors_insert ON vt.vendors FOR INSERT TO vt_app
    WITH CHECK (org_id = vt.claimed_org_id()
                AND vt.claimed_role() IN ('org_admin', 'analyst') AND {LIVE});
CREATE POLICY vendors_update ON vt.vendors FOR UPDATE TO vt_app
    USING (org_id = vt.claimed_org_id()
           AND vt.claimed_role() IN ('org_admin', 'analyst') AND {LIVE})
    WITH CHECK (org_id = vt.claimed_org_id()
                AND vt.claimed_role() IN ('org_admin', 'analyst') AND {LIVE});

-- audit_log: any member appends events about themselves; only admins and auditors read.
CREATE POLICY audit_log_select ON vt.audit_log FOR SELECT TO vt_app
    USING (org_id = vt.claimed_org_id()
           AND vt.claimed_role() IN ('org_admin', 'auditor') AND {LIVE});
CREATE POLICY audit_log_insert ON vt.audit_log FOR INSERT TO vt_app
    WITH CHECK (org_id = vt.claimed_org_id() AND actor_user_id = vt.claimed_user_id()
                AND actor_role = vt.claimed_role() AND {LIVE});

-- Explicit grants (vt_app starts with USAGE on the schema and nothing else).
GRANT SELECT ON vt.organizations TO vt_app;
GRANT SELECT, INSERT (id, email, display_name), UPDATE (display_name) ON vt.app_users TO vt_app;
GRANT SELECT, INSERT (org_id, user_id, role, vendor_id, status),
      UPDATE (role, vendor_id, status) ON vt.memberships TO vt_app;
GRANT SELECT, INSERT (org_id, name, status), UPDATE (name, status) ON vt.vendors TO vt_app;
GRANT SELECT, INSERT (org_id, actor_user_id, actor_role, action, entity_type, entity_id,
                      before_data, after_data) ON vt.audit_log TO vt_app;

-- Triggers
CREATE TRIGGER trg_organizations_updated BEFORE UPDATE ON vt.organizations
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();
CREATE TRIGGER trg_app_users_updated BEFORE UPDATE ON vt.app_users
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();
CREATE TRIGGER trg_vendors_updated BEFORE UPDATE ON vt.vendors
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();
CREATE TRIGGER trg_memberships_updated BEFORE UPDATE ON vt.memberships
    FOR EACH ROW EXECUTE FUNCTION vt.set_updated_at();
CREATE TRIGGER trg_memberships_last_admin BEFORE UPDATE ON vt.memberships
    FOR EACH ROW EXECUTE FUNCTION vt.guard_last_org_admin();
CREATE TRIGGER trg_audit_log_no_update BEFORE UPDATE OR DELETE ON vt.audit_log
    FOR EACH ROW EXECUTE FUNCTION vt.audit_log_append_only();
CREATE TRIGGER trg_audit_log_no_truncate BEFORE TRUNCATE ON vt.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION vt.audit_log_append_only();
"""


def _downgrade_sql() -> str:
    drops = [
        "DROP TRIGGER trg_audit_log_no_truncate ON vt.audit_log;",
        "DROP TRIGGER trg_audit_log_no_update ON vt.audit_log;",
        "DROP TRIGGER trg_memberships_last_admin ON vt.memberships;",
        "DROP TRIGGER trg_memberships_updated ON vt.memberships;",
        "DROP TRIGGER trg_vendors_updated ON vt.vendors;",
        "DROP TRIGGER trg_app_users_updated ON vt.app_users;",
        "DROP TRIGGER trg_organizations_updated ON vt.organizations;",
    ]
    drops += [f"REVOKE ALL ON vt.{table} FROM vt_app;" for table in TABLES]
    drops += [
        f"ALTER TABLE vt.{table} NO FORCE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE vt.{table} DISABLE ROW LEVEL SECURITY;"
        for table in TABLES
    ]
    return "\n".join(drops)


POLICIES = {
    "organizations": ("organizations_ctx_read", "organizations_select_member"),
    "app_users": (
        "app_users_ctx_read",
        "app_users_select_self",
        "app_users_select_org_staff",
        "app_users_insert_self",
        "app_users_update_self",
    ),
    "memberships": (
        "memberships_ctx_read",
        "memberships_select_self",
        "memberships_select_staff",
        "memberships_insert_admin",
        "memberships_update_admin",
    ),
    "vendors": ("vendors_select", "vendors_insert", "vendors_update"),
    "audit_log": ("audit_log_select", "audit_log_insert"),
}


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    for table, names in POLICIES.items():
        for name in names:
            op.execute(f"DROP POLICY {name} ON vt.{table}")
    op.execute(_downgrade_sql())
