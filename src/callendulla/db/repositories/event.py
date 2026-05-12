# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``EventRepository`` — CRUD scoped by owner_user_id.

Cross-user isolation is enforced *here*, not at the call site. Every
public method takes ``owner_user_id`` as a first-class argument and
adds it to the WHERE clause. A handler that forgets to pass the owner
will get a type error, not a security incident.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.exc import NoResultFound

from callendulla.db.models import Event, Trigger, TriggerKind, TriggerState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class EventRepository:
    """Owner-scoped operations on the :class:`Event` table.

    All methods take an explicit ``owner_user_id`` — there is no
    "current user" convenience. The handler that calls us already has
    a ``User`` instance from :class:`UserMiddleware`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        owner_user_id: int,
        title: str,
        dtstart: datetime,
        description: str | None = None,
        dtend: datetime | None = None,
        timezone: str = "Europe/Moscow",
        rrule: str | None = None,
    ) -> Event:
        """Create an event with a default one-shot trigger at ``dtstart``.

        The trigger is created so the scheduler has something to fire.
        Subsequent PRs add per-event multi-trigger configuration
        (e.g. ``-1 day``, ``-1 hour``, ``at start``).
        """
        event = Event(
            owner_user_id=owner_user_id,
            title=title,
            description=description,
            dtstart=dtstart,
            dtend=dtend,
            timezone=timezone,
            rrule=rrule,
            is_active=True,
        )
        self._session.add(event)
        # Flush so the event has an id we can use for the trigger FK
        # without an extra round-trip.
        await self._session.flush()

        trigger = Trigger(
            event_id=event.id,
            kind=TriggerKind.ONESHOT,
            state=TriggerState.PENDING,
            schedule_spec=dtstart.isoformat(),
            next_fire_at=dtstart,
        )
        self._session.add(trigger)
        await self._session.commit()
        await self._session.refresh(event)
        return event

    async def list_for_owner(
        self,
        owner_user_id: int,
        *,
        active_only: bool = True,
        limit: int = 10,
    ) -> list[Event]:
        stmt = (
            select(Event)
            .where(Event.owner_user_id == owner_user_id)
            .order_by(Event.dtstart.asc())
            .limit(limit)
        )
        if active_only:
            stmt = stmt.where(Event.is_active.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def get_for_owner(self, owner_user_id: int, event_id: int) -> Event:
        """Return the event or raise :class:`NoResultFound`.

        Raising on miss (not returning ``None``) gives the handler a
        single code path: catch ``NoResultFound`` → "not found or not
        yours". Same response for both cases is a feature — it leaks
        nothing about other users' event IDs.
        """
        stmt = select(Event).where(
            Event.id == event_id,
            Event.owner_user_id == owner_user_id,
        )
        result = await self._session.execute(stmt)
        event = result.scalar_one_or_none()
        if event is None:
            raise NoResultFound(f"event {event_id} not found for owner {owner_user_id}")
        return event

    async def delete_for_owner(self, owner_user_id: int, event_id: int) -> bool:
        """Delete one event; return ``True`` if a row was removed.

        ``False`` covers both "no such id" and "not yours" — same as
        :meth:`get_for_owner` we don't disambiguate to avoid leaking
        ID existence across tenants.
        """
        stmt = (
            delete(Event)
            .where(
                Event.id == event_id,
                Event.owner_user_id == owner_user_id,
            )
            .returning(Event.id)
        )
        result = await self._session.execute(stmt)
        deleted = result.scalar_one_or_none() is not None
        await self._session.commit()
        return deleted

    async def count_for_owner(self, owner_user_id: int) -> int:
        from sqlalchemy import func  # noqa: PLC0415 — local to keep API clean

        stmt = select(func.count()).select_from(Event).where(Event.owner_user_id == owner_user_id)
        return (await self._session.execute(stmt)).scalar_one()
