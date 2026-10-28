-- DEV/TEST ONLY. Never run against Supabase.
--
-- Recreates, in the throwaway local cluster, the parts of a Supabase project that matter to
-- our security model, so the bootstrap and RLS tests exercise realistic conditions:
--   * sb_admin_sim: a NON-superuser admin with CREATEROLE/CREATEDB, standing in for the
--     Supabase `postgres` role, so bootstrap.sql is proven to work without superuser rights.
--   * anon, authenticated: the Data API roles a publishable key maps to.
--   * service_role: a role with BYPASSRLS, standing in for what a secret key maps to.
--
-- Run by scripts/dev-db.sh as the local superuser. Refuses to run on a Supabase database.

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname IN ('supabase_admin', 'supabase_auth_admin'))
       OR EXISTS (SELECT FROM pg_namespace WHERE nspname IN ('auth', 'storage')) THEN
        RAISE EXCEPTION 'Refusing to run: this looks like a Supabase database';
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sb_admin_sim') THEN
        CREATE ROLE sb_admin_sim LOGIN NOSUPERUSER CREATEROLE CREATEDB;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
    END IF;
END
$$;
