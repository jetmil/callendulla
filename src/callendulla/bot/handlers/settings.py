# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""User self-service settings: ``/voice``, ``/timezone``, ``/quiet``.

Without these, every preference change requires the operator to mutate
a DB row. With them, each user owns their own profile inside the
schema's per-user fields.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy import select

from callendulla.db.models import User, VoiceProfile
from callendulla.db.session import SessionFactory

router = Router(name="settings")


_VOICE_LIST = (
    "<b>Доступные профили речи:</b>\n"
    "• <code>brutal_bro</code> — друг-братан, прямой, грубоватый\n"
    "• <code>warm_sister</code> — заботливая сестра, тёплая\n"
    "• <code>office_neutral</code> — нейтральный корпоративный\n"
    "• <code>drill_sergeant</code> — сержант, командный тон\n"
    "• <code>iron_lady</code> — уверенная руководительница\n"
    "• <code>quiet_mentor</code> — тихий наставник, через парадокс\n\n"
    "Выбрать: <code>/voice &lt;имя&gt;</code>"
)


@router.message(Command("voice"))
async def handle_voice(
    message: types.Message,
    user: User | None,
    command: CommandObject,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await message.answer("Сначала зарегистрируйся — <code>/start</code>.")
        return

    arg = (command.args or "").strip().lower()
    if not arg:
        await message.answer(f"Сейчас: <code>{user.voice_profile.value}</code>\n\n{_VOICE_LIST}")
        return

    try:
        new_profile = VoiceProfile(arg)
    except ValueError:
        await message.answer(f"Не знаю профиль <code>{arg}</code>.\n\n{_VOICE_LIST}")
        return

    async with session_factory() as session:
        fresh = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        fresh.voice_profile = new_profile
        await session.commit()

    await message.answer(
        f"✅ Профиль речи: <code>{new_profile.value}</code>. Следующие пинки будут в этом тоне."
    )


@router.message(Command("timezone"))
async def handle_timezone(
    message: types.Message,
    user: User | None,
    command: CommandObject,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await message.answer("Сначала зарегистрируйся — <code>/start</code>.")
        return

    arg = (command.args or "").strip()
    if not arg:
        await message.answer(
            f"Сейчас: <code>{user.timezone}</code>\n\n"
            "Формат: <code>/timezone Europe/Moscow</code>\n"
            "Поддерживаются все зоны из IANA tz database: "
            "<code>Europe/Moscow</code>, <code>Asia/Yekaterinburg</code>, "
            "<code>Europe/Berlin</code>, <code>America/New_York</code> и т.д."
        )
        return

    if arg not in available_timezones():
        # Cheap pre-check that gives a clearer error than ZoneInfo's
        # generic "no such file" exception.
        await message.answer(
            f"Не знаю зону <code>{arg}</code>. Полный список — "
            "<code>tz database</code> на Wikipedia."
        )
        return
    try:
        ZoneInfo(arg)
    except ZoneInfoNotFoundError:
        # Belt and braces — available_timezones() may include zones
        # the underlying tzdata package doesn't actually have.
        await message.answer(f"Зона <code>{arg}</code> известна но не загружается.")
        return

    async with session_factory() as session:
        fresh = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        fresh.timezone = arg
        await session.commit()

    await message.answer(
        f"✅ Часовой пояс: <code>{arg}</code>. События и quiet hours теперь считаются в этой зоне."
    )


@router.message(Command("quiet"))
async def handle_quiet(
    message: types.Message,
    user: User | None,
    command: CommandObject,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await message.answer("Сначала зарегистрируйся — <code>/start</code>.")
        return

    arg = (command.args or "").strip()
    if not arg:
        await message.answer(
            f"Сейчас: с <b>{user.quiet_from_hour:02d}:00</b> до "
            f"<b>{user.quiet_to_hour:02d}:00</b> по {user.timezone}.\n\n"
            "Формат: <code>/quiet &lt;from&gt; &lt;to&gt;</code> "
            "(часы 0-23, локальные).\n"
            "Пример: <code>/quiet 22 9</code> — молчу с 22:00 до 09:00.\n"
            "Окно может оборачивать полночь."
        )
        return

    parts = arg.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer(
            "Формат: <code>/quiet &lt;from&gt; &lt;to&gt;</code> — два числа 0-23."
        )
        return

    from_h, to_h = int(parts[0]), int(parts[1])
    if not (0 <= from_h <= 23) or not (0 <= to_h <= 23):
        await message.answer("Часы должны быть в диапазоне 0..23.")
        return
    if from_h == to_h:
        await message.answer(
            "Одинаковые часы — это «молчу 24/7». Если хочешь отключить "
            "пинки совсем, лучше <code>/forget</code> или попроси оператора."
        )
        return

    async with session_factory() as session:
        fresh = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        fresh.quiet_from_hour = from_h
        fresh.quiet_to_hour = to_h
        await session.commit()

    await message.answer(
        f"✅ Quiet hours: <b>{from_h:02d}:00</b> → <b>{to_h:02d}:00</b> "
        f"по <code>{user.timezone}</code>."
    )
