#!/usr/bin/env bash
# Private local PostgreSQL for machines without Docker. It uses TCP on a non-default port, keeps its
# data under .local/pgdata (git-ignored) and never touches any other PostgreSQL service.
set -euo pipefail
export LC_ALL="${LC_ALL:-en_US.UTF-8}" LANG="${LANG:-en_US.UTF-8}"
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
# shellcheck disable=SC1091
set -a; . ./.env; set +a
PGDATA_DIR="$PWD/.local/pgdata"
LOG="$PWD/.local/postgres.log"

case "${1:-}" in
  start)
    mkdir -p .local
    if [ ! -f "$PGDATA_DIR/PG_VERSION" ]; then
      PWFILE="$(mktemp)"; printf '%s' "$POSTGRES_PASSWORD" > "$PWFILE"
      initdb -D "$PGDATA_DIR" -U "$POSTGRES_USER" --pwfile="$PWFILE" -A scram-sha-256 -E UTF8 >/dev/null
      rm -f "$PWFILE"
      cat >> "$PGDATA_DIR/postgresql.conf" <<CONF
port = $POSTGRES_PORT
listen_addresses = '127.0.0.1'
unix_socket_directories = ''
max_wal_size = 256MB
min_wal_size = 64MB
CONF
    fi
    if pg_ctl -D "$PGDATA_DIR" status >/dev/null 2>&1; then
      echo "already running on port $POSTGRES_PORT"
    else
      pg_ctl -D "$PGDATA_DIR" -l "$LOG" -w start >/dev/null
      echo "started on port $POSTGRES_PORT"
    fi
    export PGPASSWORD="$POSTGRES_PASSWORD"
    if ! psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$POSTGRES_DB'" | grep -q 1; then
      createdb -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" "$POSTGRES_DB"
      echo "created database $POSTGRES_DB"
    fi
    ;;
  stop) pg_ctl -D "$PGDATA_DIR" -m fast -w stop ;;
  status) pg_ctl -D "$PGDATA_DIR" status ;;
  *) echo "usage: $0 {start|stop|status}"; exit 2 ;;
esac
