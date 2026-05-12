# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tone escalation + template messages.

This MVP does no LLM call. Each (voice_profile, tone) pair maps to a
small bank of templates, picked randomly. LLM-driven generation lands
in a follow-up PR — it slots into :func:`render_nudge` without changing
the rest of the engine.

Why hard-coded templates first:
- The scheduler can be smoke-tested without a paid API key.
- Forks without LLM access still get something usable.
- Once the schedule loop is proven, we know LLM cost / latency is the
  only variable left.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Final

from callendulla.db.models import ToneStep, VoiceProfile

# Ordering used by :func:`escalate`. Last element is the cap — repeated
# escalation past it stays at HARD until the cap-guard in nudge_engine
# kicks in.
_TONE_ORDER: Final[tuple[ToneStep, ...]] = (
    ToneStep.SOFT,
    ToneStep.NORMAL,
    ToneStep.SHARP,
    ToneStep.HARD,
)

# Delay between consecutive fires when there is no user reaction. Climbs
# down (more urgent) as tone escalates.
_INTERVAL_AFTER: Final[dict[ToneStep, timedelta]] = {
    ToneStep.SOFT: timedelta(hours=1),
    ToneStep.NORMAL: timedelta(minutes=30),
    ToneStep.SHARP: timedelta(minutes=15),
    ToneStep.HARD: timedelta(minutes=10),
}

CAP_TONE: Final[ToneStep] = _TONE_ORDER[-1]
"""The escalation ceiling. Cap-guard logic lives in nudge_engine."""

CAP_ITERATIONS_WITHOUT_REACTION: Final[int] = 3
"""Reach CAP_TONE + this many silent iterations → 12h snooze + reset."""

CAP_SNOOZE: Final[timedelta] = timedelta(hours=12)


# ─────────────────────────────────────────────────────────────────
# Template banks
# ─────────────────────────────────────────────────────────────────
# Each profile x tone has multiple variants. Random pick on render.
# Variants intentionally short — Telegram cluster of these is annoying
# enough without each one being a wall of text.

