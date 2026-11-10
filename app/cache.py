"""Process-local asynchronous TTL cache utilities."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Hashable, TypeVar

T = TypeVar("T")


@dataclass
class _CacheEntry(Generic[T]):
    value: T
    expires_at: float


@dataclass
class _KeyLock:
    lock: asyncio.Lock
    users: int = 0


class AsyncTTLCache:
    """A concurrency-safe, process-local cache with per-key request coalescing."""

    def __init__(self, ttl_seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[Hashable, _CacheEntry[object]] = {}
        self._key_locks: dict[Hashable, _KeyLock] = {}
        self._guard = asyncio.Lock()

    async def get(self, key: Hashable) -> T | None:
        async with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._entries[key]
                return None
            return entry.value  # type: ignore[return-value]

    async def set(self, key: Hashable, value: T, *, ttl_seconds: float | None = None) -> None:
        ttl = self._ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        async with self._guard:
            self._entries[key] = _CacheEntry(value=value, expires_at=self._clock() + ttl)

    async def get_or_set(
        self, key: Hashable, factory: Callable[[], Awaitable[T]], *, ttl_seconds: float | None = None
    ) -> T:
        cached = await self.get(key)
        if cached is not None:
            return cached

        lock_state = await self._borrow_key_lock(key)
        try:
            async with lock_state.lock:
                cached = await self.get(key)
                if cached is not None:
                    return cached
                value = await factory()
                await self.set(key, value, ttl_seconds=ttl_seconds)
                return value
        finally:
            await self._return_key_lock(key, lock_state)

    async def _borrow_key_lock(self, key: Hashable) -> _KeyLock:
        async with self._guard:
            state = self._key_locks.setdefault(key, _KeyLock(lock=asyncio.Lock()))
            state.users += 1
            return state

    async def _return_key_lock(self, key: Hashable, state: _KeyLock) -> None:
        async with self._guard:
            state.users -= 1
            if state.users == 0 and self._key_locks.get(key) is state:
                del self._key_locks[key]

