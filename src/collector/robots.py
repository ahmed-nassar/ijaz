"""robots.txt checks before every fetch (layer 1 and 2 rule).

Follows RFC 9309: a 4xx answer means no rules (allow), a 5xx or network failure
means assume everything is disallowed until a later retry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .http import Fetcher
from .models import RobotsCache
from .urls import origin_of

ERROR_TTL = timedelta(hours=1)
MAX_ROBOTS_BYTES = 500 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class RobotsChecker:
    def __init__(self, fetcher: Fetcher, sessions: sessionmaker[Session], settings: Settings) -> None:
        self.fetcher = fetcher
        self.sessions = sessions
        self.settings = settings
        self._parsers: dict[str, tuple[datetime, str, RobotFileParser | None]] = {}

    async def allowed(self, url: str) -> bool:
        return await self.check(url) == "allowed"

    async def check(self, url: str) -> str:
        """Return "allowed", "disallowed", or "unreachable" (robots.txt could not be read,
        so the site is treated as disallowed for now, per RFC 9309)."""
        origin = origin_of(url)
        mode, parser = await self._rules(origin)
        if mode == "allow_all":
            return "allowed"
        if mode == "disallow_all":
            return "unreachable"
        assert parser is not None
        return "allowed" if parser.can_fetch(self.settings.user_agent, url) else "disallowed"

    async def _rules(self, origin: str) -> tuple[str, RobotFileParser | None]:
        cached = self._parsers.get(origin)
        if cached and cached[0] > _now():
            return cached[1], cached[2]

        with self.sessions() as session:
            row = session.get(RobotsCache, origin)
            if row is not None:
                ttl = ERROR_TTL if row.mode == "disallow_all" else timedelta(seconds=self.settings.robots_ttl)
                if _aware(row.fetched_at) + ttl > _now():
                    return self._remember(origin, row.mode, row.body, _aware(row.fetched_at) + ttl)

        mode, body = await self._download(origin)
        with self.sessions() as session:
            session.merge(RobotsCache(origin=origin, fetched_at=_now(), mode=mode, body=body))
            session.commit()
        ttl = ERROR_TTL if mode == "disallow_all" else timedelta(seconds=self.settings.robots_ttl)
        return self._remember(origin, mode, body, _now() + ttl)

    def _remember(self, origin: str, mode: str, body: str | None, expires: datetime):
        parser = None
        if mode == "rules":
            parser = RobotFileParser()
            parser.parse((body or "").splitlines())
        self._parsers[origin] = (expires, mode, parser)
        return mode, parser

    async def _download(self, origin: str) -> tuple[str, str | None]:
        try:
            response = await self.fetcher.get(f"{origin}/robots.txt")
        except httpx.HTTPError:
            return "disallow_all", None
        if response.status_code >= 500 or response.status_code == 429:
            return "disallow_all", None
        if response.status_code >= 400:
            return "allow_all", None
        body = response.content[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace")
        return "rules", body
