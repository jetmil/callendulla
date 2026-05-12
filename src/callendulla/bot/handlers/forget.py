# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``/forget`` — GDPR-style right-to-be-forgotten.

Two-step flow:

1. ``/forget`` → confirmation message with two inline buttons.
2. Tap "🔥 Да, удалить всё" → cascade-delete the user row. All related
   data (events, triggers, nudge_logs, voice_diary) goes too —
   ``ondelete="CASCADE"`` is declared on every FK back to ``users``.

We log the deletion *intent* (Telegram id only, no PII) so an operator
investigating "where did Alice go?" can find a paper trail. The
content of Alice's calendar/diary is irrecoverable after the cascade.
"""

from __future__ import annotations

from contextlib import suppress

from aiogram import Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from loguru import logger
from sqlalchemy import delete

from callendulla.bot.keyboards import (
    FORGET_CANCEL_CALLBACK,
    FORGET_CONFIRM_CALLBACK,
    forget_confirm_keyboard,
)
from callendulla.db.models import User
from callendulla.db.session import SessionFactory

router = Router(name="forget")


_WARNING = (
    "⚠️ <b>Удалить весь аккаунт?</b>\n\n"
    "Будут <b>безвозвратно</b> стёрты:\n"
    "• все события и триггеры\n"
    "• история пинков и реакций\n"
    "• голосовой дневник и расшифровки\n"
    "• твой профиль на этом инстансе\n\n"
    "Это действие нельзя отменить. iCal-подписка перестанет работать.\n\n"
    "Если ты уверен — жми «🔥 Да, удалить всё»."
)


@router.message(Command("forget"))
async def handle_forget(message: types.Message, user: User | None) -> None:
    if user is None:
        # Anonymous caller — nothing to forget.
        await message.answer("На этом инстансе тебя и так нет — регистрироваться не пробовал.")
        return
    await message.answer(_WARNING, reply_markup=forget_confirm_keyboard())


@router.callback_query(lambda cq: cq.data == FORGET_CANCEL_CALLBACK)
async def handle_forget_cancel(
    callback_query: types.CallbackQuery,
    user: User | None,
) -> None:
    await callback_query.answer("Отменено", show_alert=False)
    if isinstance(callback_query.message, types.Message):
        # Best-effort UI cleanup — message may be too old to edit.
        with suppress(TelegramAPIError):
            await callback_query.message.edit_text("Удаление отменено. Ничего не тронуто.")


@router.callback_query(lambda cq: cq.data == FORGET_CONFIRM_CALLBACK)
async def handle_forget_confirm(
    callback_query: types.CallbackQuery,
    user: User | None,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await callback_query.answer("Тебя нет в системе — нечего удалять.", show_alert=False)
        return

    user_pk = user.id
    user_tg_id = user.tg_id

    async with session_factory() as session:
        # CASCADE FKs propagate the delete to events/triggers/
        # nudge_logs/voice_diary. nudge_cache is intentionally not
        # FK-linked to users (cross-user-safe by design) so it stays.
        await session.execute(delete(User).where(User.id == user_pk))
        await session.commit()

    logger.warning(
        "/forget: erased user_id={user_id} tg_id={tg_id} via GDPR request",
        user_id=user_pk,
        tg_id=user_tg_id,
    )

    await callback_query.answer("Аккаунт удалён.", show_alert=False)
    if isinstance(callback_query.message, types.Message):
        with suppress(TelegramAPIError):
            await callback_query.message.edit_text(
                "🔥 Готово. Твой профиль и связанные данные удалены.\n\n"
                "Чтобы начать заново — <code>/start</code> (если регистрация "
                "на этом инстансе открыта)."
            )
