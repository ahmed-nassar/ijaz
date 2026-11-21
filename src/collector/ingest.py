"""Poll one source end to end: robots check, feed fetch, page check, clean-up, store."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .feeds import FeedItem, excerpt_from, fetch_feed
from .http import Fetcher
from .models import Alert, Article, FetchLog, Source
from .pagecheck import PageHeader, check_page
from .robots import RobotsChecker
from .text import cap_excerpt, detect_language, hamming, simhash
from .urls import normalize_url, same_site, url_hash

log = logging.getLogger("collector")


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PollReport:
    source_id: int
    outcome: str
    items_seen: int = 0
    items_new: int = 0


class Collector:
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        fetcher: Fetcher,
        robots: RobotsChecker | None = None,
        page_check: bool = True,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.fetcher = fetcher
        self.robots = robots or RobotsChecker(fetcher, sessions, settings)
        self.page_check = page_check

    async def poll(self, source_id: int) -> PollReport:
        started = now()
        clock = time.monotonic()
        with self.sessions() as session:
            source = session.get(Source, source_id)
            assert source is not None
            feed_url, etag, last_modified = source.feed_url, source.etag, source.last_modified

        verdict = await self.robots.check(feed_url)
        if verdict != "allowed":
            report = PollReport(source_id, f"robots_{verdict}")
            reason = "robots.txt disallows the feed URL" if verdict == "disallowed" else "robots.txt could not be read"
            self._finish(source_id, started, clock, report, None, error=reason)
            return report

        result = await fetch_feed(self.fetcher, feed_url, etag, last_modified)
        report = PollReport(source_id, result.outcome, items_seen=len(result.items))

        if result.outcome == "ok":
            for item in result.items:
                try:
                    if await self._store_item(source_id, item, result.language):
                        report.items_new += 1
                except Exception:  # one bad item must not stop the rest
                    log.exception("failed to store item %s from source %s", item.link, source_id)

        self._finish(source_id, started, clock, report, result, error=result.error)
        return report

    async def _store_item(self, source_id: int, item: FeedItem, feed_language: str | None) -> bool:
        with self.sessions() as session:
            source = session.get(Source, source_id)
            assert source is not None
            feed_link = normalize_url(item.link, base=source.feed_url)
            feed_hash = url_hash(feed_link)
            # Already seen this exact feed link: no page check, no reprocessing.
            if session.scalar(select(Article.id).where(Article.feed_url_hash == feed_hash).limit(1)):
                return False
            publisher_id, language_hint = source.publisher_id, source.language

        header = await self._page_header(feed_link)
        canonical = feed_link
        if header and header.status == "ok" and header.canonical:
            candidate = normalize_url(header.canonical, base=header.final_url or feed_link)
            # Trust a canonical link only on the publisher's own site.
            if same_site(candidate, feed_link):
                canonical = candidate
        canonical_hash = url_hash(canonical)

        excerpt = excerpt_from(item, self.settings.excerpt_max_chars)
        excerpt_origin = "feed" if excerpt else None
        if not excerpt and header and header.description:
            excerpt = cap_excerpt(header.description, self.settings.excerpt_max_chars)
            excerpt_origin = "page" if excerpt else None

        published, published_origin = item.published, "feed" if item.published else None
        if header and header.published:
            published, published_origin = header.published, "page"

        image = item.image or (header.image if header else None)
        if image:
            image = normalize_url(image, base=canonical)

        language = detect_language(f"{item.title} {excerpt or ''}", language_hint or feed_language)
        fingerprint = simhash(f"{item.title} {excerpt or ''}")

        with self.sessions() as session:
            if session.scalar(select(Article.id).where(Article.canonical_url_hash == canonical_hash).limit(1)):
                return False
            syndicated_of = self._find_original(session, fingerprint, publisher_id)
            session.add(
                Article(
                    source_id=source_id,
                    publisher_id=publisher_id,
                    feed_url=feed_link,
                    feed_url_hash=feed_hash,
                    canonical_url=canonical,
                    canonical_url_hash=canonical_hash,
                    guid=(item.guid or None) and item.guid[:1000],
                    title=item.title,
                    author=item.author,
                    excerpt=excerpt,
                    excerpt_origin=excerpt_origin,
                    image_url=image,
                    language=language,
                    published_at=published,
                    published_at_origin=published_origin,
                    fetched_at=now(),
                    page_check=self._page_check_status(header),
                    fingerprint=fingerprint,
                    syndicated_of_id=syndicated_of,
                )
            )
            session.commit()
        return True

    async def _page_header(self, url: str) -> PageHeader | None:
        if not self.page_check:
            return None
        if not await self.robots.allowed(url):
            return PageHeader("disallowed")
        return await check_page(self.fetcher, url, self.settings.page_check_max_bytes)

    @staticmethod
    def _page_check_status(header: PageHeader | None) -> str:
        if header is None:
            return "skipped"
        return header.status if header.status in ("ok", "failed", "disallowed") else "failed"

    def _find_original(self, session: Session, fingerprint: int, publisher_id: int) -> int | None:
        """Mark near-identical text from another publisher in the last 48 hours as syndicated."""
        if fingerprint == 0:
            return None
        since = now() - timedelta(hours=self.settings.syndication_window_hours)
        rows = session.execute(
            select(Article.id, Article.fingerprint, Article.syndicated_of_id)
            .where(Article.fetched_at >= since, Article.publisher_id != publisher_id)
            .order_by(Article.fetched_at.asc())
        )
        for article_id, other, original in rows:
            if hamming(fingerprint, other) <= self.settings.syndication_max_distance:
                return original or article_id
        return None

    def _finish(self, source_id, started, clock, report: PollReport, result, error: str | None) -> None:
        with self.sessions() as session:
            source = session.get(Source, source_id)
            assert source is not None
            success = report.outcome in ("ok", "not_modified")
            if success:
                source.consecutive_failures = 0
                source.last_success_at = started
                if result is not None and report.outcome == "ok":
                    source.etag = result.etag
                    source.last_modified = result.last_modified
                if report.items_new:
                    source.last_new_item_at = started
            else:
                source.consecutive_failures += 1
            source.poll_interval = self._next_interval(session, source, success)
            source.next_poll_at = started + timedelta(seconds=source.poll_interval)
            if source.consecutive_failures >= self.settings.pause_after_failures and not source.paused:
                source.paused = True
                session.add(
                    Alert(
                        created_at=now(),
                        kind="source_paused",
                        source_id=source_id,
                        message=f"{source.slug} paused after {source.consecutive_failures} failed polls: {error}",
                    )
                )
                log.error("source %s paused after %s failures", source.slug, source.consecutive_failures)
            session.add(
                FetchLog(
                    source_id=source_id,
                    started_at=started,
                    duration_ms=int((time.monotonic() - clock) * 1000),
                    outcome=report.outcome,
                    http_status=getattr(result, "http_status", None),
                    items_seen=report.items_seen,
                    items_new=report.items_new,
                    error=error,
                )
            )
            session.commit()

    def _next_interval(self, session: Session, source: Source, success: bool) -> int:
        s = self.settings
        if not success:
            return min(s.min_interval * (2 ** min(source.consecutive_failures, 10)), s.max_backoff)
        # Adapt to how often the source publishes: about two polls per new article,
        # within 5 to 30 minutes.
        day_ago = now() - timedelta(days=1)
        new_last_day = session.scalar(
            select(func.coalesce(func.sum(FetchLog.items_new), 0)).where(
                FetchLog.source_id == source.id, FetchLog.started_at >= day_ago
            )
        ) or 0
        if new_last_day <= 0:
            return s.max_interval
        interval = int(24 * 3600 / new_last_day / 2)
        return max(s.min_interval, min(s.max_interval, interval))
