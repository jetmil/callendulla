# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Liveness / readiness probes.

``/health`` is intentionally cheap — it does NOT touch the database. The
docker-compose ``healthcheck`` calls it on every interval, and a heavy
implementation would amplify a DB blip into a process restart.

Readiness vs liveness:
- ``/health``        — liveness: the process is responsive
- ``/health/ready``  — readiness: DB and Redis reachable (lands later)
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])

_STATUS_OK: Final[str] = "ok"


class HealthResponse(BaseModel):
    status: str


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    return HealthResponse(status=_STATUS_OK)
