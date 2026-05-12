# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""RateLimiter against a real Redis (testcontainers).

Why a real Redis: the limiter's correctness hinges on Redis sorted-set
semantics (ZREMRANGEBYSCORE + ZCARD + ZADD in one pipeline). A fake
in-memory dict can implement this superficially but skips the only
thing we actually care about — that the algorithm works against the
production target.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest

testcontainers_redis = pytest.importorskip("testcontainers.redis")

from redis.asyncio import Redis  # noqa: E402
from testcontainers.redis import RedisContainer  # noqa: E402

from callendulla.core.rate_limit import RateLimiter  # noqa: E402


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer("redis:7-alpine") as redis:
        yield redis


@pytest.fixture
async def redis(redis_container: RedisContainer) -> AsyncIterator[Redis]:
    host = redis_container.get_container_host_ip()
    port = int(redis_container.get_exposed_port(6379))
    client = Redis(host=host, port=port, decode_responses=False)
    try:
        await client.flushall()  # isolation between tests
        yield client
    finally:
        await client.aclose()


class TestSlidingWindow:
    async def test_allows_below_limit(self, redis: Redis) -> None:
        limiter = RateLimiter(redis)
        for _ in range(5):
            result = await limiter.check(key="test:a", limit=5, window_seconds=10)
            assert result.allowed is True

    async def test_denies_once_over_limit(self, redis: Redis) -> None:
        limiter = RateLimiter(redis)
        for _ in range(3):
            assert (await limiter.check(key="t:b", limit=3, window_seconds=10)).allowed

        denied = await limiter.check(key="t:b", limit=3, window_seconds=10)
        assert denied.allowed is False
        assert denied.retry_after_seconds > 0

    async def test_does_not_count_denied_attempts(self, redis: Redis) -> None:
        """If a denied request added to the ZSET it would keep the
        bucket above threshold forever — exactly what we DON'T want."""
        limiter = RateLimiter(redis)
        for _ in range(2):
            await limiter.check(key="t:c", limit=2, window_seconds=10)
        # Three denials in a row — the ZSET stays at size 2, doesn't grow.
        for _ in range(3):
            await limiter.check(key="t:c", limit=2, window_seconds=10)
        size = await redis.zcard("t:c")
        assert size == 2

    async def test_keys_isolated(self, redis: Redis) -> None:
        limiter = RateLimiter(redis)
        for _ in range(3):
            await limiter.check(key="t:user-a", limit=3, window_seconds=10)
        # Different key — full budget.
        result = await limiter.check(key="t:user-b", limit=3, window_seconds=10)
        assert result.allowed is True

    async def test_zero_limit_means_disabled(self, redis: Redis) -> None:
        limiter = RateLimiter(redis)
        for _ in range(100):
            result = await limiter.check(key="t:off", limit=0, window_seconds=10)
            assert result.allowed is True
        # And nothing in Redis — the disabled path skips the call entirely.
        assert await redis.zcard("t:off") == 0

    async def test_window_actually_slides(self, redis: Redis) -> None:
        """After the window passes, the bucket frees up again."""
        limiter = RateLimiter(redis)
        # Use a very short window so the test stays fast.
        for _ in range(2):
            assert (await limiter.check(key="t:slide", limit=2, window_seconds=0.5)).allowed
        assert not (await limiter.check(key="t:slide", limit=2, window_seconds=0.5)).allowed
        await asyncio.sleep(0.6)
        # Window slid — fresh budget.
        assert (await limiter.check(key="t:slide", limit=2, window_seconds=0.5)).allowed


class TestRemainingHint:
    async def test_reports_remaining_capacity(self, redis: Redis) -> None:
        limiter = RateLimiter(redis)
        r1 = await limiter.check(key="t:rem", limit=3, window_seconds=10)
        assert r1.remaining == 2
        r2 = await limiter.check(key="t:rem", limit=3, window_seconds=10)
        assert r2.remaining == 1
        r3 = await limiter.check(key="t:rem", limit=3, window_seconds=10)
        assert r3.remaining == 0

    async def test_retry_after_present_on_deny(self, redis: Redis) -> None:
        limiter = RateLimiter(redis)
        for _ in range(2):
            await limiter.check(key="t:retry", limit=2, window_seconds=5)
        denied = await limiter.check(key="t:retry", limit=2, window_seconds=5)
        assert denied.allowed is False
        # Some non-trivial value — close to window length right after fill.
        assert 0 < denied.retry_after_seconds <= 5.0
