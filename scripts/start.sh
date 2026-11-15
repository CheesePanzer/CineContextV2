#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
job_id="${1:-}"
if [[ $# -gt 1 || ( $# -eq 1 && "$job_id" != --submit-tacc && ! "$job_id" =~ ^[0-9]+$ ) ]]; then
    echo "Usage: $0 [TACC_JOB_ID | --submit-tacc]" >&2
    exit 2
fi
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi
if [[ "$job_id" == --submit-tacc ]]; then
    umask 077
    job_records="$ROOT/.runtime/tacc-jobs"
    mkdir -p "$job_records"
    walltime="$(awk '/^[[:space:]]*- time=/ { sub(/.*time=/, ""); print }' deploy/tacc/tuxiv.conf)"
    if [[ ! "$walltime" =~ ^[0-9]+:[0-9]{2}:[0-9]{2}$ ]]; then
        echo "Set the TACC job time limit to HH:MM:SS in deploy/tacc/tuxiv.conf." >&2
        exit 1
    fi
    sdk="${APP__MODEL__TCLOUD_BIN:-tcloud}"
    submission="$(cd deploy/tacc && "$sdk" submit 2>&1)" || { printf '%s\n' "$submission" >&2; exit 1; }
    printf '%s\n' "$submission"
    job_id="$(printf '%s\n' "$submission" | tr -d '\r' | awk '/^Submitted batch job [0-9]+$/ { id=$4 } END { print id }')"
    if [[ ! "$job_id" =~ ^[0-9]+$ ]]; then
        echo "Could not read the TACC job ID from the submission. Check tcloud ps." >&2
        exit 1
    fi
    printf '%s %s\n' "$(date +%s)" "$walltime" > "$job_records/$job_id"
    latest_job="$(mktemp "$job_records/latest.XXXXXX")"
    printf '%s\n' "$job_id" > "$latest_job"
    mv -f "$latest_job" "$job_records/latest"
elif [[ -z "$job_id" && "${CINECONTEXT_APP:-}" == production && "${APP_ENVIRONMENT:-}" == tacc &&
        -z "${APP__MODEL__TACC_JOB_ID:-}" && -z "${APP__MODEL__BASE_URL:-}" &&
        -f "$ROOT/.runtime/tacc-jobs/latest" ]]; then
    read -r job_id < "$ROOT/.runtime/tacc-jobs/latest"
fi
if [[ -n "$job_id" ]]; then
    export CINECONTEXT_APP=production APP_ENVIRONMENT=tacc APP__MODEL__MODE=tacc
    export APP__MODEL__TACC_JOB_ID="$job_id"
fi

PYTHON="${CINECONTEXT_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
    PYTHON="$(conda run -n cinecontext python -c 'import sys; print(sys.executable)')"
fi
(cd backend && "$PYTHON" -c 'import cinecontext.main, asyncpg, uvicorn, alembic') 2>/dev/null || "$PYTHON" -m pip install -e backend

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
