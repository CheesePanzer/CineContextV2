#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

PYTHON="${CINECONTEXT_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
    PYTHON="$(conda run -n cinecontext python -c 'import sys; print(sys.executable)')"
fi
"$PYTHON" -c 'import alcedo, asyncpg, openai, httpx' 2>/dev/null || "$PYTHON" -m pip install -e backend

if [[ -z "${DATABASE_URL:-}" ]]; then
    umask 077
    runtime="$ROOT/.runtime"
    mkdir -p "$runtime"
    db_port="${CINECONTEXT_DB_PORT:-55438}"
    db_dir="$runtime/postgres"
    if [[ ! -f "$db_dir/PG_VERSION" ]]; then
        "$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(24))' > "$runtime/db-password"
        initdb -D "$db_dir" -U cinecontext --auth=scram-sha-256 --pwfile="$runtime/db-password"             --no-locale -E UTF8 >/dev/null
    fi
    if ! pg_ctl -D "$db_dir" status >/dev/null; then
        pg_ctl -D "$db_dir" -l "$runtime/postgres.log"             -o "-p $db_port -c listen_addresses=127.0.0.1 -c unix_socket_directories=''" -w start >/dev/null
    fi
    export DATABASE_URL="postgresql+asyncpg://cinecontext:$(cat "$runtime/db-password")@127.0.0.1:$db_port/postgres"
fi

(cd backend && "$PYTHON" -m alembic upgrade head)
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build
cd backend
case "${CINECONTEXT_APP:-mock}" in
    mock) app_factory="cinecontext.mock.main:create_app" ;;
    production) app_factory="cinecontext.main:create_app" ;;
    *) echo "CINECONTEXT_APP must be mock or production" >&2; exit 2 ;;
esac
exec "$PYTHON" -m uvicorn "$app_factory" --factory --host 127.0.0.1 --port "${CINECONTEXT_PORT:-8765}"
