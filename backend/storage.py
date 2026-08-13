"""In-memory storage backend — a drop-in stand-in for Redis.

Redis exists in this app to solve a problem a local, single-user run doesn't
have: coordinating session/swap state across *multiple concurrent users*
hitting one shared server. A local desktop build has neither multiple users
nor a separate server process, so it doesn't need Redis at all — a plain
in-process dict does the same job with one less thing to install and run.

This module implements the small slice of the Redis client interface the
app actually uses (`get`, `setex`, `ttl`, `delete`, `ping`, `set`) against an
in-memory dict, plus an async-compatible wrapper so it can stand in for
either the sync or async Redis client in app.py without any changes to the
business-logic code that calls them — see `STORAGE_BACKEND` in config.py and
`setup_storage_backends()` in app.py for how the two backends are selected.
"""

from __future__ import annotations

import math
import threading
import time


class InMemoryStorage:
    """Thread-safe in-memory key/value store with Redis-compatible TTL semantics."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at is not None and time.time() >= expires_at:
                del self._data[key]
                return None
            return value

    def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        with self._lock:
            self._data[key] = (value, time.time() + ttl_seconds)

    def set(self, key: str, value: str) -> None:
        """Store a value with no expiry (used only by the /test-redis dev route)."""
        with self._lock:
            self._data[key] = (value, None)

    def ttl(self, key: str) -> int:
        """Match redis-py's TTL semantics: -2 if missing, -1 if no expiry, else seconds left."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return -2
            _value, expires_at = entry
            if expires_at is None:
                return -1
            remaining = expires_at - time.time()
            if remaining <= 0:
                del self._data[key]
                return -2
            # Round up rather than truncate: a key with 0.3s genuinely left
            # should report 1, not 0 (which would read as "already expired").
            return math.ceil(remaining)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def ping(self) -> bool:
        return True


class InMemoryStorageAsync:
    """Async-compatible wrapper around a shared InMemoryStorage instance.

    An in-memory dict has nothing to actually await, but the FastAPI route
    handlers call their storage client with `await` (to match the real async
    Redis client). This wraps each method so the same calling convention
    works unmodified regardless of which backend is active.
    """

    def __init__(self, store: InMemoryStorage) -> None:
        self._store = store

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        self._store.setex(key, ttl_seconds, value)

    async def set(self, key: str, value: str) -> None:
        self._store.set(key, value)

    async def ttl(self, key: str) -> int:
        return self._store.ttl(key)

    async def delete(self, key: str) -> None:
        self._store.delete(key)

    async def ping(self) -> bool:
        return self._store.ping()
