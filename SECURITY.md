# Security Policy

## Reporting a vulnerability

**Please do NOT open a public GitHub Issue for security problems.**

Email: **jetmil@proton.me**
PGP key: *(coming, ask for it in the email)*

Include in your report:

- Brief description of the issue
- Steps to reproduce (or PoC)
- Affected version / commit SHA
- Suggested fix if you have one
- Your contact for follow-up

I aim to respond within **72 hours**. Critical issues affecting deployed
instances will get a patched release within **7 days**.

After the issue is fixed and a release is out, I will:

- Credit you in the release notes (unless you prefer to stay anonymous)
- File a CVE if the impact warrants it

## Scope

In scope:

- Authentication / authorization in the web console
- iCal token leakage or predictability
- Cross-user data leakage via `nudge_cache`, LLM prompts, or logs
- Prompt injection through user-supplied event titles / diary entries
- Voice diary encryption at-rest
- Telegram webhook signature handling
- Rate-limit bypasses
- Container escape via Dockerfile choices
- AGPL §13 compliance bypasses (e.g. ways to hide `X-Source-URL`)

Out of scope:

- DoS via single-tenant resource exhaustion (this is your VPS, scale it)
- Issues in upstream dependencies (report to the dependency author;
  I will patch when they patch)
- Social engineering against your Telegram users
- Self-XSS in the web console

## Supported versions

| Version | Security patches |
|---------|------------------|
| latest `main` | yes |
| latest tagged release | yes |
| older tagged releases | no — please upgrade |

---

# Hardening checklist for operators

Чек-лист обязательных шагов перед тем как открывать инстанс наружу.

## 1. Никогда не открывай Docker-порт PostgreSQL наружу

В `docker-compose.yml` Postgres не пробрасывает порт на хост. Если ты
добавил `ports: ["5432:5432"]` для удобства psql — закрой это
firewall'ом или сделай `127.0.0.1:5432:5432`. Postgres c дефолтным
паролем + публичный 5432 = взлом за минуты.

## 2. nginx/Caddy перед app, не сам Docker-порт

Не пробрасывай 8290 наружу. Поставь TLS-прокси (nginx, Caddy,
Traefik), который терминирует HTTPS и форвардит на `127.0.0.1:8290`.

## 3. ALLOWED_HOSTS = твой домен, не `*`

Host header injection — атакующий шлёт `Host: evil.com`, и твои
абсолютные URL (password reset links, OAuth callbacks) генерятся на
домен атакующего. В `.env`:

```ini
ALLOWED_HOSTS=callendulla.example.com,api.callendulla.example.com
```

## 4. CORS закрыт по умолчанию

`CORS_ORIGINS=` (пусто) блокирует все cross-origin браузерные запросы.
Открой только конкретные origin'ы своей веб-консоли:

```ini
CORS_ORIGINS=https://app.callendulla.example.com
```

`*` — только для локальной разработки.

## 5. REGISTRATION_MODE не `open`

```ini
REGISTRATION_MODE=invite   # лучший дефолт
# или
REGISTRATION_MODE=whitelist
WHITELIST_TG_USERNAMES=alice,bob_smith
```

`open` = открытая регистрация = за день наберётся 500 ботов, выжгут
твою LLM-квоту.

## 6. Webhook — секрет в header, не в path

Если включаешь webhook (`BOT_MODE=webhook`):

- **`WEBHOOK_PATH`** — это просто URL-обфускация. Путь попадает в
  nginx access-log, в логи метрик прокси, в браузерную историю если
  кто-то ткнёт. **Не клади туда секрет, на который ты надеешься.**
- **`WEBHOOK_SECRET`** — Telegram присылает его в header
  `X-Telegram-Bot-Api-Secret-Token` (доступен с Bot API 6.0, 2022).
  Сервер сверяет его до обработки апдейта. В access-log не пишется,
  в браузерах не светится. **Это реальная защита.**

Дефолтный код Callendulla проверяет header автоматически если
`WEBHOOK_SECRET` задан. Без него любой, кто узнал твой `WEBHOOK_PATH`,
шлёт fake-апдейты от Telegram.

## 7. Token redact в логах — обязательно

Любой `logger.error(f"LLM failed: {exc!r}")` риска утечки. Если
`exc` это `httpx.HTTPStatusError`, repr содержит URL с `?api_key=AIz...`
или header `Authorization: Bearer sk-...`. Docker logs → grep → утечка.

В коде Callendulla — wrapper `core.safelog._redact()` маскирует
известные паттерны:

