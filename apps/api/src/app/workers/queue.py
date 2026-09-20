"""Small ARQ pool wrapper shared by API outbox relays and tests."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool

_pool: ArqRedis | None = None
_pool_url: str | None = None
_pool_lock = asyncio.Lock()


def redis_settings(redis_url: str) -> RedisSettings:
    """Parse the configured Redis URL without logging credentials."""

    return RedisSettings.from_dsn(redis_url)


async def get_arq_pool(redis_url: str) -> ArqRedis:
    """Create one process-local ARQ pool for the requested Redis endpoint."""

    global _pool, _pool_url
    if _pool is not None and _pool_url == redis_url:
        return _pool

    async with _pool_lock:
        if _pool is not None and _pool_url != redis_url:
            await close_arq_pool()
        if _pool is None:
            _pool = await create_pool(redis_settings(redis_url))
            _pool_url = redis_url
    return _pool


async def close_arq_pool() -> None:
    """Close the process-local pool during API or worker shutdown."""

    global _pool, _pool_url
    if _pool is None:
        return
    close_method = getattr(_pool, "aclose", None) or getattr(_pool, "close", None)
    if close_method is not None:
        result = close_method()
        if inspect.isawaitable(result):
            await result
    _pool = None
    _pool_url = None


async def enqueue_job(
    redis_url: str,
    function_name: str,
    *args: Sequence[Any] | Any,
    **kwargs: Any,
) -> Any:
    """Enqueue a function while keeping all job payloads explicit and small."""

    pool = await get_arq_pool(redis_url)
    return await pool.enqueue_job(function_name, *args, **kwargs)


__all__ = ["close_arq_pool", "enqueue_job", "get_arq_pool", "redis_settings"]
