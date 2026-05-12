# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Sentry observability bootstrap.

Opt-in: nothing initialises unless the operator sets ``SENTRY_DSN``.
Tests verify that with no DSN the function is a true no-op — no
network, no global state mutation.

Why Sentry specifically: it covers error tracking + traces in one
free-tier-friendly product with first-party SDKs for every framework
we use (FastAPI, SQLAlchemy, Loguru). OTEL is more flexible but
needs a backend the operator runs themselves; we keep it as a
follow-up that operators with existing OTEL infra can add.

Token redact: we install the loguru patcher *before* Sentry attaches
to loguru. Sentry receives only redacted log payloads. Same goes for
events from our exception handlers — :func:`safe_repr` is the
standard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from callendulla._version import __version__

if TYPE_CHECKING:
    from collections.abc import Callable

    from callendulla.config import Settings


def init_observability(settings: Settings) -> bool:
    """Initialise Sentry if ``SENTRY_DSN`` is configured. Returns ``True``
    when Sentry was wired up, ``False`` otherwise (no DSN = no-op).

    Safe to call multiple times — Sentry's own ``init`` is idempotent
    enough for our purposes (process-global state, last call wins).
    """
    dsn_secret = settings.sentry_dsn
    if dsn_secret is None:
        logger.debug("SENTRY_DSN not set — observability disabled")
        return False

    dsn = dsn_secret.get_secret_value().strip()
    if not dsn:
        return False

    # Lazy import keeps sentry-sdk out of the import graph for tests
    # that don't configure a DSN.
    import sentry_sdk  # noqa: PLC0415
    from sentry_sdk.integrations.fastapi import FastApiIntegration  # noqa: PLC0415
    from sentry_sdk.integrations.loguru import LoguruIntegration  # noqa: PLC0415
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration  # noqa: PLC0415
    from sentry_sdk.integrations.starlette import StarletteIntegration  # noqa: PLC0415

    sentry_sdk.init(
        dsn=dsn,
        release=f"callendulla@{__version__}",
        # Conservative traces sample: 10% — enough to spot patterns
        # without burning the operator's quota. Operators with paid
        # plans can override via SENTRY_TRACES_SAMPLE_RATE in their
        # process env (Sentry SDK reads it).
        traces_sample_rate=0.1,
        # Don't ship the request body / form data — may contain
        # event titles, voice transcripts, anything user-typed.
        # PII is OFF by default in Sentry but we belt-and-braces.
        send_default_pii=False,
        attach_stacktrace=True,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
            LoguruIntegration(),
            SqlalchemyIntegration(),
        ],
        before_send=_strip_secrets,  # type: ignore[arg-type]
    )
    logger.info("sentry: initialised (release={})", f"callendulla@{__version__}")
    return True


def _strip_secrets(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Last-line defence: scrub secret-shaped strings from outgoing events.

    Sentry's own scrubber catches a lot, but ``safelog._redact`` knows
    our specific token patterns (Telegram bot, Fernet, etc.). We
    delegate to it here.
    """
    from callendulla.core.safelog import redact  # noqa: PLC0415

    # Walk the event dict; redact string leaves in place.
    return cast("dict[str, Any]", _scrub(event, redact))


def _scrub(value: Any, redact_fn: Callable[[str], str]) -> Any:
    """Recursively walk a JSON-ish structure and run ``redact_fn`` on
    every string. Lists / dicts traversed in place; scalars replaced."""
    if isinstance(value, dict):
        return {k: _scrub(v, redact_fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, redact_fn) for v in value]
    if isinstance(value, str):
        return redact_fn(value)
    return value
