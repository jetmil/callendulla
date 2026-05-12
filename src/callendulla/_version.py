# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Single source of truth for the package version.

Read at import time by :mod:`callendulla.__init__`, by Alembic for migration
filenames, and by the AGPL §13 ``/source`` endpoint.
"""

from __future__ import annotations

__version__ = "0.1.0.dev0"
