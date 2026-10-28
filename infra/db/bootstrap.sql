-- VendorTrust database bootstrap. Idempotent and fail-safe.
--
-- Run with psql ONLY (it uses psql meta-commands; psql 13 or newer), as the administrative
-- role (Supabase: `postgres`), never from application code, CI deploys or Alembic:
--
--     psql -X -v ON_ERROR_STOP=1 -f infra/db/bootstrap.sql                # dry run: ROLLBACK (default)
--     psql -X -v ON_ERROR_STOP=1 -v commit=1 -f infra/db/bootstrap.sql    # apply: COMMIT
--
-- Procedure and safeguards: infra/db/RUNBOOK.md. Run infra/db/preflight.sql first.
--
-- Safety properties:
--   * SAFE BY DEFAULT: the script ends in ROLLBACK unless it is given exactly `-v commit=1`
--     (checked again at the moment of commit). A mistyped option name (comit, COMMIT, ...)
--     therefore rolls back instead of committing. Any other value of `commit`, and the retired
--     `dry_run` option, are rejected before a transaction is even opened.
--   * Everything runs in ONE transaction. Any error aborts the script (it sets
--     ON_ERROR_STOP itself) and PostgreSQL rolls the whole transaction back.
--   * Section 0 validates the starting state and changes NOTHING. It aborts if:
--       - schema `vt` already exists with a different owner or any foreign object or grantee
--         (the script never takes over an existing schema: there is no ALTER ... OWNER);
--       - `vt_app` / `vt_migrator` already exist but are not exactly LOGIN with no SUPERUSER,
--         CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS, are members of other roles, have
--         unexpected members, or own objects they should not.
--   * Existing role memberships are never altered. The one temporary membership needed to
--     create the schema is skipped if the admin can already SET ROLE, and otherwise is
--     granted and then revoked exactly (section 3 / 6), then verified in section 7.
--   * No passwords are set (use psql `\password`), no table privileges are granted (each
--     migration grants explicitly), and `public`/Supabase-managed schemas are untouched.

\set ON_ERROR_STOP on

-- Parameter check. Runs BEFORE `BEGIN`, so a bad parameter never opens a transaction.
-- psql defines any `-v name=value` you pass, even misspelled ones, so unknown names cannot be
-- detected here; they are made harmless by the rollback default at the end of this script.
\if :{?dry_run}
    \echo 'ERROR: -v dry_run is no longer supported. A dry run is now the default: omit the option.'
    \echo '       To apply for real, pass exactly  -v commit=1'
    DO $$ BEGIN RAISE EXCEPTION 'dry_run is no longer supported (dry run is the default; apply with -v commit=1)'; END $$;
\endif
\if :{?commit}
    SELECT (:'commit' = '1') AS commit_value_ok \gset
    \if :commit_value_ok
    \else
        \echo 'ERROR: invalid value for -v commit. It must be exactly 1, or be omitted for a dry run.'
        DO $$ BEGIN RAISE EXCEPTION 'invalid value for commit: it must be exactly 1 (or omitted for a dry run)'; END $$;
    \endif
\endif

BEGIN;

-- 0. Preconditions: validate only; nothing is modified in this section ----------------------
DO $$
DECLARE
    me      pg_roles%ROWTYPE;
    r       pg_roles%ROWTYPE;
    vt_oid  oid := to_regnamespace('vt')::oid;
    allowed oid;
    owned   bigint;
    found   text;
