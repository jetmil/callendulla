# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Telegram webhook receiver.

Mounted onto the FastAPI app only when ``Settings.bot_mode == WEBHOOK``.
The endpoint sits at ``Settings.webhook_path`` — that path is obfuscation,
not security. The real defence is the
``X-Telegram-Bot-Api-Secret-Token`` header (available since Bot API 6.0,
2022): Telegram includes the operator-configured secret on every POST,
the server rejects anything that doesn't match.

Operator obligations in :mod:`SECURITY.md` §6 explain why the path goes
in nginx access logs while the header does not.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request, status
from loguru import logger

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher


# Header name, not a secret value — bandit/ruff S105 false positive.
WEBHOOK_SECRET_HEADER: str = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105


def build_webhook_router(
    bot: Bot,
    dispatcher: Dispatcher,
    secret_token: str,
    path: str,
) -> APIRouter:
    """Construct the webhook router for a given Bot/Dispatcher pair.

    The router exposes a single ``POST <path>`` endpoint that:

    1. Constant-time-compares the secret header against ``secret_token``.
       Constant-time avoids leaking the secret length through timing.
    2. Validates the JSON body against :class:`aiogram.types.Update`.
    3. Feeds it into the dispatcher, which runs middlewares + handlers.

    Errors during handler execution are logged but the endpoint always
    returns 200 — Telegram retries on non-2xx for up to 60 minutes,
    which would amplify any bug into a queue flood. The dispatcher's
    own error handling decides whether to send a chat reply.
    """

    router = APIRouter(tags=["telegram"])

    @router.post(path, status_code=status.HTTP_200_OK)
    async def telegram_update(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, str]:
        if x_telegram_bot_api_secret_token is None or not secrets.compare_digest(
            x_telegram_bot_api_secret_token, secret_token
        ):
            # 403 vs 401: Telegram never sends Authorization, so 401 is
            # the wrong code semantically. We use 403 to mean "the
            # header you sent doesn't match the configured secret".
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid webhook secret",
            )

        try:
            update = Update.model_validate(await request.json(), context={"bot": bot})
        except (ValueError, TypeError) as exc:
            # A malformed update is almost certainly an attacker, not
            # Telegram. Don't be helpful with the error message.
            logger.warning(
                "webhook: rejected malformed update body (exc_type={})",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid update payload",
            ) from None

        try:
            await dispatcher.feed_webhook_update(bot, update)
        except Exception:
            # Swallow exception, return 200 — see docstring.
            logger.exception("webhook: handler raised; returning 200 to avoid TG retry storm")

        return {"ok": "true"}

    return router