_TEMPLATES: dict[tuple[VoiceProfile, ToneStep], tuple[str, ...]] = {
    # ── BRUTAL_BRO ────────────────────────────────────────────────
    (VoiceProfile.BRUTAL_BRO, ToneStep.SOFT): (
        "🔔 Слушай, у тебя сейчас «{title}». Не профукай.",
        "🔔 Время «{title}». Шевелись.",
    ),
    (VoiceProfile.BRUTAL_BRO, ToneStep.NORMAL): (
        "🔔 Эй, я тебе уже говорил про «{title}». Делай давай.",
        "🔔 «{title}» — твоё, не моё. Поехали.",
    ),
    (VoiceProfile.BRUTAL_BRO, ToneStep.SHARP): (
        "🔔 Серьёзно, «{title}» ждёт. Заколебал откладывать.",
        "🔔 «{title}» — тебе самому надо. Хватит тянуть.",
    ),
    (VoiceProfile.BRUTAL_BRO, ToneStep.HARD): (
        "🔔 Всё, последний раз: «{title}». Сделал — отметил. Не сделал — потом не плачь.",
        "🔔 «{title}». Я всё, дальше сам.",
    ),
    # ── WARM_SISTER ───────────────────────────────────────────────
    (VoiceProfile.WARM_SISTER, ToneStep.SOFT): (
        "🌼 Привет! Напомню: «{title}». Не забыть бы.",
        "🌼 Тут «{title}» по плану. Получится сделать?",
    ),
    (VoiceProfile.WARM_SISTER, ToneStep.NORMAL): (
        "🌼 «{title}» всё ещё актуально. Если что — я рядом.",
        "🌼 Помнишь про «{title}»? Хорошо бы заняться.",
    ),
    (VoiceProfile.WARM_SISTER, ToneStep.SHARP): (
        "🌼 «{title}» уже долго висит. Может, всё-таки?",
        "🌼 Я понимаю, что лень. Но «{title}» само себя не сделает.",
    ),
    (VoiceProfile.WARM_SISTER, ToneStep.HARD): (
        "🌼 «{title}». Я больше повторять не буду — ты взрослый.",
        "🌼 Окей, «{title}» — последнее напоминание от меня сегодня.",
    ),
    # ── OFFICE_NEUTRAL ────────────────────────────────────────────
    (VoiceProfile.OFFICE_NEUTRAL, ToneStep.SOFT): (
        "📌 Напоминание: «{title}».",
        "📌 К сведению: пора заняться «{title}».",
    ),
    (VoiceProfile.OFFICE_NEUTRAL, ToneStep.NORMAL): (
        "📌 «{title}» — пора приступать.",
        "📌 Задача «{title}» в активном статусе.",
    ),
    (VoiceProfile.OFFICE_NEUTRAL, ToneStep.SHARP): (
        "📌 «{title}» — задержка. Просьба уделить внимание.",
        "📌 По «{title}» — требуется действие.",
    ),
    (VoiceProfile.OFFICE_NEUTRAL, ToneStep.HARD): (
        "📌 «{title}» — критическая задержка. Финальное напоминание.",
        "📌 «{title}» — эскалация. Дальнейшие пинки прекращаются.",
    ),
    # ── DRILL_SERGEANT ────────────────────────────────────────────
    (VoiceProfile.DRILL_SERGEANT, ToneStep.SOFT): (
        "⚔️ К исполнению: «{title}». Приступить.",
        "⚔️ «{title}» — задача поставлена. Время пошло.",
    ),
    (VoiceProfile.DRILL_SERGEANT, ToneStep.NORMAL): (
        "⚔️ «{title}» — выполнения не вижу. Доложить о статусе.",
        "⚔️ Боец, «{title}» — почему не начато?",
    ),
    (VoiceProfile.DRILL_SERGEANT, ToneStep.SHARP): (
        "⚔️ «{title}» — третье напоминание. Это вообще нормально?",
        "⚔️ «{title}». Я жду действия, а не оправданий.",
    ),
    (VoiceProfile.DRILL_SERGEANT, ToneStep.HARD): (
        "⚔️ «{title}» — отбой. Сдохнешь под завалами — пеняй на себя.",
        "⚔️ Всё, «{title}» — записываю в провалы. Дальше сам.",
    ),
    # ── IRON_LADY ─────────────────────────────────────────────────
    (VoiceProfile.IRON_LADY, ToneStep.SOFT): (
        "💼 Напоминаю: «{title}». В рабочее окно успеваешь?",
        "💼 «{title}» — у нас по плану. Сделаем?",
    ),
    (VoiceProfile.IRON_LADY, ToneStep.NORMAL): (
        "💼 «{title}» висит без движения. Это не дело.",
        "💼 Дорогой, «{title}» уже неприлично затянулось.",
    ),
    (VoiceProfile.IRON_LADY, ToneStep.SHARP): (
        "💼 «{title}». Я устала повторять. Это последний разумный тон.",
        "💼 «{title}» — выбирай: сделать или объясняться.",
    ),
    (VoiceProfile.IRON_LADY, ToneStep.HARD): (
        "💼 «{title}». Всё, я молчу. Ты сам решил, как тебе жить.",
        "💼 «{title}» — fine. Считаю инцидент закрытым.",
    ),
    # ── QUIET_MENTOR ──────────────────────────────────────────────
    (VoiceProfile.QUIET_MENTOR, ToneStep.SOFT): (
        "🪷 «{title}». Когда будешь готов — приступай.",
        "🪷 Заметь: «{title}» сейчас.",
    ),
    (VoiceProfile.QUIET_MENTOR, ToneStep.NORMAL): (
        "🪷 «{title}». Подумай о том, что откладывание тоже выбор.",
        "🪷 «{title}» зовёт. Услышь.",
    ),
    (VoiceProfile.QUIET_MENTOR, ToneStep.SHARP): (
        "🪷 «{title}». Откладывая, ты выбираешь иную судьбу.",
        "🪷 «{title}». Что важнее: комфорт сейчас или результат потом?",
    ),
    (VoiceProfile.QUIET_MENTOR, ToneStep.HARD): (
        "🪷 «{title}». Я свою часть выполнил. Дальше — твоё.",
        "🪷 «{title}». Молчание — тоже ответ.",
    ),
}


def escalate(tone: ToneStep) -> ToneStep:
    """Move one step up the ladder. Stays at :data:`CAP_TONE` at the top."""
    try:
        idx = _TONE_ORDER.index(tone)
    except ValueError:
        return _TONE_ORDER[0]
    next_idx = min(idx + 1, len(_TONE_ORDER) - 1)
    return _TONE_ORDER[next_idx]


def interval_after(tone: ToneStep) -> timedelta:
    """Delay before the next fire when there's no reaction to ``tone``."""
    return _INTERVAL_AFTER[tone]


def render_nudge(
    *,
    profile: VoiceProfile,
    tone: ToneStep,
    title: str,
    rng: random.Random | None = None,
) -> str:
    """Pick one template variant for ``(profile, tone)`` and substitute ``title``.

    Falls back to ``OFFICE_NEUTRAL`` if a profile isn't templated yet —
    new profiles can land without freezing every (profile, tone) bank
    being filled.
    """
    chooser = rng if rng is not None else random.SystemRandom()
    variants = _TEMPLATES.get((profile, tone))
    if variants is None:
        variants = _TEMPLATES[(VoiceProfile.OFFICE_NEUTRAL, tone)]
    template = chooser.choice(variants)
    return template.format(title=title)
