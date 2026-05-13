# Ротация ключей

## `SECRET_KEY`

Используется для подписи web-сессий и CSRF-токенов. Ротация — без
последствий: существующие web-сессии инвалидируются, юзеры
залогинятся заново.

```bash
# 1. Сгенерировать новый
openssl rand -hex 32

# 2. Записать в .env
sed -i 's/^SECRET_KEY=.*/SECRET_KEY=<новое>/' .env

# 3. Перезапустить
docker compose up -d --force-recreate app
```

## `WEBHOOK_SECRET`

Тот, что Telegram присылает в `X-Telegram-Bot-Api-Secret-Token`.
Ротация — Telegram автоматически узнает новый при следующем
`setWebhook`, который делается в lifespan контейнера.

```bash
openssl rand -hex 32  # → новое значение
# обновить .env, перезапустить — lifespan вызовет set_webhook с новым secret
docker compose up -d --force-recreate app
```

Старый secret сразу мёртв. Все pending updates Telegram'а в очереди
ловят 403, но Telegram умеет ретраить — потери нет.

## `DIARY_ENCRYPTION_KEY` ⚠️ ОПАСНО

Этим ключом зашифрован voice diary (audio + transcript ciphertext).
**Просто заменить ключ = старые записи дневника становятся
нечитаемыми навечно.**

Безопасный путь: re-encrypt сначала, потом меняй ключ.

### Шаг 1 — сгенерировать новый ключ

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Шаг 2 — re-encrypt скрипт

```python
# scripts/reencrypt_diary.py
"""Re-encrypt all VoiceDiary rows from OLD_KEY to NEW_KEY.

Usage:
    OLD_DIARY_KEY=... NEW_DIARY_KEY=... python scripts/reencrypt_diary.py
"""
import asyncio
import os
import sys

from pydantic import SecretStr
from sqlalchemy import select

from callendulla.core.voice_crypto import decrypt, encrypt
from callendulla.db.models import VoiceDiary
from callendulla.db.session import create_engine, create_session_factory


async def main() -> None:
    old = SecretStr(os.environ["OLD_DIARY_KEY"])
    new = SecretStr(os.environ["NEW_DIARY_KEY"])
    dsn = os.environ["DB_DSN"]
    engine = create_engine(dsn)
    factory = create_session_factory(engine)

    async with factory() as session:
        entries = list((await session.execute(select(VoiceDiary))).scalars())
        for e in entries:
            audio = decrypt(e.audio_ciphertext, key=old)
            transcript = decrypt(e.transcript_ciphertext, key=old)
            e.audio_ciphertext = encrypt(audio, key=new)
            e.transcript_ciphertext = encrypt(transcript, key=new)
            print(f"re-encrypted entry {e.id}")
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

### Шаг 3 — прогнать в контейнере

```bash
# 1. Положить старый ключ во временную переменную
docker compose exec -T app sh -c '
  OLD_DIARY_KEY=$DIARY_ENCRYPTION_KEY \
  NEW_DIARY_KEY=НОВЫЙ_КЛЮЧ \
  python /app/scripts/reencrypt_diary.py
'

# 2. Обновить .env на новый ключ
sed -i 's/^DIARY_ENCRYPTION_KEY=.*/DIARY_ENCRYPTION_KEY=НОВЫЙ_КЛЮЧ/' .env

# 3. Перезапустить
docker compose up -d --force-recreate app bot scheduler
```

### Сбой посередине

Если скрипт упал на половине списка — часть rows под старым ключом,
часть под новым. `decrypt()` бросит `DecryptionError` на тех, что
расшифровываются не тем ключом. В бот-handler'е это выльется как
сообщение "Запись не открывается, DIARY_ENCRYPTION_KEY был
ротирован без re-encrypt". Откатить .env на старый ключ, прогнать
скрипт заново — он re-encrypt'ит идемпотентно (тех что уже на новом
просто не сможет decrypt старым, скрипт упадёт; повтори с новым
старым ключом).

**Правильный путь:** скрипт идемпотентный через try-old / fallback-new.
Готовый template — TODO в будущем PR.

## `LLM_API_KEY` / `TELEGRAM_BOT_TOKEN`

Без последствий — обнови .env, перезапусти. Ключи только в RAM
процессов, в БД не хранятся.

## `ical_token` (per-user)

Юзер сам — `/rotate_ical` в боте. Не требует операторского
вмешательства.
