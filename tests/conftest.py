from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from collector.config import Settings
from collector.db import init_db, make_engine, session_factory
from collector.models import Base

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeWeb:
    """A tiny fake internet for httpx.MockTransport. Records every request."""

    def __init__(self) -> None:
        self.routes: dict[str, callable] = {}
        self.requests: list[httpx.Request] = []

    def add(self, url: str, body: bytes | str = b"", status: int = 200, headers: dict | None = None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.routes[url] = lambda request: httpx.Response(status, content=body, headers=headers or {})

    def add_handler(self, url: str, handler):
        self.routes[url] = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url in self.routes:
            return self.routes[url](request)
        return httpx.Response(404, content=b"not found")

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url="sqlite://", user_agent="TestCollector/0.1 (+https://example.invalid)")


@pytest.fixture
def sessions(tmp_path):
    """SQLite by default; set COLLECTOR_TEST_DATABASE_URL to run against PostgreSQL."""
    url = os.environ.get("COLLECTOR_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    init_db(engine)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()
