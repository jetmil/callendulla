# Тесты

Две части:
- **Unit** (~358 тестов) — против in-memory SQLite, ~3 секунды.
- **Integration** (~26 тестов) — против real Postgres + Redis через
  testcontainers, ~20 секунд (тянет Docker images первый раз).

## Установка dev-окружения

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,test]"
```

## Запуск

```bash
# Только unit (быстро)
pytest --ignore=tests/integration

# Только integration (нужен Docker daemon)
pytest tests/integration

# Всё
pytest
```

Integration-тесты **автоматически skip'аются** если Docker daemon
недоступен (например, на дев-машине без Docker). На GH Actions
runners Docker есть → они проходят в CI.

## Что покрыто unit

| Тесты | Слой |
|---|---|
| `test_safelog.py` | Token redact regex'ы |
| `test_no_pii.py` | Repo-scan на запрещённые substring'и |
| `test_config.py` | Settings validators (Fernet, webhook secret, etc.) |
| `test_models.py` | SQLAlchemy metadata, naming convention |
| `test_alembic.py` | Revision graph + smoke `upgrade head` на SQLite |
| `test_api.py` | FastAPI: /health, /source, AGPL §13, TrustedHost, CORS |
| `test_api_webhook.py` | Webhook secret header enforcement |
| `test_bot_handlers_*.py` | aiogram handlers через AsyncMock |
| `test_bot_keyboards.py` | Inline keyboard callback encoding |
| `test_event_repository.py` | EventRepository CRUD + cross-user |
| `test_scheduler_*.py` | Quiet hours, tone escalation, nudge engine |
| `test_llm_*.py` | Provider adapters + factory + engine integration |
| `test_stt.py` | Whisper adapter + background task |
| `test_voice_crypto.py` | Fernet round-trip |
| `test_observability.py` | Sentry init + scrubber |

## Что покрыто integration

`tests/integration/test_postgres_schema.py`:
- Alembic upgrade на pristine Postgres 16
- JSONB column type через `information_schema`
- JSONB `->>` operator работает
- `TIMESTAMP WITH TIME ZONE` round-trip preserves tzinfo
- CHECK constraint на quiet hours отбивает плохие значения
- FK CASCADE работает без SQLite PRAGMA
- Enum column = varchar (native_enum=False guard)

`tests/integration/test_rate_limit.py`:
- Sliding window под real Redis sorted set
- Concurrent calls не пропускают за threshold
- Denied attempts НЕ инфлятят bucket
- Окно реально слайдит после window+jitter

## Запись новых LLM fixtures

Default: все тесты LLM мокают SDK через `AsyncMock`, никаких реальных
вызовов в CI. Если когда-нибудь захочется записать real-API ответ —
сценарий описан, но не реализован (см. roadmap в README).

## Coverage

```bash
pytest --cov=callendulla --cov-report=term-missing
```

Целевое покрытие критических путей (safelog, scheduler, llm, db
repositories) — 70%+. На остальных — best-effort.

## Что не тестируется

- Реальные API провайдеров (Gemini, OpenAI, Anthropic, Ollama,
  Whisper) — мокаются. Это сознательное решение: дешевле +
  не выдаёт ключи в CI.
- Реальный Telegram Bot API — мокается через `AsyncMock`.
- Длительные scheduler scenarios (часы/дни в реальном времени) —
  передача `now_utc=` в `run_once` шорткатит время.

## CI

`.github/workflows/ci.yml` запускает:
- `ruff check` + `ruff format --check`
- `mypy src/`
- `pytest` (unit + integration)
- `gitleaks` секрет-скан
- `hadolint Dockerfile`
- `shellcheck deploy/*.sh`
- `docker build .` (sanity)

GH Actions runners имеют Docker daemon, integration тесты проходят.
