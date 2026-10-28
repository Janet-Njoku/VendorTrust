-- VendorTrust read-only preflight. Run BEFORE bootstrap.sql. It changes nothing: the whole
-- script runs inside BEGIN READ ONLY, which PostgreSQL enforces (any write would error).
--
--     psql -X -v ON_ERROR_STOP=1 -v expected_ref=<project-ref> -f infra/db/preflight.sql
--
-- <project-ref> is the 20-character "Reference ID" from the Supabase dashboard
-- (Project Settings, General) of the project you INTEND to change. The script refuses to pass
-- unless the connection itself (user or host) names that project, and the database carries
-- Supabase's markers. It then checks the admin role can run the bootstrap, and reports the
-- current state of vt_app, vt_migrator and schema vt.
--
-- Exit status is non-zero if any check fails. Nothing sensitive is printed (no passwords).

\set ON_ERROR_STOP on

\if :{?expected_ref}
\else
    \echo 'ERROR: pass the project reference, e.g.  -v expected_ref=abcdefghijklmnopqrst'
    DO $$ BEGIN RAISE EXCEPTION 'preflight not run: expected_ref is required'; END $$;
\endif

BEGIN READ ONLY;

SELECT set_config('vt_preflight.ref',  :'expected_ref', true) AS _ref,
       set_config('vt_preflight.user', :'USER', true)         AS _user,
       set_config('vt_preflight.host', :'HOST', true)         AS _host,
       set_config('vt_preflight.port', :'PORT', true)         AS _port \gset

DO $$
DECLARE
    ref        text := current_setting('vt_preflight.ref');
    conn_user  text := current_setting('vt_preflight.user');
    conn_host  text := current_setting('vt_preflight.host');
    conn_port  text := current_setting('vt_preflight.port');
    me         pg_roles%ROWTYPE;
    r          pg_roles%ROWTYPE;
    report     text := '';
    fails      int  := 0;
    ok         boolean;
    detail     text;
    users_n    text;
    vt_owner   text;
BEGIN
    -- Helper pattern: ok := <condition>; report := report || line(ok, text); fails += not ok.
    -- 1. Is this the project the operator intended?
    ok := ref ~ '^[a-z0-9]{6,32}$';
    report := report || format(E'%s  expected project reference looks valid (%s)\n',
                               CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END, ref);
    fails := fails + (NOT ok)::int;

    ok := strpos(conn_user, ref) > 0 OR strpos(conn_host, ref) > 0;
    report := report || format(E'%s  connection targets project "%s" (user "%s", host "%s", port %s)\n',
                               CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END, ref, conn_user, conn_host, conn_port);
    fails := fails + (NOT ok)::int;

    -- 2. Encrypted unless a local unix socket.
    ok := conn_host LIKE '/%' OR EXISTS (SELECT 1 FROM pg_stat_ssl WHERE pid = pg_backend_pid() AND ssl);
    report := report || format(E'%s  connection is encrypted (TLS) or a local socket\n',
                               CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END);
    fails := fails + (NOT ok)::int;

    -- 3. Supabase fingerprints.
    ok := (SELECT count(*) FROM pg_roles
            WHERE rolname IN ('supabase_admin', 'anon', 'authenticated', 'service_role')) = 4
          AND (SELECT count(*) FROM pg_namespace WHERE nspname IN ('auth', 'storage')) = 2;
    report := report || format(E'%s  database has Supabase markers (roles supabase_admin/anon/authenticated/service_role; schemas auth/storage)\n',
                               CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END);
    fails := fails + (NOT ok)::int;

    BEGIN
        EXECUTE 'SELECT count(*)::text FROM auth.users' INTO users_n;
    EXCEPTION WHEN OTHERS THEN
        users_n := 'unreadable';
    END;
    report := report || format(E'INFO  database "%s", server %s, auth.users rows: %s (compare with Authentication > Users in the dashboard)\n',
                               current_database(), current_setting('server_version'), users_n);

    -- 4. Can the admin role run bootstrap.sql?
    ok := current_setting('server_version_num')::int >= 160000;
    report := report || format(E'%s  PostgreSQL 16 or newer (%s)\n',
                               CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END, current_setting('server_version'));
    fails := fails + (NOT ok)::int;

    SELECT * INTO me FROM pg_roles WHERE rolname = current_user;
    ok := me.rolsuper OR me.rolcreaterole;
    report := report || format(E'%s  admin role "%s" can create roles (CREATEROLE or superuser)\n',
                               CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END, current_user);
    fails := fails + (NOT ok)::int;

    ok := me.rolsuper OR has_database_privilege(current_user, current_database(), 'CREATE');
    report := report || format(E'%s  admin role has CREATE on database "%s" (needed for schema vt)\n',
                               CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END, current_database());
    fails := fails + (NOT ok)::int;

    -- 5. Current state of what bootstrap.sql manages.
    FOR r IN SELECT * FROM pg_roles WHERE rolname IN ('vt_migrator', 'vt_app') ORDER BY rolname LOOP
        ok := r.rolcanlogin AND NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
              AND (me.rolsuper OR EXISTS (SELECT 1 FROM pg_auth_members m
                                           WHERE m.roleid = r.oid AND m.member = me.oid AND m.admin_option));
        detail := format('pre-existing role "%s" is safe and administrable by "%s"', r.rolname, current_user);
        report := report || format(E'%s  %s\n', CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END, detail);
        fails := fails + (NOT ok)::int;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('vt_migrator', 'vt_app')) THEN
        report := report || E'INFO  vt_app and vt_migrator do not exist yet (bootstrap will create them)\n';
    END IF;

    SELECT nspowner::regrole::text INTO vt_owner FROM pg_namespace WHERE nspname = 'vt';
    IF vt_owner IS NULL THEN
        report := report || E'INFO  schema vt does not exist yet (bootstrap will create it)\n';
    ELSE
        ok := vt_owner = 'vt_migrator';
        report := report || format(E'%s  existing schema vt is owned by "%s" (must be vt_migrator; bootstrap never takes over a schema)\n',
                                   CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END, vt_owner);
        fails := fails + (NOT ok)::int;
    END IF;

    PERFORM set_config('vt_preflight.report', report, true);
    PERFORM set_config('vt_preflight.fails', fails::text, true);
END
$$;

\pset tuples_only on
\pset format unaligned
SELECT current_setting('vt_preflight.report');
SELECT (current_setting('vt_preflight.fails')::int = 0) AS passed \gset

\if :passed
    \echo 'PREFLIGHT PASSED. Nothing was changed. If the project reference above is your development project, you may continue.'
    ROLLBACK;
\else
    ROLLBACK;
    \echo 'PREFLIGHT FAILED. Nothing was changed. Do NOT run bootstrap.sql until every FAIL is understood.'
    DO $$ BEGIN RAISE EXCEPTION 'preflight failed: see the FAIL lines above'; END $$;
\endif
