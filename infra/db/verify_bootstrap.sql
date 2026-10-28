-- Read-only verification of infra/db/bootstrap.sql. Safe to run on any database at any time:
-- it only reads catalogs and changes nothing. Every row must show ok = true.
--
-- Run locally:    psql -d <db> -f infra/db/verify_bootstrap.sql
-- Run on Supabase: paste into the SQL editor after the bootstrap has been applied.

WITH
app AS (SELECT * FROM pg_roles WHERE rolname = 'vt_app'),
mig AS (SELECT * FROM pg_roles WHERE rolname = 'vt_migrator'),
checks (check_name, ok) AS (
    VALUES
    ('vt_app exists', EXISTS (SELECT FROM app)),
    ('vt_migrator exists', EXISTS (SELECT FROM mig)),
    ('vt_app can log in', COALESCE((SELECT rolcanlogin FROM app), false)),
    ('vt_migrator can log in', COALESCE((SELECT rolcanlogin FROM mig), false)),
    ('vt_app has no dangerous attributes',
        (SELECT NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)
           FROM app)),
    ('vt_migrator has no dangerous attributes',
        (SELECT NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)
           FROM mig)),
    ('vt_app is a member of no role',
        NOT EXISTS (SELECT FROM pg_auth_members m JOIN app ON m.member = app.oid)),
    ('vt_migrator is a member of no role',
        NOT EXISTS (SELECT FROM pg_auth_members m JOIN mig ON m.member = mig.oid)),
    ('schema vt exists', EXISTS (SELECT FROM pg_namespace WHERE nspname = 'vt')),
    ('schema vt is owned by vt_migrator',
        COALESCE((SELECT nspowner::regrole::text = 'vt_migrator'
                    FROM pg_namespace WHERE nspname = 'vt'), false)),
    ('PUBLIC has no privileges on schema vt',
        NOT EXISTS (SELECT FROM pg_namespace n, aclexplode(n.nspacl) a
                     WHERE n.nspname = 'vt' AND a.grantee = 0)),
    ('vt_app can use schema vt',
        COALESCE(has_schema_privilege('vt_app', 'vt', 'USAGE'), false)),
    ('vt_app cannot create in schema vt',
        NOT COALESCE(has_schema_privilege('vt_app', 'vt', 'CREATE'), true)),
    ('vt_app cannot create in schema public',
        NOT has_schema_privilege('vt_app', 'public', 'CREATE')),
    ('vt_migrator cannot create in schema public',
        NOT has_schema_privilege('vt_migrator', 'public', 'CREATE')),
    ('vt_app cannot create schemas in this database',
        NOT has_database_privilege('vt_app', current_database(), 'CREATE')),
    ('vt_app owns no objects',
        NOT EXISTS (SELECT FROM pg_class c JOIN app ON c.relowner = app.oid)
        AND NOT EXISTS (SELECT FROM pg_namespace n JOIN app ON n.nspowner = app.oid)),
    ('everything in schema vt is owned by vt_migrator',
        NOT EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'vt' AND c.relowner::regrole::text <> 'vt_migrator')),
    ('anon/authenticated (if present) cannot use schema vt',
        NOT EXISTS (SELECT FROM pg_roles r
                     WHERE r.rolname IN ('anon', 'authenticated')
                       AND has_schema_privilege(r.rolname, 'vt', 'USAGE'))),
    ('vt_app search_path is pinned',
        COALESCE((SELECT 'search_path=vt, pg_catalog' = ANY (rolconfig) FROM app), false)),
    ('vt_app statement_timeout is set',
        COALESCE((SELECT EXISTS (SELECT FROM unnest(rolconfig) s WHERE s LIKE 'statement_timeout=%')
                    FROM app), false))
)
SELECT check_name, COALESCE(ok, false) AS ok FROM checks ORDER BY ok, check_name;
