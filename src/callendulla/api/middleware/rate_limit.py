# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""HTTP rate-limit middleware.

Applies a per-IP sliding window to ``/ical/`` only; other paths
pass through.

We do NOT rate-limit the webhook endpoint: Telegram is the only
caller, the ``X-Telegram-Bot-Api-Secret-Token`` header is the
actual gate, and an abusive request without the secret already
gets 403 before any work happens.

Operators can disable the whole layer by setting
``ICAL_RATE_LIMIT_PER_IP_HOURLY=0``.

Behind a trusted reverse proxy that sets ``X-Forwarded-For``,
operator should layer
:class:`uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware`
*outside* this one — it rewrites ``request.client.host``. We do
NOT trust ``X-Forwarded-For`` blind — spoofable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

    from callendulla.core.rate_limit import RateLimiter


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding window on ``/ical/`` paths."""

    def __init__(
        self,
        app: object,
        *,
        limiter: RateLimiter,
        ical_per_ip_hourly: int,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._limiter = limiter
        self._ical_per_ip_hourly = ical_per_ip_hourly

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not request.url.path.startswith("/ical/") or self._ical_per_ip_hourly <= 0:
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        key = f"rl:ical:{ip}"
        result = await self._limiter.check(
            key=key, limit=self._ical_per_ip_hourly, window_seconds=3600.0
        )
        if not result.allowed:
            retry = max(1, int(result.retry_after_seconds))
            logger.info(
                "rate-limit: denied {key} (limit={limit}/3600s, retry={retry}s)",
                key=key,
                limit=self._ical_per_ip_hourly,
                retry=retry,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(retry)},
            )

        return await call_next(request)
