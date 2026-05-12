# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Event CRUD: ``/add``, ``/list``, ``/delete``.

Minimum-viable syntax — no NLP, explicit grammar. NLP and natural
phrasing land later, once we have telemetry on what users actually
type.

Grammar:
- ``/add <title> ; <YYYY-MM-DD HH:MM>``  — semicolon separates title
  from the timestamp so titles can contain colons, spaces, anything
  except a literal `` ; ``. Timezone follows the user's
  ``User.timezone`` (set at registration).
- ``/list``                              — next 10 upcoming events
- ``/delete <id>``                       — soft same response for
  "not found" and "not yours" (cross-user safety)
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy.exc import IntegrityError, NoResultFound

from callendulla.db.models import User
from callendulla.db.repositories import EventRepository
from callendulla.db.session import SessionFactory

router = Router(name="events")


_HELP_ADD = (
    "Формат:\n"
    "<code>/add Название события ; ГГГГ-ММ-ДД ЧЧ:ММ</code>\n\n"
    "Пример:\n"
    "<code>/add Стендап ; 2026-06-01 10:00</code>\n\n"
    "Точка с запятой отделяет название от времени. Время — в твоём "
    "часовом поясе (<code>/me</code> покажет какой)."
)

_DT_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
)


def _parse_dt(raw: str, tz_name: str) -> datetime:
    """Parse user time string + apply user timezone.

    Returns a tz-aware datetime. Raises ``ValueError`` on unparsable
    input or invalid timezone (the latter should never happen — the
    timezone was validated at user registration time).
    """
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        msg = f"unknown timezone {tz_name!r}"
        raise ValueError(msg) from exc

    for fmt in _DT_FORMATS:
        try:
            naive = datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=tz)

    msg = f"could not parse {raw!r} as a date/time"
    raise ValueError(msg)


@router.message(Command("add"))
async def handle_add(
    message: types.Message,
    user: User | None,
    command: CommandObject,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await message.answer("Сначала зарегистрируйся — <code>/start</code>.")
        return
    if command.args is None or ";" not in command.args:
        await message.answer(_HELP_ADD)
        return

    title_raw, _, dt_raw = command.args.partition(";")
    title = title_raw.strip()
    if not title:
        await message.answer("Название не может быть пустым.\n\n" + _HELP_ADD)
        return

    try:
        dtstart = _parse_dt(dt_raw, user.timezone)
    except ValueError:
        await message.answer(
            f"Не распознал дату/время: <code>{dt_raw.strip()}</code>\n\n" + _HELP_ADD
        )
        return

    async with session_factory() as session:
        repo = EventRepository(session)
        try:
            event = await repo.create(
                owner_user_id=user.id,
                title=title,
                dtstart=dtstart,
                timezone=user.timezone,
            )
        except IntegrityError:
            await message.answer("Не удалось сохранить событие — попробуй ещё раз.")
            return

    local_dt = event.dtstart.astimezone(ZoneInfo(user.timezone))
    await message.answer(
        f"✅ Событие <b>#{event.id}</b> создано:\n"
        f"<i>{event.title}</i>\n"
        f"📅 {local_dt:%Y-%m-%d %H:%M} ({user.timezone})"
    )


@router.message(Command("list"))
async def handle_list(
    message: types.Message,
    user: User | None,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await message.answer("Сначала зарегистрируйся — <code>/start</code>.")
        return

    async with session_factory() as session:
        repo = EventRepository(session)
        events = await repo.list_for_owner(user.id)

    if not events:
        await message.answer("Событий пока нет. Добавь первое:\n\n" + _HELP_ADD)
        return

    user_tz = ZoneInfo(user.timezone)
    lines = ["<b>Твои ближайшие события:</b>"]
    for event in events:
        local = event.dtstart.astimezone(user_tz)
        lines.append(f"#{event.id} · {local:%Y-%m-%d %H:%M} · {event.title}")
    await message.answer("\n".join(lines))


@router.message(Command("delete"))
async def handle_delete(
    message: types.Message,
    user: User | None,
    command: CommandObject,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await message.answer("Сначала зарегистрируйся — <code>/start</code>.")
        return
    if command.args is None or not command.args.strip().isdigit():
        await message.answer(
            "Формат: <code>/delete &lt;id&gt;</code>\nID видно в <code>/list</code>."
        )
        return

    event_id = int(command.args.strip())
    async with session_factory() as session:
        repo = EventRepository(session)
        try:
            event = await repo.get_for_owner(user.id, event_id)
        except NoResultFound:
            # Same response for "not yours" and "doesn't exist" so we
            # don't leak ID existence across tenants.
            await message.answer(f"Событие #{event_id} не найдено.")
            return
        title = event.title
        await repo.delete_for_owner(user.id, event_id)

    await message.answer(f"🗑 Событие #{event_id} удалено: <i>{title}</i>")
