#!/usr/bin/env bash
# Callendulla — пример скрипта ежедневного бэкапа PostgreSQL.
#
# Положи этот скрипт на хост (НЕ в Docker), отредактируй пути и пароль,
# поставь в cron:
#
#   sudo cp deploy/backup-cron.example.sh /usr/local/bin/callendulla-backup.sh
#   sudo chmod +x /usr/local/bin/callendulla-backup.sh
#   sudo crontab -e
#   # добавить строку:
#   0 4 * * * /usr/local/bin/callendulla-backup.sh >> /var/log/callendulla-backup.log 2>&1
#
# Альтернатива через systemd timer — см. docs/backup.md (готовится).

set -euo pipefail

# ─── конфигурация (отредактируй под свой деплой) ──────────────────────
readonly BACKUP_DIR="/var/backups/callendulla"
readonly RETAIN_DAYS=7
readonly COMPOSE_DIR="/opt/callendulla"         # где лежит docker-compose.yml
readonly PG_SERVICE="postgres"                  # имя сервиса в compose
readonly PG_DB="callendulla"
readonly PG_USER="callendulla"

# Опционально: внешнее хранилище. Раскомментируй и настрой.
# readonly RCLONE_REMOTE="b2:my-bucket/callendulla-backups"

# ─── pre-flight ───────────────────────────────────────────────────────
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_file="${BACKUP_DIR}/callendulla-${timestamp}.sql.gz"

mkdir -p "${BACKUP_DIR}"

# Лок-файл против параллельных запусков (cron + manual)
exec 200>/var/lock/callendulla-backup.lock
flock -n 200 || { echo "[backup] already running, skipping"; exit 0; }

# ─── pg_dump через docker compose exec ────────────────────────────────
echo "[backup] $(date -Iseconds) starting → ${backup_file}"

cd "${COMPOSE_DIR}"

# -T = no TTY (для cron). Дамп уезжает по pipe из контейнера в gzip на хосте.
# --clean --if-exists = идемпотентный restore.
docker compose exec -T "${PG_SERVICE}" \
    pg_dump --clean --if-exists --no-owner --no-acl \
            -U "${PG_USER}" "${PG_DB}" \
    | gzip -9 > "${backup_file}"

size_bytes=$(stat -c %s "${backup_file}")
echo "[backup] dump done, ${size_bytes} bytes"

# ─── sanity check: дамп не должен быть слишком маленьким ──────────────
if [ "${size_bytes}" -lt 1024 ]; then
    echo "[backup] FATAL: dump < 1KB, что-то сломалось"
    rm -f "${backup_file}"
    exit 1
fi

# ─── retention ────────────────────────────────────────────────────────
find "${BACKUP_DIR}" -name 'callendulla-*.sql.gz' -mtime +"${RETAIN_DAYS}" \
    -print -delete

# ─── опционально: вывоз на off-host ───────────────────────────────────
# Внешнее хранилище — KAR PUNKT для disaster recovery. VPS может умереть
# вместе с локальными бэкапами.
#
# if [ -n "${RCLONE_REMOTE:-}" ]; then
#     rclone copy "${backup_file}" "${RCLONE_REMOTE}/" --quiet
#     echo "[backup] uploaded to ${RCLONE_REMOTE}"
# fi

echo "[backup] $(date -Iseconds) done"

# ─── восстановление (для справки, НЕ запускается этим скриптом) ───────
# Тест на восстановление обязателен — без него бэкап = плацебо.
# Раз в месяц прогоняй на отдельной БД:
#
#   gunzip -c callendulla-20260601T040000Z.sql.gz | \
#     docker compose exec -T postgres psql -U callendulla -d callendulla_test
#
# Проверь что таблицы и счётчики совпадают с прод.
