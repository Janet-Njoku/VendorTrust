"""Request context: claim readers, the membership check and begin_request.

The database cannot verify a JWT, so `vt.claim_*` settings are CLAIMS made by the API process,
not authenticated identity. Everything below treats them that way:

* `vt.claimed_*()` read a claim, validate its shape and return NULL (fail closed) otherwise.
* `vt.claim_matches_active_membership()` is the ONLY SECURITY DEFINER function. It asks the
  database whether the claimed (user, organization, role, vendor) is a live membership of an
  active user in an active organization. No arguments, no dynamic SQL, boolean result.
  - Owner vt_migrator: not a superuser, no BYPASSRLS, and bound by FORCE ROW LEVEL SECURITY.
  - It reads memberships, app_users and organizations under the `*_ctx_read` policies, which
    apply to vt_migrator ONLY. Policies of vt_app never apply inside it, and vt_migrator's
    policies call no function, so policy recursion is impossible by construction. Nothing here
    depends on policy evaluation order or function cost.
* `vt.begin_request()` is SECURITY INVOKER: it derives org, role and vendor from the user's own
  membership row (never from the caller), then confirms them with the definer function.

Revision ID: 0002_request_context
Revises: 0001_core_tables
"""

from alembic import op

revision = "0002_request_context"
down_revision = "0001_core_tables"
branch_labels = None
depends_on = None

UUID_RE = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

