# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``/ical`` and ``/rotate_ical`` commands.

``/ical`` shows the current subscribe URL.
``/rotate_ical`` regenerates the token, invalidating any old
subscribers. Operator points ``WEB_BASE_URL`` at their public host
in ``.env``; without it the URL is incomplete and the bot says so.
"""

from __future__ import annotations

import secrets

from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select

from callendulla.config import Settings
from callendulla.db.models import User
from callendulla.db.session import SessionFactory

router = Router(name="ical")


def _ical_path(token: str) -> str:
    return f"/ical/{token}"


def _full_url(settings: Settings, token: str) -> str | None:
    if settings.web_base_url is None:
        return None
    return f"{str(settings.web_base_url).rstrip('/')}{_ical_path(token)}"


@router.message(Command("ical"))
async def handle_ical(
    message: types.Message,
    user: User | None,
    settings: Settings,
) -> None:
    if user is None:
        await message.answer("Сначала зарегистрируйся — <code>/start</code>.")
        return

    url = _full_url(settings, user.ical_token)
    if url is None:
        await message.answer(
            "WEB_BASE_URL не настроен на этом инстансе. Оператор должен "
            "выставить его в <code>.env</code>, чтобы iCal-фид работал."
        )
        return

    await message.answer(
        "<b>Подключение календаря</b>\n\n"
        f"Скопируй URL и добавь в Google / Apple / Outlook как "
        f"подписку на календарь:\n\n<code>{url}</code>\n\n"
        "Сменить URL (например, если он утёк) — <code>/rotate_ical</code>.",
        disable_web_page_preview=True,
    )


@router.message(Command("rotate_ical"))
async def handle_rotate_ical(
    message: types.Message,
    user: User | None,
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await message.answer("Сначала зарегистрируйся — <code>/start</code>.")
        return

    new_token = secrets.token_hex(16)
    async with session_factory() as session:
        # Re-fetch the user inside the session for a clean UPDATE.
        fresh = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        fresh.ical_token = new_token
        await session.commit()

    url = _full_url(settings, new_token)
    if url is None:
        await message.answer("Токен обновлён, но WEB_BASE_URL не настроен — попроси оператора.")
        return

    await message.answer(
        "<b>Токен обновлён.</b> Старая подписка перестанет работать.\n\n"
        f"Новый URL:\n\n<code>{url}</code>",
        disable_web_page_preview=True,
    )
