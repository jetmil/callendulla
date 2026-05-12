# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Render a list of :class:`Event` rows as RFC 5545 (iCalendar) text.

Output is what Google / Apple / Outlook calendars expect when they
fetch the per-user feed. Each event becomes one ``VEVENT`` block.
Recurrence (``Event.rrule``) is forwarded verbatim — the underlying
``icalendar`` library handles RFC compliance.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from icalendar import Calendar, Event as ICalEvent

from callendulla._version import __version__

if TYPE_CHECKING:
    from callendulla.db.models import Event

PRODID: str = f"-//callendulla {__version__}//jetmil/callendulla//RU"


def render_calendar(events: Iterable[Event], *, user_id: int) -> bytes:
    """Build a ``text/calendar`` byte payload for the given events.

    ``user_id`` is woven into each event UID to keep them stable
    across server reboots — a stale subscriber will still see the
    same event entries as last poll instead of duplicates.
    """
    # ``icalendar`` ships without type stubs — mypy can't infer the
    # constructor signature. The library is well-tested upstream.
    cal = Calendar()  # type: ignore[no-untyped-call]
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "Callendulla")

    for event in events:
        cal.add_component(_render_event(event, user_id=user_id))

    # ``icalendar.Calendar.to_ical`` returns ``bytes`` with CRLF
    # line endings as required by RFC 5545.
    return cal.to_ical()  # type: ignore[no-any-return]


def _render_event(event: Event, *, user_id: int) -> ICalEvent:
    ve = ICalEvent()  # type: ignore[no-untyped-call]
    # UID format ties an event to a specific Callendulla install +
    # owner, so two events with the same id from different forks /
    # operators never collide in a subscriber's calendar.
    ve.add("uid", f"callendulla-{user_id}-{event.id}@callendulla")
    ve.add("dtstamp", _ensure_aware(event.updated_at))
    ve.add("created", _ensure_aware(event.created_at))
    ve.add("last-modified", _ensure_aware(event.updated_at))
    ve.add("dtstart", _ensure_aware(event.dtstart))
    if event.dtend is not None:
        ve.add("dtend", _ensure_aware(event.dtend))
    else:
        # Default duration when only a start is given.
        ve.add("duration", timedelta(minutes=30))
    ve.add("summary", event.title)
    if event.description:
        ve.add("description", event.description)
    if event.rrule:
        ve.add("rrule", event.rrule)
    return ve


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; assume UTC if naive.

    Production Postgres preserves tz, so this is mostly a SQLite-test
    safety net. We document the assumption rather than silently break.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
