# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``/start`` — onboarding message + registration-mode awareness.

By the time this handler runs, :class:`UserMiddleware` has already
either created the user or set ``data["user"] = None`` if registration
was refused. We just shape the message accordingly.
"""

from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import CommandStart

from callendulla.db.models import User, UserRole

router = Router(name="start")


_WELCOME_OWNER = (
    "Я — Callendulla, твой календарь-нянька.\n"
    "Ты вошёл как <b>owner</b>: у тебя полный доступ ко всему.\n\n"
    "Что я умею (уже сейчас):\n"
    "• <code>/help</code> — подсказка по командам\n"
    "• <code>/source</code> — открытый исходник + версия\n\n"
    "Календарные команды (события, напоминания) подключатся "
    "в следующих обновлениях. Спасибо что self-host'ишь!"
)

_WELCOME_MEMBER = (
    "Я — Callendulla, твой календарь-нянька.\n"
    "Ты — <b>member</b> этой инсталляции.\n\n"
    "Доступные команды:\n"
    "• <code>/help</code> — список команд\n"
    "• <code>/source</code> — открытый исходник\n\n"
    "Календарные функции подключатся в следующих обновлениях."
)

_REGISTRATION_REFUSED = (
    "Этот сервер не принимает новые регистрации.\n"
    "Связь с владельцем инстанса — через того, кто прислал тебе ссылку.\n\n"
    "Если ты тут по приглашению — попроси выслать <code>/invite</code>-код "
    "от существующего пользователя."
)


@router.message(CommandStart())
async def handle_start(message: types.Message, user: User | None) -> None:
    if user is None:
        await message.answer(_REGISTRATION_REFUSED)
        return
    if user.role is UserRole.OWNER:
        await message.answer(_WELCOME_OWNER)
    else:
        await message.answer(_WELCOME_MEMBER)
