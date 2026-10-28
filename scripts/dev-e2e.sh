#!/usr/bin/env bash
# Local end-to-end helper: the real database, real migrations, real API, real web app.
# Companion to scripts/dev-db.sh (which runs the local PostgreSQL 17 cluster). See docs/local-e2e.md.
#
# LOCAL DEVELOPMENT ONLY. It talks to the project-local unix-socket cluster, creates a throwaway
# database, and holds no secret: the socket cluster has no passwords, and the stand-in identity
# provider (apps/api/scripts/dev_auth.py) never leaves the loopback interface.
#
# Usage: scripts/dev-e2e.sh {setup|env|grant <email> <role>|psql|reset}
#   setup   create the database, apply infra/db/bootstrap.sql, run every Alembic migration
#   env     print the environment variables for each terminal (API, operator, web)
#   grant   give a user who has signed in once a role in the first organization (by hand; there are
#           no invitations yet). Roles: org_admin analyst reviewer auditor
#   psql    open psql on the database as the local superuser
#   reset   drop the database (and only this one)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOCK="$ROOT/.local-data/pgsock"
PORT=54329
SUPERUSER=vt_dev_admin
ADMIN=sb_admin_sim   # plays Supabase's non-superuser "postgres" admin role
DB=vendortrust_dev
AUTH_PORT=54330

PGBIN="${VT_PG_BIN:-}"
if [ -z "$PGBIN" ]; then
  PGBIN="$(brew --prefix postgresql@17 2>/dev/null)/bin" || true
fi
[ -x "$PGBIN/psql" ] || { echo "psql not found: brew install postgresql@17, or set VT_PG_BIN" >&2; exit 1; }

psql_as() { local user="$1" db="$2"; shift 2; "$PGBIN/psql" -X -q -h "$SOCK" -p "$PORT" -U "$user" -d "$db" -v ON_ERROR_STOP=1 "$@"; }
db_url() { echo "postgresql+psycopg://$1@/$DB?host=$SOCK&port=$PORT"; }

require_cluster() {
  psql_as "$SUPERUSER" postgres -c 'select 1' >/dev/null 2>&1 \
    || { echo "The local cluster is not running. Run: scripts/dev-db.sh init   (once), then scripts/dev-db.sh start" >&2; exit 1; }
}

cmd_setup() {
  require_cluster
  if ! psql_as "$SUPERUSER" postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
    psql_as "$SUPERUSER" postgres -c "CREATE DATABASE $DB OWNER $ADMIN"
    echo "Created database $DB"
  fi
  # exactly the file the Supabase runbook applies, committed explicitly
  psql_as "$ADMIN" "$DB" -v commit=1 -f "$ROOT/infra/db/bootstrap.sql" >/dev/null
  echo "Applied infra/db/bootstrap.sql (roles vt_app and vt_migrator, schema vt)"
  (cd "$ROOT/apps/api" && VT_MIGRATION_DATABASE_URL="$(db_url vt_migrator)" \
    uv run alembic -x commit=1 upgrade head)
  echo
  echo "Ready. Next: scripts/dev-e2e.sh env   (and follow docs/local-e2e.md)"
}

cmd_env() {
  cat <<EOF
# --- Terminal 1: the stand-in identity provider (local only, NOT Supabase) ---
cd apps/api && uv run python -m scripts.dev_auth serve

# --- Terminal 2: the API (it holds ONLY the application role's URL) ---
cd apps/api
export VT_ENVIRONMENT=development
export VT_SUPABASE_URL=http://127.0.0.1:$AUTH_PORT
export VT_DATABASE_URL='$(db_url vt_app)'
uv run uvicorn app.main:app --port 8000

# --- Terminal 3: the one-time operator bootstrap (holds ONLY the migration role's URL) ---
cd apps/api
export VT_SUPABASE_URL=http://127.0.0.1:$AUTH_PORT
export VT_SUPABASE_PUBLISHABLE_KEY=local-dev-key
export VT_MIGRATION_DATABASE_URL='$(db_url vt_migrator)'
uv run python -m scripts.bootstrap_org --org-name "Acme Ltd" --admin-email you@example.com
#   (add --commit once the dry run looks right)

# --- Terminal 4: the web app ---
cd apps/web
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \\
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:$AUTH_PORT \\
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=local-dev-key \\
pnpm dev
EOF
}

cmd_grant() {
  local email="${1:?usage: grant <email> <role>}" role="${2:?usage: grant <email> <role>}"
  case "$role" in org_admin|analyst|reviewer|auditor) ;; *) echo "role must be org_admin, analyst, reviewer or auditor" >&2; exit 2 ;; esac
  require_cluster
  psql_as "$SUPERUSER" "$DB" -v email="$email" -v role="$role" <<'SQL'
WITH target AS (SELECT id FROM vt.app_users WHERE lower(email) = lower(:'email')),
     org AS (SELECT id FROM vt.organizations ORDER BY created_at LIMIT 1)
INSERT INTO vt.memberships (org_id, user_id, role)
SELECT org.id, target.id, :'role' FROM org, target
RETURNING 'granted ' || role || ' in the first organization' AS result;
SQL
}

cmd_reset() {
  require_cluster
  psql_as "$SUPERUSER" postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)"
  echo "Dropped $DB. Delete .local-data/dev-auth to forget the local test users too."
}

case "${1:-}" in
  setup) cmd_setup ;;
  env) cmd_env ;;
  grant) shift; cmd_grant "$@" ;;
  psql) exec "$PGBIN/psql" -X -h "$SOCK" -p "$PORT" -U "$SUPERUSER" -d "$DB" ;;
  reset) cmd_reset ;;
  *) echo "Usage: $0 {setup|env|grant <email> <role>|psql|reset}" >&2; exit 2 ;;
esac
