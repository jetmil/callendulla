# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Repository pattern over SQLAlchemy models.

Each repository owns its scope — :class:`EventRepository` always
filters by ``owner_user_id``, so a missing owner check at the call
site cannot accidentally surface another user's events. This is the
project's cross-user isolation primitive.
"""

from callendulla.db.repositories.event import EventRepository

__all__ = ["EventRepository"]
