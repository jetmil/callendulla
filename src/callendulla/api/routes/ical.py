# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""iCal subscription endpoint: ``GET /ical/{token}``.

Authentication is the URL itself — the token is a 32-hex random per
user, treated as a bearer credential. Loss of the URL == loss of
read access; the user can rotate via :func:`bot.handlers.ical.handle_rotate_ical`.

Why a path token instead of a query param:
- it survives URL-shorteners and copy-paste better
- standard pattern for iCalendar subscribe URLs (Google/Apple support both)

What we do NOT do:
- log the token on hits (would defeat the secrecy purpose) —
  ``callendulla.core.safelog.redact`` masks ``token=`` patterns; the
  route uses a path parameter, not a query, so the access log only
  records the *URL path*. Operator's nginx config should NOT log
  the full path of ``/ical/*`` — see SECURITY.md note (added in this PR).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from callendulla.api.ical_render import render_calendar
from callendulla.db.models import Event, User
from callendulla.db.session import get_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["ical"])


@router.get(
    "/ical/{token}",
    response_class=Response,
    responses={
        200: {"content": {"text/calendar": {}}},
        404: {"description": "Token unknown or revoked"},
    },
    summary="Subscribe URL for Google / Apple / Outlook calendars",
)
async def ical_feed(
    token: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008 — FastAPI DI pattern
) -> Response:
    user = (
        await session.execute(
            select(User).where(User.ical_token == token).options(selectinload(User.events))
        )
    ).scalar_one_or_none()

    if user is None:
        # 404 not 401: the URL is the auth, so an unknown token is
        # indistinguishable from a typo'd path. Same status keeps
        # token-existence opaque.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    # Filter to active events; the user explicitly disabled inactive
    # ones and the subscriber expects a current calendar.
    events: list[Event] = [e for e in user.events if e.is_active]
    body = render_calendar(events, user_id=user.id)

    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            # Calendar clients re-fetch on their own schedule; tell them
            # how to cache.
            "Cache-Control": "private, max-age=300",
            # Subscribe filename — Google / Apple use it as the default
            # calendar name unless x-wr-calname overrides.
            "Content-Disposition": 'inline; filename="callendulla.ics"',
        },
    )
