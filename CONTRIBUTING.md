# Contributing to Callendulla

Спасибо, что хочешь помочь. Пара правил, чтобы PR прошёл быстро.

> Проект в ранней публичной стадии — каркас и часть документации
> уже стоит, runtime-код пишется. PR на любую часть приветствуются.

## Code style

- Python 3.12+
- `ruff` для линта и форматирования (конфиг в `pyproject.toml`)
- `mypy --strict` на новый код
- Имена переменных и комментарии — на английском (репо public),
  пользовательские строки в боте — на русском (целевая аудитория)

Перед PR прогони локально:

```bash
ruff check .
ruff format .
mypy src/
pytest
```

Или один раз поставь pre-commit:

```bash
pip install pre-commit
pre-commit install
```

## Тесты

- На новую фичу — тесты в `tests/` (mirror структуры `src/`)
- Не мокай PostgreSQL — используй реальный через `testcontainers`
  (это правило выучено через `feedback_smoke_test_obman` — мок
  показывал зелёный там, где прод падал на migration)
- Не мокай LLM — используй `tests/fixtures/llm_replay/` (записанные
  ответы реальных API). Если нужен новый сценарий — запиши через
  `pytest --record-llm` (см. `docs/testing.md`)
- Coverage критических путей (scheduler, auth, LLM-wrapper) — 70%+

## Commits и PR

- Commit message: `feat: short description` / `fix: ...` /
  `docs: ...` / `refactor: ...` / `test: ...` / `chore: ...`
- Тело коммита: что и почему. «Что» видно в diff, «почему» —
  только тут (PR description мы потеряем при squash в main)
- Подписывай коммиты GPG если можешь (`git commit -S`)
- PR — один логический change. Не миксуй рефакторинг и фичу
- Перед merge твой PR будет:
  - проверен CI (ruff + mypy + pytest)
  - просмотрен мной (Darovitsky), обычно в течение 3 дней
  - возможно squash'нут — fine, я сохраню ваш `Co-authored-by`

## CLA (Contributor License Agreement)

При первом PR вас попросят подписать CLA через
[CLA Assistant](https://cla-assistant.io). Подпись одна на все будущие
вклады.

**Почему:** проект использует двойную лицензию — AGPL-3.0 для open
source + опциональная коммерческая лицензия для бизнеса, который не
может использовать AGPL. Без CLA вкладчики держат права на свои
патчи, и я не имею права предоставлять коммерческую лицензию,
включающую их код. Без коммерческой лицензии нет монетизации проекта
вообще.

CLA НЕ передаёт право собственности — вы остаётесь автором, я
получаю лицензию использовать ваш вклад в обеих ветках лицензии.
Текст CLA — стандартный [Harmony 1.0](http://harmonyagreements.org/).

Если вы принципиально против CLA — открывайте Issue, обсудим.

## Что я проверяю в каждом PR (твой self-check перед запросом review)

### Token / secret leak

Любой `logger.*` или `raise` с интерполяцией внешних exception'ов —
потенциальная утечка. `httpx.HTTPStatusError` в repr() содержит URL с
`?api_key=...` и header `Authorization: Bearer ...`. Это летит в
Docker logs.

**Обязательное правило:** оборачивай чувствительные строки в
`core.safelog._redact()` перед записью в лог. Известные паттерны
(api_key, Bearer, sk-, AIzaSy, ghp_) маскируются. PR без redact'а на
LLM/STT error path — отклоняется.

### AGPL §13

Если ты добавляешь HTTP middleware или меняешь response pipeline —
не сломай header `X-Source-URL`. Если ты добавляешь новый endpoint —
он наследует middleware, проверять не нужно. Если меняешь endpoint
`/source` — обоснуй в PR description.

### Cross-user изоляция в кешах и LLM-prompt'ах

Если ты трогаешь `NudgeCache` или формирование LLM-prompt'а — убедись,
что в кеш-ключ или prompt не попадает плейн user data (event title,
voice text, имя юзера). Это утечка между юзерами в multi-tenant.

### Тесты

См. секцию выше — реальный Postgres, записанные LLM-fixture'ы.

## Assets (картинки, иконки, шрифты, аудио)

**Никакого Pinterest / Behance / Google Image Search.** Все assets в
репо должны быть либо:

- Твои собственные (CC0 от тебя)
- Из CC0 / Public Domain коллекций (unsplash, lottiefiles CC0, openmoji)
- Под лицензией, совместимой с AGPL-3.0 (CC-BY-SA 4.0, OFL 1.1 для
  шрифтов, GPL для иконок)

В PR с новым asset'ом приложи источник и явную лицензию в commit
message:

```
asset: add header background

Source: https://unsplash.com/photos/abc123
License: Unsplash License (CC0-equivalent)
Author: Jane Doe
```

PR с asset'ами без указания источника — отклоняется до выяснения.
Сторонняя картинка под copyright в AGPL-репо = юридический риск для
всех, кто склонировал.

## О чём НЕ делать PR (без обсуждения в Issue)

- Замена выбранного LLM-провайдера / TTS-движка на ваш любимый
- Удаление AGPL clauses из заголовков (юридически опасно)
- Удаление middleware `X-Source-URL` (нарушение §13)
- Изменение архитектуры (api+bot+scheduler разделение)
- Добавление новых runtime-зависимостей весом >50MB
- Изменение публичного API без semver-bump

Эти PR обычно отклоняются — лучше сначала обсудить в Issue.

## Spam

PR'ы вида «I made it work on Windows by replacing X with Y» без
тестов и без описания зачем — будут closed без ответа. PR'ы от
ботов с "swap X dependency for Y because Y is more popular" —
аналогично.
