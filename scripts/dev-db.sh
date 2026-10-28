#!/usr/bin/env bash
# Local development/test PostgreSQL 17 cluster for VendorTrust.
#
# Isolated from any other Postgres: data lives in .local-data/ (git-ignored), there is NO TCP
# listener (unix socket only, in an owner-only directory), and it uses passwordless "trust"
# auth on that socket, which is acceptable only because nothing outside this OS user can
# reach it. It holds throwaway data and no credentials. Never point real data at it.
#
# Usage: scripts/dev-db.sh {init|start|stop|status|reset|psql [db]|url <role> <db>}
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="$ROOT/.local-data"
DATA="$BASE/pg"
SOCK="$BASE/pgsock"
LOG="$BASE/pg.log"
PORT=54329
SUPERUSER=vt_dev_admin

pgbin() {
  local prefix
  prefix="$(brew --prefix postgresql@17 2>/dev/null)" || { echo "postgresql@17 not installed (brew install postgresql@17)" >&2; exit 1; }
  echo "$prefix/bin"
}
PGBIN="$(pgbin)"

check_socket_path() {
  local p="$SOCK/.s.PGSQL.$PORT"
  if [ "${#p}" -gt 100 ]; then
    echo "Socket path too long (${#p} > 100): $p. Move the repository to a shorter path." >&2
    exit 1
  fi
}

is_running() { "$PGBIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; }

cmd_init() {
  if [ -d "$DATA" ]; then echo "Cluster already initialised at $DATA"; return; fi
  check_socket_path
  mkdir -p "$BASE" "$SOCK"
  chmod 700 "$BASE" "$SOCK"
  "$PGBIN/initdb" -D "$DATA" -U "$SUPERUSER" --auth-local=trust --auth-host=reject \
    --encoding=UTF8 --locale=C >/dev/null
  echo "Initialised cluster at $DATA"
  cmd_start
  "$PGBIN/psql" -h "$SOCK" -p "$PORT" -U "$SUPERUSER" -d postgres -v ON_ERROR_STOP=1 -q \
    -f "$ROOT/infra/db/dev/supabase-sim.sql"
  echo "Created Supabase-simulation roles (dev/test only)"
}

cmd_start() {
  check_socket_path
  mkdir -p "$SOCK"; chmod 700 "$SOCK"
  if is_running; then echo "Already running"; return; fi
  "$PGBIN/pg_ctl" -D "$DATA" -l "$LOG" -w \
    -o "-c listen_addresses='' -c unix_socket_directories='$SOCK' -c port=$PORT" start >/dev/null
  echo "Started (socket: $SOCK, port $PORT, no TCP listener)"
}

cmd_stop() {
  if is_running; then "$PGBIN/pg_ctl" -D "$DATA" -m fast -w stop >/dev/null; echo "Stopped"; else echo "Not running"; fi
}

cmd_status() {
  if is_running; then echo "running (socket: $SOCK, port $PORT)"; else echo "stopped"; exit 3; fi
}

cmd_reset() {
  # Refuse to delete anything that is not exactly the project-local data directory.
  case "$DATA" in "$ROOT/.local-data/pg") ;; *) echo "refusing: unexpected data dir $DATA" >&2; exit 1 ;; esac
  cmd_stop
  rm -rf -- "$DATA" "$LOG"
  echo "Removed local cluster"
  cmd_init
}

case "${1:-}" in
  init) cmd_init ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  reset) cmd_reset ;;
  psql) shift; exec "$PGBIN/psql" -h "$SOCK" -p "$PORT" -U "$SUPERUSER" -d "${1:-postgres}" ;;
  url) # non-secret: there are no passwords on the local socket cluster
    echo "postgresql://${2:?role}@/${3:?db}?host=$SOCK&port=$PORT" ;;
  *) echo "Usage: $0 {init|start|stop|status|reset|psql [db]|url <role> <db>}" >&2; exit 2 ;;
esac
