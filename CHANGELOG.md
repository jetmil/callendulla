# Changelog

Все заметные изменения этого проекта ведутся в этом файле.

Формат — [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
версионирование — [SemVer 2.0](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- Каркас репозитория: `Dockerfile`, `docker-compose.yml`, `LICENSE`
  (AGPL-3.0), `README.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  `docs/agpl-compliance.md`
- `.env.example` со всеми переменными окружения (LLM, Telegram, DB,
  webhook, quiet hours, AGPL source URL)
- `pyproject.toml` с PEP 621 metadata, ruff/mypy/pytest конфигами
- `pre-commit` с gitleaks + ruff + hadolint + shellcheck +
  conventional-commits
- GitHub Actions CI: gitleaks, ruff, mypy, pytest, docker build,
  shellcheck, hadolint
- Issue/PR templates с security checklist и AGPL §13 проверками
- `.github/CODEOWNERS` и `dependabot.yml`
- `deploy/entrypoint.sh` (Alembic-race guard) +
  `deploy/supervisord.conf` (3 процесса в одном образе) +
  `deploy/backup-cron.example.sh`

### Planned (WIP в README)

- Runtime код в `src/callendulla/`: api / bot / scheduler / core
- 6 архетипов голоса с собственным словарём и жанром шуток
- Голосовой дневник с Fernet at-rest
- iCal-фид per-user
- `core/safelog.py` с token redact + loguru patcher
- AGPL §13 middleware (`X-Source-URL` header + `/source` endpoint +
  `/source` команда в боте)
- Webhook режим с проверкой `X-Telegram-Bot-Api-Secret-Token`
- Cross-user изоляция в `NudgeCache` и LLM-промптах
- Prompt injection sanitization для user-supplied event titles
- Тесты с testcontainers (реальный Postgres+Redis, не моки)

[Unreleased]: https://github.com/jetmil/callendulla/compare/HEAD...HEAD
