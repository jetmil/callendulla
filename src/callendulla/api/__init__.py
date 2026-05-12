# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""FastAPI HTTP layer.

The :func:`create_app` factory builds a fully wired ASGI application:
- middleware stack (TrustedHost, CORS, AGPL X-Source-URL)
- routers (/health, /source, future feature endpoints)
- lifespan hooks (eager Settings read so misconfig fails fast)

Use :func:`create_app` from ``uvicorn`` / ``gunicorn`` entry-points and
from FastAPI ``TestClient`` in tests. A module-level :data:`app` is also
exported for the common ``uvicorn callendulla.api:app`` invocation.
"""

from callendulla.api.app import create_app

# ``app`` is a lazy attribute on :mod:`callendulla.api.app` — accessing
# it constructs the FastAPI instance on first use. See PEP 562 hook in
# that module. ``uvicorn callendulla.api.app:app`` resolves it correctly.
__all__ = ["create_app"]