```python
_TOKEN_PATTERNS = [
    (re.compile(r"(api_key=)[A-Za-z0-9_-]{8,}"),   r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._-]{10,}", re.I), r"\1***"),
    (re.compile(r"(sk-[a-z]*-?)[A-Za-z0-9_-]{20,}"),     r"\1***"),
    (re.compile(r"(AIzaSy)[A-Za-z0-9_-]{30,}"),          r"\1***"),
    (re.compile(r"(ghp_)[A-Za-z0-9]{30,}"),               r"\1***"),
]
```

> **WIP:** утилита `_redact()` и автоматический loguru patcher
> запланированы в одном из первых runtime-коммитов. До тех пор любой
> контрибьютор обязан вручную оборачивать чувствительные строки —
> [`CONTRIBUTING.md`](CONTRIBUTING.md) фиксирует это как code-review
> требование.

## 8. AGPL §13 compliance

Сервер обязан давать пользователям network-сервиса доступ к source code.
Реализовано через:

- HTTP header `X-Source-URL` в каждом ответе (значение из
  `AGPL_SOURCE_URL` env)
- Endpoint `GET /source` с JSON: url + commit_sha + build_date
- Telegram-команда `/source`

Если форкнул и подменил код — **обязан** поставить `AGPL_SOURCE_URL` на
свой fork. Подробности — [`docs/agpl-compliance.md`](docs/agpl-compliance.md).

## 9. PostgreSQL backups — ежедневный cron, off-host копия

В `docker-compose.yml` нет автоматического бэкапа намеренно — у каждого
свой backup-таргет (S3, B2, локальный NAS, rsync на второй VPS).

Готовый пример скрипта — [`deploy/backup-cron.example.sh`](deploy/backup-cron.example.sh).
Положи в `/usr/local/bin/`, в cron на 04:00 ежедневно, retention 7
дней. **Дополнительно** настрой выгрузку off-host (rclone в B2/S3, или
scp на второй VPS) — VPS может умереть вместе с локальными бэкапами.

Без backup'а через 6 месяцев потеряешь весь календарь юзеров после
случайного `docker compose down -v`. Catastrophic data loss с моих
рук — не моя ответственность, но твоя репутация.

**Раз в месяц** прогоняй тест восстановления на отдельной БД — без
этого backup = плацебо.

## 10. SELinux / AppArmor совместимость

На RHEL/CentOS/Fedora SELinux в strict mode ломает bind mounts в
Docker. Если контейнер не может писать в `/app/data` или `/app/logs` —
скорее всего SELinux. Лечится:

```bash
# для конкретного volume
sudo chcon -Rt container_file_t /var/lib/callendulla/data

# или relabel при mount (compose):
volumes:
  - ./data:/app/data:Z   # :Z релейблит для privatе use
```

На Ubuntu/Debian с AppArmor — обычно работает из коробки. Если custom
profile — проверь что Docker default unconfined.

## 11. Pin Docker base image к SHA, не `:latest`

В `Dockerfile` сейчас `FROM python:3.12-slim` — это `:latest` для
slim-варианта. В prod для воспроизводимости:

```dockerfile
FROM python:3.12-slim@sha256:abc123...
```

Узнать SHA: `docker pull python:3.12-slim && docker inspect python:3.12-slim --format='{{.Id}}'`.

## 12. Rotate `SECRET_KEY` и `DIARY_ENCRYPTION_KEY`

Если твой VPS был когда-либо клонирован/шарен (snapshot для другого
человека, неудачный hosting migration) — ротируй оба ключа. **Внимание:**

- `SECRET_KEY` — ротация просто инвалидирует существующие веб-сессии,
  юзеры залогинятся заново
- `DIARY_ENCRYPTION_KEY` — ротация **сделает старые записи
  голосового дневника нечитаемыми**. Сначала прогони re-encrypt
  скрипт (см. `docs/key-rotation.md`, *готовится*), потом меняй ключ

## 13. Container не root

В Dockerfile создан non-root user `callendulla` с UID 1000 (matches
host's first user, чтобы bind mount permissions работали без хаков).
Если у тебя host-user с другим UID — meняй `--uid` в Dockerfile или
делай `chown` на volumes.

## 14. Logs rotation

supervisord пишет в stdout/stderr, Docker daemon забирает в JSON-file
driver. **Без ротации** логи растут до full disk. В `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "5"
  }
}
```

Или используй `journald` driver если у тебя systemd.
