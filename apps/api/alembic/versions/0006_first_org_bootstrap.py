"""One-time first-organization bootstrap, enforced by the database.

The migration role (vt_migrator) is read-only on tenant tables because FORCE ROW LEVEL SECURITY
binds the owner. The first organization still has to come from somewhere, and the application role
must never be able to create organizations. So the operator CLI (scripts/bootstrap_org.py, run with
the migration credential, never exposed as an endpoint) gets exactly four INSERT policies, and each
is open ONLY while the bootstrap has not happened:

* a first user row (or none, if first login already provisioned it) and the first organization,
  only while NO organization exists;
* one active `org_admin` membership, only while exactly one organization and no membership exist;
* one `bootstrap.first_organization` audit event, only while exactly one organization exists and no
  such event does (a narrow read policy lets the check see that event and nothing else).

After that every policy's condition is false: the migration role cannot create a second
organization, membership or bootstrap event, whatever the script does. The helper functions are
plain SECURITY INVOKER SQL (run as vt_migrator under its own policies), executable by vt_migrator
only, so they add no definer surface and cannot recurse through RLS.

Revision ID: 0006_first_org_bootstrap
Revises: 0005_questionnaire_rls_triggers
"""

from alembic import op

revision = "0006_first_org_bootstrap"
down_revision = "0005_questionnaire_rls_triggers"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE FUNCTION vt.bootstrap_is_open() RETURNS boolean
    LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$
    SELECT NOT EXISTS (SELECT 1 FROM vt.organizations)
$$;

CREATE FUNCTION vt.bootstrap_membership_open() RETURNS boolean
    LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$
    SELECT (SELECT count(*) FROM vt.organizations) = 1
       AND NOT EXISTS (SELECT 1 FROM vt.memberships)
$$;

CREATE FUNCTION vt.bootstrap_audit_open() RETURNS boolean
    LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$
    SELECT (SELECT count(*) FROM vt.organizations) = 1
       AND NOT EXISTS (SELECT 1 FROM vt.audit_log WHERE action = 'bootstrap.first_organization')
$$;

REVOKE ALL ON FUNCTION
    vt.bootstrap_is_open(), vt.bootstrap_membership_open(), vt.bootstrap_audit_open()
    FROM PUBLIC;

CREATE POLICY organizations_bootstrap_insert ON vt.organizations FOR INSERT TO vt_migrator
    WITH CHECK (vt.bootstrap_is_open() AND status = 'active');
CREATE POLICY app_users_bootstrap_insert ON vt.app_users FOR INSERT TO vt_migrator
    WITH CHECK (vt.bootstrap_is_open() AND status = 'active');
CREATE POLICY memberships_bootstrap_insert ON vt.memberships FOR INSERT TO vt_migrator
    WITH CHECK (role = 'org_admin' AND status = 'active' AND vendor_id IS NULL
                AND vt.bootstrap_membership_open());
CREATE POLICY audit_log_bootstrap_read ON vt.audit_log FOR SELECT TO vt_migrator
    USING (action = 'bootstrap.first_organization');
CREATE POLICY audit_log_bootstrap_insert ON vt.audit_log FOR INSERT TO vt_migrator
    WITH CHECK (action = 'bootstrap.first_organization' AND actor_role = 'org_admin'
                AND vt.bootstrap_audit_open());
"""

DOWNGRADE = """
DROP POLICY audit_log_bootstrap_insert ON vt.audit_log;
DROP POLICY audit_log_bootstrap_read ON vt.audit_log;
DROP POLICY memberships_bootstrap_insert ON vt.memberships;
DROP POLICY app_users_bootstrap_insert ON vt.app_users;
DROP POLICY organizations_bootstrap_insert ON vt.organizations;
DROP FUNCTION vt.bootstrap_audit_open();
DROP FUNCTION vt.bootstrap_membership_open();
DROP FUNCTION vt.bootstrap_is_open();
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
