# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""AGPL §13 ``X-Source-URL`` header injector.

§13 of AGPL-3.0 obliges operators of a modified network-service version
to offer users the corresponding source. The mechanism we implement
here is one of the two recognised ones (the other is the ``/source``
endpoint): every HTTP response carries ``X-Source-URL`` pointing at the
repository where users can fetch the deployed version's source.

Operators who fork and modify MUST set ``AGPL_SOURCE_URL`` to their
fork — keeping the upstream URL while running modifications is a §13
violation. ``docs/agpl-compliance.md`` spells out the obligation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response


SOURCE_HEADER: str = "X-Source-URL"


class AGPLSourceHeaderMiddleware(BaseHTTPMiddleware):
    """Add ``X-Source-URL: <agpl_source_url>`` to every response.

    Deliberately minimal — Starlette's ``BaseHTTPMiddleware`` interface
    keeps it a one-liner. Header name is also exported as
    :data:`SOURCE_HEADER` so tests reference a single constant.
    """

    def __init__(self, app: object, source_url: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._source_url = source_url

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers[SOURCE_HEADER] = self._source_url
        return response
