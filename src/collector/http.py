"""Shared HTTP client: honest user agent, timeouts, one request at a time per host."""

from __future__ import annotations

import asyncio
from collections import defaultdict

import httpx

from .config import Settings
from .urls import host_of


class Fetcher:
    """Wraps an httpx.AsyncClient with global and per-host concurrency limits."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent},
            timeout=httpx.Timeout(settings.request_timeout),
            follow_redirects=True,
            max_redirects=5,
        )
        if client is not None and "user-agent" not in {k.lower() for k in client.headers}:
            client.headers["User-Agent"] = settings.user_agent
        self._global = asyncio.Semaphore(settings.global_concurrency)
        self._hosts: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(1))

    def host_slot(self, url: str) -> asyncio.Semaphore:
        return self._hosts[host_of(url)]

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        async with self._global, self.host_slot(url):
            return await self.client.get(url, headers=headers)

    def stream(self, url: str, headers: dict[str, str] | None = None):
        """Streaming GET; caller must hold ``limits(url)`` while reading."""
        return self.client.stream("GET", url, headers=headers)

    def limits(self, url: str):
        return _Both(self._global, self.host_slot(url))

    async def aclose(self) -> None:
        await self.client.aclose()


class _Both:
    def __init__(self, a: asyncio.Semaphore, b: asyncio.Semaphore) -> None:
        self.a, self.b = a, b

    async def __aenter__(self):
        await self.a.acquire()
        await self.b.acquire()

    async def __aexit__(self, *exc):
        self.b.release()
        self.a.release()