BEGIN
    IF current_setting('server_version_num')::int < 160000 THEN
        RAISE EXCEPTION 'PostgreSQL 16 or newer is required (found %)', current_setting('server_version');
    END IF;

    SELECT * INTO me FROM pg_roles WHERE rolname = current_user;
    IF NOT (me.rolsuper OR me.rolcreaterole) THEN
        RAISE EXCEPTION 'Admin role "%" needs CREATEROLE (or superuser) to create vt_app and vt_migrator', current_user;
    END IF;
    IF NOT (me.rolsuper OR has_database_privilege(current_user, current_database(), 'CREATE')) THEN
        RAISE EXCEPTION 'Admin role "%" needs CREATE on database "%" to create schema vt', current_user, current_database();
    END IF;

    -- Pre-existing roles must be exactly what this script would have created.
    FOR r IN SELECT * FROM pg_roles WHERE rolname IN ('vt_migrator', 'vt_app') LOOP
        IF NOT r.rolcanlogin THEN
            RAISE EXCEPTION 'Existing role "%" cannot log in (expected LOGIN); refusing to continue', r.rolname;
        END IF;
        IF r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls THEN
            RAISE EXCEPTION 'Existing role "%" has unexpected attributes (superuser=%, createrole=%, createdb=%, replication=%, bypassrls=%); refusing to continue',
                r.rolname, r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolreplication, r.rolbypassrls;
        END IF;
        IF NOT (me.rolsuper OR EXISTS (SELECT 1 FROM pg_auth_members m
                                        WHERE m.roleid = r.oid AND m.member = me.oid AND m.admin_option)) THEN
            RAISE EXCEPTION 'Admin role "%" has no ADMIN OPTION on existing role "%"; refusing to continue', current_user, r.rolname;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid) THEN
            RAISE EXCEPTION 'Existing role "%" is a member of other roles; refusing to continue', r.rolname;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.roleid = r.oid AND m.member <> me.oid) THEN
            RAISE EXCEPTION 'Existing role "%" has members other than the admin; refusing to continue', r.rolname;
        END IF;

        -- vt_app may own nothing; vt_migrator may own only schema vt and what is inside it.
        allowed := CASE WHEN r.rolname = 'vt_migrator' THEN coalesce(vt_oid, 0) ELSE 0 END;
        SELECT
            (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE c.relowner = r.oid AND n.oid <> allowed AND n.nspname NOT LIKE 'pg\_toast%')
          + (SELECT count(*) FROM pg_namespace WHERE nspowner = r.oid AND oid <> allowed)
          + (SELECT count(*) FROM pg_proc WHERE proowner = r.oid AND pronamespace <> allowed)
          + (SELECT count(*) FROM pg_type WHERE typowner = r.oid AND typnamespace <> allowed
                AND typrelid = 0 AND typelem = 0)
          + (SELECT count(*) FROM pg_database WHERE datdba = r.oid)
          INTO owned;
        IF owned > 0 THEN
            RAISE EXCEPTION 'Existing role "%" owns % unexpected object(s); refusing to continue', r.rolname, owned;
        END IF;
    END LOOP;

    -- A pre-existing schema must already be ours, and contain only our objects.
    IF vt_oid IS NOT NULL THEN
        SELECT nspowner::regrole::text INTO found FROM pg_namespace WHERE oid = vt_oid;
        IF found <> 'vt_migrator' THEN
            RAISE EXCEPTION 'Schema vt already exists and is owned by "%", not vt_migrator; refusing to take it over', found;
        END IF;

        SELECT string_agg(c.relname || ' (owner ' || c.relowner::regrole::text || ')', ', ') INTO found
          FROM pg_class c WHERE c.relnamespace = vt_oid AND c.relowner::regrole::text <> 'vt_migrator';
        IF found IS NOT NULL THEN
            RAISE EXCEPTION 'Schema vt contains relations not owned by vt_migrator: %', found;
        END IF;
        SELECT string_agg(p.proname || ' (owner ' || p.proowner::regrole::text || ')', ', ') INTO found
          FROM pg_proc p WHERE p.pronamespace = vt_oid AND p.proowner::regrole::text <> 'vt_migrator';
        IF found IS NOT NULL THEN
            RAISE EXCEPTION 'Schema vt contains functions not owned by vt_migrator: %', found;
        END IF;
        SELECT string_agg(t.typname || ' (owner ' || t.typowner::regrole::text || ')', ', ') INTO found
          FROM pg_type t WHERE t.typnamespace = vt_oid AND t.typrelid = 0 AND t.typelem = 0
           AND t.typowner::regrole::text <> 'vt_migrator';
        IF found IS NOT NULL THEN
            RAISE EXCEPTION 'Schema vt contains types not owned by vt_migrator: %', found;
        END IF;

        -- Only roles this script manages (or will revoke) may hold privileges on the schema.
        SELECT string_agg(DISTINCT pg_get_userbyid(a.grantee), ', ') INTO found
          FROM pg_namespace n, aclexplode(n.nspacl) a
         WHERE n.oid = vt_oid AND a.grantee <> 0
           AND pg_get_userbyid(a.grantee) NOT IN ('vt_migrator', 'vt_app', 'anon', 'authenticated');
        IF found IS NOT NULL THEN
            RAISE EXCEPTION 'Schema vt grants privileges to unexpected role(s): %', found;
        END IF;
    END IF;
END
$$;

-- 1. Roles ----------------------------------------------------------------------------------
-- CREATE ROLE defaults: NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS, and no
-- password. (A non-superuser admin cannot ALTER those attributes, so they are verified instead.)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vt_migrator') THEN
        CREATE ROLE vt_migrator LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vt_app') THEN
        CREATE ROLE vt_app LOGIN;
    END IF;
END
$$;

-- 2. Role defaults: fixed search_path (no `public`) and timeouts ----------------------------
ALTER ROLE vt_migrator SET search_path = vt, pg_catalog;
ALTER ROLE vt_app      SET search_path = vt, pg_catalog;
ALTER ROLE vt_app      SET statement_timeout = '30s';
ALTER ROLE vt_app      SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE vt_app      SET lock_timeout = '10s';

-- 3. Temporary membership (PG16+: creating a schema for another role, or SET ROLE to it,
--    needs the SET option). Skipped when the admin already has it. Whatever this block does
--    is recorded in a transaction-local setting and undone exactly in section 6.
DO $$
DECLARE
    had_row boolean;
