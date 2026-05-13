# LLM провайдеры — BYOK выбор

Callendulla не несёт ни одного API-ключа. Оператор приносит свой,
выбирает провайдера через `.env`. Без ключа бот тоже работает —
шлёт пинки из template-банка (6 voice profiles × 4 tones).

## Сравнение

| Провайдер | Цена | Качество | Нужен интернет | Локальный |
|---|---|---|---|---|
| **Ollama** | Бесплатно | Зависит от модели | Нет | Да |
| **Gemini** | Платный с Billing, бесплатный без | Высокое | Да | Нет |
| **OpenAI** | $0.15-0.60/M токенов | Высокое | Да | Нет |
| **Anthropic** | $1-15/M токенов | Высокое | Да | Нет |

Один пинк = один LLM-вызов. Промпт ~500 токенов, ответ ~50 токенов.
**На 100 пинков в день у одного юзера:**

- Gemini 2.5 Flash: ~$0.40/мес (если Billing активен)
- OpenAI gpt-4o-mini: ~$0.30/мес
- Anthropic Haiku 4.5: ~$1.50/мес
- Ollama: $0, твой GPU/CPU

## Ollama (локальный)

Без ключа. Бесплатно. Нужен GPU 8+ GB VRAM или мощный CPU.

```ini
LLM_PROVIDER=ollama
LLM_MODEL=gemma3:12b
OLLAMA_BASE_URL=http://ollama:11434
```

Минимальный docker-compose-overlay:

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    ports:
      - "127.0.0.1:11434:11434"
    volumes:
      - ollama-data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  ollama-data:
```

Подтянуть модель:

```bash
docker compose exec ollama ollama pull gemma3:12b
```

## Gemini (Google)

```ini
LLM_PROVIDER=gemini
LLM_API_KEY=AIzaSy...
LLM_MODEL=gemini-2.5-flash   # default; -pro если нужно качество
```

Ключ — в [Google AI Studio](https://aistudio.google.com/apikey).

**Подводный камень:** если в Google Cloud Console **активирован
Billing для проекта**, ВСЕ запросы платные, даже на моделях с
формальным free tier. Без Billing — жёсткие лимиты RPM/RPD.

## OpenAI

```ini
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

Ключ — [platform.openai.com/api-keys](https://platform.openai.com/api-keys).

**Бонус:** этот же ключ автоматически работает для Whisper STT
(расшифровка голосового дневника). Не нужно держать два разных.

## Anthropic

```ini
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5-20251001
```

Ключ — [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys).

## Что выбрать

- **Дома, без интернета** → Ollama
- **Хочешь дёшево + хорошо** → Gemini Flash (без Billing) или OpenAI gpt-4o-mini
- **Хочешь STT для voice diary** → OpenAI (один ключ на два сервиса)
- **Хочешь анонимность от cloud** → Ollama

## Что НЕ работает (yet)

- Локальный Whisper для STT — STT-провайдер сейчас только OpenAI.
  Без OpenAI ключа дневник работает в audio-only режиме (запись
  сохраняется зашифрованно, но без транскрипта).
- Function-calling / tool-use — не используется проектом, MVP
  делает только текстовую генерацию.

## Отключение LLM

Если оператор вообще не хочет LLM:

```ini
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11999   # неработающий порт
```

Бот будет валиться в `LLMError` → автоматически fallback на
template-bank на каждый пинк. То же что `LLM_PROVIDER=ollama` без
поднятого Ollama. **Это документированный режим** — нагрузки на
LLM нет, юзеры всё равно получают пинки.

## Отладка

```bash
docker compose exec app python -c "
from callendulla.config import get_settings
from callendulla.llm import build_provider
s = get_settings()
p = build_provider(s)
print('provider:', type(p).__name__, 'model:', s.llm_model)
"
```

Если возвращает `OllamaProvider` но Ollama не отвечает — увидишь
`LLMError` в логах планировщика, бот при этом продолжит шлёть из
template-банка.
