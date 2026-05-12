#!/usr/bin/env bash
# Callendulla — container entrypoint.
#
# Зачем: supervisord стартует api+bot+scheduler параллельно. Если каждый
# из них на старте делает `alembic upgrade head` — гонка на DDL.
# Поэтому миграции тащим ровно один раз, ДО запуска supervisord'а.
#
# Этот скрипт идемпотентен: если миграций нет — `alembic upgrade head`
# просто завершится с success.

set -euo pipefail

readonly APP_DIR="${APP_DIR:-/app}"
readonly LOG_PREFIX="[entrypoint]"

log() {
    echo "${LOG_PREFIX} $*"
}

die() {
    log "FATAL: $*"
    exit 1
}

# --- 1) wait for postgres ----------------------------------------------------
# docker-compose depends_on с healthcheck даёт стартовый сигнал, но при
# нестандартных deploy'ях healthcheck может отсутствовать. Простой poll.
wait_for_postgres() {
    local max_wait=60
    local elapsed=0
    log "waiting for postgres (max ${max_wait}s)..."
    while ! python -c "
import os, sys
from sqlalchemy import create_engine, text
dsn = os.environ.get('DB_DSN_SYNC')
if not dsn:
    print('DB_DSN_SYNC not set', file=sys.stderr); sys.exit(1)
try:
    eng = create_engine(dsn, connect_args={'connect_timeout': 3})
    with eng.connect() as c: c.execute(text('SELECT 1'))
except Exception as e:
    print(f'pg not ready: {e}', file=sys.stderr); sys.exit(2)
" 2>/dev/null; do
        elapsed=$((elapsed + 2))
        if [ "${elapsed}" -ge "${max_wait}" ]; then
            die "postgres not reachable after ${max_wait}s — проверь DB_DSN_SYNC и доступность контейнера postgres"
        fi
        sleep 2
    done
    log "postgres is up (${elapsed}s)"
}

# --- 2) run migrations -------------------------------------------------------
run_migrations() {
    log "running alembic upgrade head..."
    cd "${APP_DIR}"
    if [ ! -f "alembic.ini" ]; then
        log "WARN: alembic.ini не найден в ${APP_DIR}, пропускаю миграции"
        return 0
    fi
    alembic upgrade head
    log "migrations applied"
}

# --- 3) optional: skip migrations for replica containers ---------------------
# Если ты подняла N реплик через scale=N, миграции должна делать только
# одна. Передай SKIP_MIGRATIONS=1 в env реплик. В compose с одной репликой
# по умолчанию переменная не задана — миграции бегут.
if [ "${SKIP_MIGRATIONS:-0}" = "1" ]; then
    log "SKIP_MIGRATIONS=1 — пропускаю миграции (реплика)"
else
    wait_for_postgres
    run_migrations
fi

# --- 4) exec the actual command (supervisord по умолчанию) ------------------
log "exec: $*"
exec "$@"
