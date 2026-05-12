# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``/source`` — AGPL §13 disclosure inside Telegram.

Counterpart of the HTTP ``/source`` endpoint. Users who don't have web
access to the operator's domain still need to discover the source URL —
TG is the channel they already have.
"""

from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import Command

from callendulla.api.version import build_date, commit_sha, package_version
from callendulla.config import Settings

router = Router(name="source")


@router.message(Command("source"))
async def handle_source(message: types.Message, settings: Settings) -> None:
    text = (
        f"<b>Callendulla</b>\n"
        f"License: <code>AGPL-3.0-or-later</code>\n"
        f"Source: {settings.agpl_source_url}\n"
        f"Version: <code>{package_version()}</code>\n"
        f"Commit: <code>{commit_sha()}</code>\n"
        f"Built: <code>{build_date()}</code>"
    )
    await message.answer(text, disable_web_page_preview=True)
