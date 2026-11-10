import asyncio

import pytest

from app.cache import AsyncTTLCache


@pytest.mark.asyncio
async def test_cache_returns_value_until_entry_expires() -> None:
    cache = AsyncTTLCache(ttl_seconds=0.02)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    assert await cache.get_or_set("weather:rome", factory) == "value-1"
    assert await cache.get_or_set("weather:rome", factory) == "value-1"
    assert calls == 1

    await asyncio.sleep(0.03)
    assert await cache.get_or_set("weather:rome", factory) == "value-2"
    assert calls == 2


@pytest.mark.asyncio
async def test_cache_coalesces_concurrent_misses() -> None:
    cache = AsyncTTLCache(ttl_seconds=60)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "cached"

    results = await asyncio.gather(*(cache.get_or_set("places:rome", factory) for _ in range(10)))
    assert results == ["cached"] * 10
    assert calls == 1
