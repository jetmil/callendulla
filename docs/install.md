# Установка Callendulla (self-host)

Подробное руководство для оператора. README покрывает quick-start —
здесь полная конфигурация для production.

## Требования

- Linux x86_64 (тестируется Ubuntu 22.04+ / Debian 12)
- Docker 24+ и Docker Compose v2
- 2 GB RAM, 1 vCPU минимум; 4 GB / 2 vCPU для комфорта
- Доменное имя с TLS (только для webhook-режима / iCal/веб-консоли)

## Минимальный путь

```bash
git clone https://github.com/jetmil/callendulla
cd callendulla
cp .env.example .env
```

Открой `.env` и заполни **минимум** четыре переменные:

| Что | Откуда |
|---|---|
| `TELEGRAM_BOT_TOKEN` | `@BotFather` в Telegram → `/newbot` |
| `OWNER_TG_ID` | `@userinfobot` → твой user_id (число) |
| `SECRET_KEY` | `openssl rand -hex 32` |
| `DIARY_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

Для LLM-генерации текста пинков понадобится один из:
- `LLM_PROVIDER=ollama` (бесплатно, нужен GPU / 16 GB RAM)
- `LLM_PROVIDER=openai` + `LLM_API_KEY=sk-...`
- `LLM_PROVIDER=gemini` + `LLM_API_KEY=AIzaSy...`
- `LLM_PROVIDER=anthropic` + `LLM_API_KEY=sk-ant-...`

Если LLM не настроен — бот всё равно работает, шлёт пинки из
template-банка (6 voice profiles × 4 tones, см. `docs/llm.md`).

```bash
docker compose up -d
docker compose logs -f app
```

Найди бота в Telegram, напиши `/start`. Если твой `OWNER_TG_ID`
совпал — у тебя роль owner.

## Polling vs Webhook

### Polling (по умолчанию)

```ini
BOT_MODE=polling
```

Бот сам ходит за апдейтами. Не нужен публичный домен. Достаточно
для домашнего стенда / семейного использования. Один процесс — не
масштабируется горизонтально.

### Webhook (production)

Нужен публичный домен с TLS и nginx/Caddy перед FastAPI.

```ini
BOT_MODE=webhook
WEBHOOK_HOST=https://callendulla.example.com
WEBHOOK_PATH=/tg/$(openssl rand -hex 16)
WEBHOOK_SECRET=$(openssl rand -hex 32)
```

`WEBHOOK_PATH` — обфускация, попадёт в access-log nginx.
**Реальная защита** — `WEBHOOK_SECRET` в header
`X-Telegram-Bot-Api-Secret-Token`, header в логи не пишется.
SECURITY.md §6 разбирает это.

При старте контейнер сам зовёт `setWebhook` на Telegram, при
остановке — `deleteWebhook`.

## nginx минимальный конфиг

```nginx
server {
    listen 443 ssl http2;
    server_name callendulla.example.com;

    ssl_certificate     /etc/letsencrypt/live/callendulla.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/callendulla.example.com/privkey.pem;

    # Не логируй /ical/ — токен в URL обфускация, не нужно его в access.log
    location /ical/ {
        access_log off;
        proxy_pass http://127.0.0.1:8290;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Webhook должен быть TLS — Telegram не примет HTTP
    location /tg/ {
        proxy_pass http://127.0.0.1:8290;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location / {
        proxy_pass http://127.0.0.1:8290;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Также `ALLOWED_HOSTS=callendulla.example.com` в `.env` —
блокирует Host header injection.

## TLS-only

Telegram **отказывается** ходить на HTTP webhook. Let's Encrypt
через certbot — стандартный путь. После получения сертификата
nginx нужен `ssl_certificate` + перезапуск.

## Регистрация юзеров

```ini
REGISTRATION_MODE=invite        # default, никого не пускает кроме owner
# или
REGISTRATION_MODE=whitelist
WHITELIST_TG_USERNAMES=alice,bob_smith
# или (опасно для прода)
REGISTRATION_MODE=open
```

`open` за день наберёт 500 ботов, выжжет твою LLM-квоту. Используй
только для теста.

## Backup БД

Полезные команды:

```bash
# Дамп
docker compose exec -T postgres pg_dump -U callendulla -d callendulla | gzip > backup.sql.gz

# Восстановление
gunzip -c backup.sql.gz | docker compose exec -T postgres psql -U callendulla -d callendulla
```

Готовый cron-скрипт — `deploy/backup-cron.example.sh`. Положи в
`/usr/local/bin/`, в cron на 04:00 ежедневно, retention 7 дней.

**Off-host копия** — обязательно. Локальный backup рядом с VPS
помрёт вместе с ним. rclone в B2/S3 или scp на второй сервер.

## Логи и ротация

supervisord пишет в stdout/stderr → Docker JSON-file driver.
**Без ротации логи растут до full disk.** В `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "5" }
}
```

Или `journald` driver если у тебя systemd.

## Обновление

```bash
git pull
docker compose pull              # подтянуть base images
docker compose up -d --build
```

Миграции применяются автоматически при старте контейнера
(см. `deploy/entrypoint.sh`). Если несколько реплик —
`SKIP_MIGRATIONS=1` для всех кроме одной.

## Health check

- `GET /health` — пинг от k8s / docker healthcheck. Не трогает БД,
  так что блип Postgres не amplified в рестарт.
- `GET /source` — AGPL §13 disclosure (license, source_url, commit_sha)

## Что дальше

- [docs/llm.md](llm.md) — выбор провайдера и стоимость
- [docs/testing.md](testing.md) — как прогонять тесты
- [docs/key-rotation.md](key-rotation.md) — ротация
  DIARY_ENCRYPTION_KEY (re-encrypt дневника)
- [docs/agpl-compliance.md](agpl-compliance.md) — обязанности
  оператора при форке
- [SECURITY.md](../SECURITY.md) — 14-step hardening checklist
