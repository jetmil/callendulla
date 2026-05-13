# Changelog

Все заметные изменения этого проекта ведутся в этом файле.

Формат — [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
версионирование — [SemVer 2.0](https://semver.org/lang/ru/).

## [Unreleased]

Планируется к следующему минорному релизу: локальный Piper для TTS,
local Whisper-cpp для STT без сетевых вызовов, voice-output опция
на пинках (`Bot.send_voice` вместо `send_message`),
prompt-injection sanitization для user-supplied event titles в LLM
промптах, branch-protection и CODEOWNERS-review enforcement на main.

## [0.1.0] — 2026-05-13

Первый публичный релиз. Полный end-to-end путь от регистрации до
GDPR-erasure работает.

### Added

#### Каркас и инфра (PR #1–#4)

- Пакетная структура `src/callendulla/` с SPDX-headers, PEP 621
  `pyproject.toml`, ruff strict, mypy strict
- `core/safelog.py` — token redact (api_key, Bearer, OpenAI/Anthropic/
  Gemini/GitHub/Telegram/Fernet detectors) + loguru patcher +
  `safe_repr()` для exception path-ов
- `tests/test_no_pii.py` — repo-scan против запрещённых substring'ов
  (defence in depth поверх gitleaks)
- pydantic-settings `Settings` с валидаторами безопасности:
  `SECRET_KEY` ≥ 32 chars, `DIARY_ENCRYPTION_KEY` корректный Fernet,
  `WEBHOOK_SECRET` ≥ 32 при `BOT_MODE=webhook`, `LLM_API_KEY`
  обязателен для не-ollama, `QUIET_FROM == QUIET_TO` отбит
- SQLAlchemy 2.0 declarative модели: User, Event, Trigger, NudgeLog,
  NudgeCache, VoiceDiary. Naming convention для Alembic. `NudgeCache`
  без FK на users — cross-user-safe by design
- Alembic init + handwritten initial migration, `entrypoint.sh`
  применяет `alembic upgrade head` один раз перед supervisord

#### HTTP API (PR #5, #7, #17)

- FastAPI factory `create_app(settings)` + lazy module-level `app`
  через PEP 562
- AGPL §13: `X-Source-URL` header на КАЖДОМ ответе (включая 404)
  + `GET /source` JSON с license/url/commit_sha/build_date
- `/health` cheap liveness probe (не трогает БД)
- TrustedHost (allowed_hosts), CORS (closed by default)
- Webhook receiver `POST /tg/{webhook_path}` с
  `X-Telegram-Bot-Api-Secret-Token` через `secrets.compare_digest`,
  всегда возвращает 200 на handler-exception (не даём Telegram
  retry-storm)
- Per-IP rate-limit на `/ical/` — Redis sliding window под
  MULTI/EXEC pipeline; denied attempts не инфлятят bucket

#### Bot (PR #6, #8, #11, #15, #23)

- aiogram 3 dispatcher с роутерами по доменам команд
- `UserMiddleware` — единственная точка mapping'а TG identity → User
  row. `REGISTRATION_MODE` policy: open / whitelist / invite.
  Owner всегда self-register (bootstrap)
- Команды: `/start /help /source`, `/add /list /delete` (events),
  `/ical /rotate_ical`, `/diary /diary play/transcript/forget`,
  voice messages, `/forget`, `/voice /timezone /quiet`
- Inline-кнопки реакций на пинках (✅💤🌅🔇) → callback-handler
  обновляет `Trigger` state + ломает silent streak для cap-guard
- Opaque-response для cross-user попыток: одинаковый текст «не
  найдено» для чужого ID и несуществующего

#### Scheduler (PR #9)

- APScheduler tick раз в минуту с `misfire_grace_time=60`
- Quiet hours per-user локальное окно (22..9 wraps midnight)
- Cap-эскалация: HARD + 3 silent NudgeLog → 12h snooze + reset tone
- 6 voice profiles × 4 tones template bank, random variants

#### LLM / STT / TTS (PR #10, #14, #22) — все BYOK

- LLMProvider Protocol + 4 адаптера (Gemini, OpenAI, Anthropic,
  Ollama). Lazy SDK imports. Template fallback при LLMError
- STTProvider — OpenAI Whisper, активируется когда
  `LLM_PROVIDER=openai` (ключ piggyback'ает на LLM_API_KEY)
- TTSProvider — edge-tts (Microsoft Azure free, без ключа)
- Никаких ключей в репо/тестах/CI; `repr()` каждого провайдера
  проверен на отсутствие api_key

#### Voice diary (PR #13)

- `core/voice_crypto.py` — Fernet encrypt/decrypt с
  `DecryptionError` обёрткой (не палит криптодетали)
- `bot/handlers/diary.py` — приём голосового, decrypt-free
  background STT task, transcript хранится тоже зашифрованно
- Plaintext audio никогда не на диске

#### iCal feed (PR #12)

- `GET /ical/{token}` → text/calendar (RFC 5545) с per-user
  событиями. Token = `User.ical_token`, 32-hex random
- `/ical` и `/rotate_ical` команды в боте

#### GDPR (PR #15)

- `/forget` с двухступенчатым подтверждением (inline keyboard)
- Cascade-delete user → events, triggers, nudge_logs, voice_diary.
  `nudge_cache` остаётся (no FK по дизайну)
- Audit log через `logger.warning` с tg_id, без PII

#### Observability (PR #18)

- Sentry SDK по `SENTRY_DSN` (opt-in). FastAPI + Starlette + Loguru
  + SqlAlchemy integrations
- `before_send=_strip_secrets` рекурсивно scrub'ит JSON event через
  `safelog.redact()` — last-line defence

#### Tests (PR #16, #20, #21)

- 410+ unit-тестов под SQLite (PRAGMA foreign_keys=ON для CASCADE
  parity с Postgres)
- 18 integration на real Postgres 16-alpine через testcontainers:
  JSONB indexing, TIMESTAMP WITH TIME ZONE round-trip, CHECK
  constraints, FK CASCADE без PRAGMA
- 8 integration на real Redis 7-alpine — sliding window
  correctness под concurrent calls
- CI split на unit + integration jobs; docker compose smoke
  поднимает stack и hit'ит `/health` + `/source`

#### Docs (PR #19)

- `docs/install.md` — полная установка с nginx-конфигом
- `docs/llm.md` — BYOK provider matrix + cost estimates
- `docs/testing.md` — что покрыто и как запускать
- `docs/key-rotation.md` — re-encrypt при ротации
  DIARY_ENCRYPTION_KEY

### Security

- AGPL §13 compliance: `X-Source-URL` + `/source` endpoint + TG
  `/source` команда
- Cross-user изоляция на трёх уровнях: repository
  (`*_for_owner` методы), handler (`data["user"]` из middleware),
  opaque-response policy
- BYOK инвариант: ни одного ключа в репо; factory-тесты проверяют
  что operator's key проходит verbatim; `repr()` не палит
- Webhook secret в header (`X-Telegram-Bot-Api-Secret-Token`),
  constant-time compare; обфусцированный path только для access-log
  obscurity
- Fernet at-rest для voice diary + transcript
- Rate-limit per-IP на `/ical/` (sliding window Redis)
- Sentry `before_send` scrubber поверх собственного безопасного
  логирования

### Versions / dependencies

- Python 3.12+
- PostgreSQL 16 (prod target; SQLite только для unit-test smoke)
- Redis 7 (rate-limit + future кеш)
- FastAPI 0.115+, aiogram 3.27+, APScheduler 3.10+

[Unreleased]: https://github.com/jetmil/callendulla/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jetmil/callendulla/releases/tag/v0.1.0
