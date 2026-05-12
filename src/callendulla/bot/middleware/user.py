# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``UserMiddleware`` — resolves the Telegram identity to a :class:`User` row.

Runs on every update before handlers. Behaviour:

- find ``User`` by ``tg_id``
- if absent and ``REGISTRATION_MODE`` allows, create one
  (whitelist / invite enforcement lives in this middleware)
- promote to ``OWNER`` when the Telegram id matches
  ``Settings.owner_tg_id``
- attach the resolved (or ``None``) user to ``data["user"]``

Cross-user safety: this middleware is the *only* place where a TG
identity is mapped to a database row. Downstream handlers MUST use
``data["user"]`` to identify the caller, not re-query by ``tg_id``.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy import select

from callendulla.config import RegistrationMode, Settings
from callendulla.db.models import User, UserRole, UserStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from callendulla.db.session import SessionFactory


def _generate_ical_token() -> str:
    """Opaque 32-hex token (≈128 bits) for the per-user iCal feed URL."""
    return secrets.token_hex(16)


def _allowed_to_register(tg_id: int, tg_username: str | None, settings: Settings) -> bool:
    """Apply ``REGISTRATION_MODE`` policy to first-contact users."""
    if tg_id == settings.owner_tg_id:
        # Owner can always register themselves; bootstrap problem otherwise.
        return True
    mode = settings.registration_mode
    if mode is RegistrationMode.OPEN:
        return True
    if mode is RegistrationMode.WHITELIST:
        if tg_username is None:
            return False
        return tg_username.lstrip("@").lower() in {
            name.lstrip("@").lower() for name in settings.whitelist_tg_usernames
        }
    # INVITE — not handled in middleware; only existing owners/members
    # can issue invites via a future /invite command, and that flow
    # creates the row itself. First-contact INVITE users are rejected.
    return False


class UserMiddleware(BaseMiddleware):
    def __init__(self, session_factory: SessionFactory, settings: Settings) -> None:
        super().__init__()
        self._session_factory = session_factory
        self._settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # We only register this middleware on dispatcher.update, so in
        # practice ``event`` is always an :class:`Update`. The cast keeps
        # the parent-class LSP-compatible signature while letting us
        # work with Update-specific attributes.
        update = event if isinstance(event, Update) else None
        tg_user = self._extract_tg_user(update) if update is not None else None
        if tg_user is None:
            data["user"] = None
            return await handler(event, data)

        async with self._session_factory() as session:
            stmt = select(User).where(User.tg_id == tg_user["id"])
            user = (await session.execute(stmt)).scalar_one_or_none()

            if user is None and _allowed_to_register(
                tg_user["id"], tg_user.get("username"), self._settings
            ):
                user = User(
                    tg_id=tg_user["id"],
                    tg_username=tg_user.get("username"),
                    display_name=tg_user.get("display_name"),
                    role=(
                        UserRole.OWNER
                        if tg_user["id"] == self._settings.owner_tg_id
                        else UserRole.MEMBER
                    ),
                    status=UserStatus.ACTIVE,
                    timezone=self._settings.default_timezone,
                    quiet_from_hour=self._settings.quiet_from_hour,
                    quiet_to_hour=self._settings.quiet_to_hour,
                    ical_token=_generate_ical_token(),
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
            elif (
                user is not None
                and user.tg_id == self._settings.owner_tg_id
                and user.role is not UserRole.OWNER
            ):
                # Promote previously-existing member to owner if env now
                # designates them. Demoting in the other direction needs
                # explicit ops action — we don't do it automatically.
                user.role = UserRole.OWNER
                await session.commit()
                await session.refresh(user)

            data["user"] = user

        return await handler(event, data)

    @staticmethod
    def _extract_tg_user(event: Update) -> dict[str, Any] | None:
        """Pull the originating TG user out of any update kind we handle."""
        candidates = (
            event.message,
            event.callback_query,
            event.edited_message,
            event.my_chat_member,
        )
        for source in candidates:
            if source is None or source.from_user is None:
                continue
            tg_user = source.from_user
            display = " ".join(filter(None, [tg_user.first_name, tg_user.last_name])) or None
            return {
                "id": tg_user.id,
                "username": tg_user.username,
                "display_name": display,
            }
        return None