UPGRADE = f"""
-- Claim readers. SECURITY INVOKER, pinned search_path, NULL when missing or malformed.
CREATE FUNCTION vt.claimed_user_id() RETURNS uuid
    LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$
    SELECT CASE WHEN s ~ '{UUID_RE}' THEN s::uuid END
    FROM (SELECT nullif(pg_catalog.current_setting('vt.claim_user_id', true), '') AS s) q
$$;

CREATE FUNCTION vt.claimed_org_id() RETURNS uuid
    LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$
    SELECT CASE WHEN s ~ '{UUID_RE}' THEN s::uuid END
    FROM (SELECT nullif(pg_catalog.current_setting('vt.claim_org_id', true), '') AS s) q
$$;

CREATE FUNCTION vt.claimed_vendor_id() RETURNS uuid
    LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$
    SELECT CASE WHEN s ~ '{UUID_RE}' THEN s::uuid END
    FROM (SELECT nullif(pg_catalog.current_setting('vt.claim_vendor_id', true), '') AS s) q
$$;

CREATE FUNCTION vt.claimed_role() RETURNS text
    LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$
    SELECT CASE WHEN s IN ('org_admin', 'analyst', 'reviewer', 'auditor', 'vendor_user')
                THEN s END
    FROM (SELECT nullif(pg_catalog.current_setting('vt.claim_role', true), '') AS s) q
$$;

-- The one SECURITY DEFINER function (owner: the migration role running this migration).
CREATE FUNCTION vt.claim_matches_active_membership() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM vt.memberships m
        JOIN vt.app_users u     ON u.id = m.user_id AND u.status = 'active'
        JOIN vt.organizations o ON o.id = m.org_id  AND o.status = 'active'
        WHERE m.status = 'active'
          AND m.user_id = vt.claimed_user_id()
          AND m.org_id  = vt.claimed_org_id()
          AND m.role    = vt.claimed_role()
          AND m.vendor_id IS NOT DISTINCT FROM vt.claimed_vendor_id()
    )
$$;

CREATE FUNCTION vt.clear_claims() RETURNS void
    LANGUAGE sql SET search_path = pg_catalog, pg_temp
AS $$
    SELECT pg_catalog.set_config('vt.claim_user_id', '', true),
           pg_catalog.set_config('vt.claim_org_id', '', true),
           pg_catalog.set_config('vt.claim_role', '', true),
           pg_catalog.set_config('vt.claim_vendor_id', '', true)
$$;

-- Resolve a request context for a user the API has authenticated. Transaction-local.
-- VT001 access denied (no live membership; same answer for "not a member" and "no such org"),
-- VT002 the user has several memberships and must choose an organization.
CREATE FUNCTION vt.begin_request(p_user_id uuid, p_org_id uuid DEFAULT NULL) RETURNS void
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_count  integer;
    v_org    uuid;
    v_role   text;
    v_vendor uuid;
BEGIN
    PERFORM vt.clear_claims();
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'vt: access denied' USING ERRCODE = 'VT001';
    END IF;
    PERFORM pg_catalog.set_config('vt.claim_user_id', p_user_id::text, true);

    -- Only the user's OWN membership rows are visible at this point (memberships_select_self).
    SELECT count(*) INTO v_count
    FROM vt.memberships m
    WHERE m.user_id = p_user_id AND m.status = 'active'
      AND (p_org_id IS NULL OR m.org_id = p_org_id);
    IF v_count = 0 THEN
        PERFORM vt.clear_claims();
        RAISE EXCEPTION 'vt: access denied' USING ERRCODE = 'VT001';
    ELSIF v_count > 1 THEN
        PERFORM vt.clear_claims();
        RAISE EXCEPTION 'vt: organization required' USING ERRCODE = 'VT002';
    END IF;

    SELECT m.org_id, m.role, m.vendor_id INTO v_org, v_role, v_vendor
    FROM vt.memberships m
    WHERE m.user_id = p_user_id AND m.status = 'active'
      AND (p_org_id IS NULL OR m.org_id = p_org_id);

    PERFORM pg_catalog.set_config('vt.claim_org_id', v_org::text, true);
    PERFORM pg_catalog.set_config('vt.claim_role', v_role, true);
    PERFORM pg_catalog.set_config('vt.claim_vendor_id', coalesce(v_vendor::text, ''), true);

    -- Confirms user active, organization active and membership live, in one definer call.
    IF NOT vt.claim_matches_active_membership() THEN
        PERFORM vt.clear_claims();
        RAISE EXCEPTION 'vt: access denied' USING ERRCODE = 'VT001';
    END IF;
END
$$;

-- Trigger functions (SECURITY INVOKER; see 0003 for the triggers).
CREATE FUNCTION vt.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    NEW.updated_at := pg_catalog.now();
    RETURN NEW;
END
$$;

CREATE FUNCTION vt.guard_last_org_admin() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF OLD.role = 'org_admin' AND OLD.status = 'active'
       AND (NEW.role <> 'org_admin' OR NEW.status <> 'active') THEN
        -- serialize concurrent demotions in one organization (READ COMMITTED sees the winner)
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('vt.last_admin.' || OLD.org_id::text, 0));
        IF NOT EXISTS (
            SELECT 1 FROM vt.memberships m
            WHERE m.org_id = OLD.org_id AND m.id <> OLD.id
              AND m.role = 'org_admin' AND m.status = 'active'
        ) THEN
            RAISE EXCEPTION 'vt: an organization must keep one active org_admin'
                USING ERRCODE = 'VT003';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION vt.audit_log_append_only() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'vt: audit_log is append-only' USING ERRCODE = 'VT004';
END
$$;

-- Functions are executable by PUBLIC unless revoked: close that, then grant explicitly.
REVOKE ALL ON FUNCTION
    vt.claimed_user_id(), vt.claimed_org_id(), vt.claimed_vendor_id(), vt.claimed_role(),
    vt.claim_matches_active_membership(), vt.clear_claims(), vt.begin_request(uuid, uuid),
    vt.set_updated_at(), vt.guard_last_org_admin(), vt.audit_log_append_only()
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    vt.claimed_user_id(), vt.claimed_org_id(), vt.claimed_vendor_id(), vt.claimed_role(),
    vt.claim_matches_active_membership(), vt.clear_claims(), vt.begin_request(uuid, uuid)
    TO vt_app;
"""

DOWNGRADE = """
DROP FUNCTION vt.audit_log_append_only();
DROP FUNCTION vt.guard_last_org_admin();
DROP FUNCTION vt.set_updated_at();
DROP FUNCTION vt.begin_request(uuid, uuid);
DROP FUNCTION vt.clear_claims();
DROP FUNCTION vt.claim_matches_active_membership();
DROP FUNCTION vt.claimed_role();
DROP FUNCTION vt.claimed_vendor_id();
DROP FUNCTION vt.claimed_org_id();
DROP FUNCTION vt.claimed_user_id();
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
