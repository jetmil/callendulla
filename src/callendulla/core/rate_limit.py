# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Sliding-window rate limiter backed by Redis.

Algorithm: per key, store request timestamps in a Redis sorted set
keyed by the millisecond. Each check:

1. ZREMRANGEBYSCORE drops entries older than the window.
2. ZCARD counts what's left.
3. If the count is ≥ ``limit``, deny. Otherwise ZADD the current
   timestamp and EXPIRE the key.

The whole sequence runs as a single MULTI/EXEC pipeline so two
concurrent requests can't both ZADD past the threshold. Drift between
the Redis clock and the client clock matters only for the
microsecond-scale boundary, not the rate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    """How many further requests fit in the current window."""

    retry_after_seconds: float
    """Hint for ``Retry-After`` header when ``allowed`` is False; 0 otherwise."""


class RateLimiter:
    """Sliding-window limiter.

    Construct once per app, share across requests. Stateless beyond
    the Redis connection.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: float,
    ) -> RateLimitResult:
        if limit <= 0:
            # ``limit=0`` would deny everyone; treat as "rate-limit off".
            return RateLimitResult(allowed=True, remaining=0, retry_after_seconds=0.0)

        now = time.time()
        cutoff = now - window_seconds
        member = f"{now:.6f}"  # uniquify members so ZADD doesn't dedup

        pipe = self._redis.pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        pipe.zadd(key, {member: now})
        pipe.expire(key, int(window_seconds) + 1)
        _, prior_count, *_ = await pipe.execute()

        # ``prior_count`` is the size BEFORE our ZADD. Adding ours makes
        # the new count prior_count + 1. We allow up to ``limit``.
        if prior_count >= limit:
            # Remove the entry we just added — over the limit, do not
            # count this attempt. Otherwise a flood would keep itself
            # over the threshold forever.
            await self._redis.zrem(key, member)
            # When does the oldest entry expire and free a slot?
            oldest = await self._redis.zrange(key, 0, 0, withscores=True)
            retry_after = 0.0
            if oldest:
                _, oldest_score = oldest[0]
                retry_after = max(0.0, (oldest_score + window_seconds) - now)
            return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=retry_after)

        return RateLimitResult(
            allowed=True,
            remaining=limit - (prior_count + 1),
            retry_after_seconds=0.0,
        )
