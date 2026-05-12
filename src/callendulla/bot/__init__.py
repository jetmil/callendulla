# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Telegram bot — aiogram 3 dispatcher and handlers.

The factory pair :func:`create_bot` + :func:`create_dispatcher` mirrors
the API layer: tests build their own instances against fake sessions,
production wires once at startup.
"""

from callendulla.bot.main import create_bot, create_dispatcher

__all__ = ["create_bot", "create_dispatcher"]
