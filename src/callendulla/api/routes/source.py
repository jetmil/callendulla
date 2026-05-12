# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""AGPL §13 ``/source`` endpoint.

Returns the deployed version's source URL, commit SHA, and build date —
machine-readable counterpart of the :class:`AGPLSourceHeaderMiddleware`
header. Operators of forks MUST keep this endpoint (or a moved
equivalent) reachable; see ``docs/agpl-compliance.md``.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter
from pydantic import BaseModel, HttpUrl

from callendulla.config import get_settings
from callendulla.core.version import build_date, commit_sha, package_version

router = APIRouter(tags=["meta"])

_LICENSE: Final[str] = "AGPL-3.0-or-later"


class SourceResponse(BaseModel):
    license: str
    source_url: HttpUrl
    commit_sha: str
    version: str
    build_date: str


@router.get(
    "/source",
    response_model=SourceResponse,
    summary="AGPL §13 source disclosure",
)
async def source() -> SourceResponse:
    settings = get_settings()
    return SourceResponse(
        license=_LICENSE,
        source_url=HttpUrl(str(settings.agpl_source_url)),
        commit_sha=commit_sha(),
        version=package_version(),
        build_date=build_date(),
    )
