# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``/help`` — short command reference.

Kept terse on purpose: detailed docs live in the repo, not in chat.
"""

from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import Command

router = Router(name="help")


_HELP_TEXT = (
    "<b>Callendulla — справка</b>\n\n"
    "<b>События:</b>\n"
    "• <code>/add Название ; ГГГГ-ММ-ДД ЧЧ:ММ</code> — создать\n"
    "• <code>/list</code> — ближайшие события\n"
    "• <code>/delete &lt;id&gt;</code> — удалить по id из /list\n\n"
    "<b>Календарная подписка:</b>\n"
    "• <code>/ical</code> — URL для Google / Apple / Outlook\n"
    "• <code>/rotate_ical</code> — сменить токен подписки\n\n"
    "<b>Общее:</b>\n"
    "• <code>/start</code> — приветствие, проверка регистрации\n"
    "• <code>/source</code> — открытый исходник, версия, коммит\n"
    "• <code>/help</code> — эта справка\n\n"
    "<b>Скоро:</b> голосовой дневник, выбор характера, профиля речи.\n\n"
    "Документация: https://github.com/jetmil/callendulla"
)


@router.message(Command("help"))
async def handle_help(message: types.Message) -> None:
    await message.answer(_HELP_TEXT, disable_web_page_preview=True)