BEGIN
    PERFORM set_config('vt_bootstrap.had_set',
                       pg_has_role(current_user, 'vt_migrator', 'SET')::text, true);
    IF pg_has_role(current_user, 'vt_migrator', 'SET') THEN
        PERFORM set_config('vt_bootstrap.temp_grant', 'none', true);
    ELSE
        had_row := EXISTS (SELECT 1 FROM pg_auth_members
                            WHERE roleid = to_regrole('vt_migrator')
                              AND member = to_regrole(current_user)
                              AND grantor = to_regrole(current_user));
        GRANT vt_migrator TO CURRENT_USER WITH SET TRUE GRANTED BY CURRENT_USER;
        PERFORM set_config('vt_bootstrap.temp_grant', CASE WHEN had_row THEN 'option' ELSE 'row' END, true);
    END IF;
END
$$;

-- 4. Schema: created directly for vt_migrator. An existing schema was validated in section 0
--    and is never re-owned.
DO $$
BEGIN
    IF to_regnamespace('vt') IS NULL THEN
        CREATE SCHEMA vt AUTHORIZATION vt_migrator;
    END IF;
END
$$;

-- 5. Schema access control, performed as the owner so the ACL belongs to vt_migrator ---------
SET LOCAL ROLE vt_migrator;
REVOKE ALL ON SCHEMA vt FROM PUBLIC;
GRANT USAGE ON SCHEMA vt TO vt_app;
RESET ROLE;

-- The Supabase Data API roles (what a publishable key maps to) must never reach this schema.
DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
            EXECUTE 'SET LOCAL ROLE vt_migrator';
            EXECUTE format('REVOKE ALL ON SCHEMA vt FROM %I', r);
            EXECUTE 'RESET ROLE';
        END IF;
    END LOOP;
END
$$;

-- 6. Undo exactly what section 3 did (nothing, if the admin already had SET) -----------------
DO $$
BEGIN
    CASE current_setting('vt_bootstrap.temp_grant')
        WHEN 'row' THEN
            REVOKE vt_migrator FROM CURRENT_USER GRANTED BY CURRENT_USER;
        WHEN 'option' THEN
            REVOKE SET OPTION FOR vt_migrator FROM CURRENT_USER GRANTED BY CURRENT_USER;
        ELSE
            NULL;
    END CASE;
END
$$;

-- 7. Post-conditions: abort (and roll back) unless the end state is exactly as designed ------
DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(rolname, ', ') INTO bad FROM pg_roles
     WHERE rolname IN ('vt_migrator', 'vt_app')
       AND (NOT rolcanlogin OR rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls);
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Post-check failed: unexpected attributes on %', bad;
    END IF;
    IF (SELECT count(*) FROM pg_roles WHERE rolname IN ('vt_migrator', 'vt_app')) <> 2 THEN
        RAISE EXCEPTION 'Post-check failed: vt_app and vt_migrator must both exist';
    END IF;
    IF (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'vt') IS DISTINCT FROM 'vt_migrator' THEN
        RAISE EXCEPTION 'Post-check failed: schema vt is not owned by vt_migrator';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace n, aclexplode(n.nspacl) a WHERE n.nspname = 'vt' AND a.grantee = 0) THEN
        RAISE EXCEPTION 'Post-check failed: PUBLIC still has privileges on schema vt';
    END IF;
    IF NOT has_schema_privilege('vt_app', 'vt', 'USAGE') OR has_schema_privilege('vt_app', 'vt', 'CREATE') THEN
        RAISE EXCEPTION 'Post-check failed: vt_app must have USAGE and not CREATE on schema vt';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members m
                WHERE m.member IN (to_regrole('vt_app'), to_regrole('vt_migrator'))) THEN
        RAISE EXCEPTION 'Post-check failed: vt_app/vt_migrator must not be members of any role';
    END IF;
    -- The admin's own membership of vt_migrator must be exactly what it was before.
    IF pg_has_role(current_user, 'vt_migrator', 'SET')::text <> current_setting('vt_bootstrap.had_set') THEN
        RAISE EXCEPTION 'Post-check failed: the admin''s SET option on vt_migrator was changed';
    END IF;
    IF current_setting('vt_bootstrap.temp_grant') = 'row' AND EXISTS (
        SELECT 1 FROM pg_auth_members
         WHERE roleid = to_regrole('vt_migrator') AND member = to_regrole(current_user)
           AND grantor = to_regrole(current_user)) THEN
        RAISE EXCEPTION 'Post-check failed: the temporary membership was not removed';
    END IF;
END
$$;

-- 8. End of transaction: ROLLBACK unless `commit` is exactly 1. The value is checked again here,
--    at the only place a COMMIT can happen, rather than trusting the earlier check.
\set do_commit false
\if :{?commit}
    SELECT (:'commit' = '1') AS do_commit \gset
\endif
\if :do_commit
    COMMIT;
    \echo 'Bootstrap COMMITTED. Next: run infra/db/verify_bootstrap.sql, then set the role passwords.'
\else
    ROLLBACK;
    \echo 'DRY RUN OK: every statement ran and all changes were rolled back. NOTHING WAS CHANGED.'
    \echo 'To apply for real, run the same command again with  -v commit=1'
\endif
