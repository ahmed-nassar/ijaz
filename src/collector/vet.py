"""Vet candidate sources: find each site's feeds and test them.

Produces a draft source list. Nothing is enabled automatically: a feed becomes
``enabled: true`` only after its publisher's terms are reviewed (decision D16).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx
import yaml

from .config import Settings
from .feeds import fetch_feed
from .http import Fetcher
from .robots import RobotsChecker
from .text import detect_language
from .urls import normalize_url

COMMON_PATHS = ("/rss", "/rss.xml", "/feed", "/feed/", "/feed.xml", "/atom.xml", "/rss/all.xml", "/index.xml")
FRESH_DAYS = 7


class _AlternateLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.feeds: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if "alternate" in a.get("rel", "").lower() and a.get("type", "").lower() in (
            "application/rss+xml",
            "application/atom+xml",
            "application/rdf+xml",
        ) and a.get("href"):
            self.feeds.append(a["href"])


@dataclass
class FeedCheck:
    url: str
    status: str  # ok | stale | empty | invalid | robots_disallowed | robots_unreachable | error
    items: int = 0
    newest_age_hours: float | None = None
    with_summary_pct: int = 0
    language: str | None = None
    error: str | None = None


@dataclass
class SiteReport:
    slug: str
    homepage_robots: str = "unknown"
    discovered: list[str] = field(default_factory=list)
    feeds: list[FeedCheck] = field(default_factory=list)


async def discover_feeds(fetcher: Fetcher, robots: RobotsChecker, website: str) -> tuple[str, list[str]]:
    verdict = await robots.check(website)
    if verdict != "allowed":
        return verdict, []
    found: list[str] = []
    try:
        async with fetcher.limits(website):
            async with fetcher.stream(website, headers={"Accept": "text/html"}) as response:
                head = b""
                async for chunk in response.aiter_bytes():
                    head += chunk
                    if b"</head" in head.lower() or len(head) > 512 * 1024:
                        break
                base = str(response.url)
        parser = _AlternateLinks()
        parser.feed(head.decode(response.charset_encoding or "utf-8", errors="replace"))
        found = [urljoin(base, href) for href in parser.feeds]
    except httpx.HTTPError:
        pass
    return "allowed", found


async def check_feed(fetcher: Fetcher, robots: RobotsChecker, url: str, declared_language: str | None) -> FeedCheck:
    verdict = await robots.check(url)
    if verdict != "allowed":
        return FeedCheck(url, f"robots_{verdict}")
    result = await fetch_feed(fetcher, url)
    if result.outcome != "ok":
        return FeedCheck(url, "invalid" if result.outcome == "parse_error" else "error", error=result.error)
    if not result.items:
        return FeedCheck(url, "empty")
    dates = [i.published for i in result.items if i.published]
    newest = max(dates) if dates else None
    age = (datetime.now(timezone.utc) - newest).total_seconds() / 3600 if newest else None
    with_summary = sum(1 for i in result.items if i.summary)
    sample = " ".join(i.title for i in result.items[:10])
    status = "ok" if age is not None and age <= FRESH_DAYS * 24 else "stale"
    return FeedCheck(
        url,
        status,
        items=len(result.items),
        newest_age_hours=round(age, 1) if age is not None else None,
        with_summary_pct=round(100 * with_summary / len(result.items)),
        language=detect_language(sample, result.language or declared_language),
    )


async def vet_publisher(fetcher: Fetcher, robots: RobotsChecker, pub: dict) -> SiteReport:
    report = SiteReport(pub["slug"])
    report.homepage_robots, discovered = await discover_feeds(fetcher, robots, pub["website"])
    hints = [f["url"] for f in pub.get("feeds") or []] + list(pub.get("feed_hints") or [])
    candidates = hints + discovered
    if not candidates:
        candidates = [urljoin(pub["website"], p) for p in COMMON_PATHS]
    seen: set[str] = set()
    for url in candidates:
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        report.feeds.append(await check_feed(fetcher, robots, url, pub.get("language")))
        if not hints and not discovered and report.feeds[-1].status == "ok":
            break  # one working common path is enough
    report.discovered = discovered
    return report


async def vet_all(settings: Settings, fetcher: Fetcher, robots: RobotsChecker, publishers: list[dict]) -> list[SiteReport]:
    return await asyncio.gather(*(vet_publisher(fetcher, robots, p) for p in publishers))


def draft_source_list(publishers: list[dict], reports: list[SiteReport]) -> dict:
    """Merge vetting results into source-list format. All feeds start disabled."""
    by_slug = {r.slug: r for r in reports}
    out = []
    for pub in publishers:
        report = by_slug.get(pub["slug"])
        entry = {k: v for k, v in pub.items() if k not in ("feeds", "feed_hints")}
        entry["terms_reviewed"] = pub.get("terms_reviewed", False)
        feeds = []
        for i, check in enumerate(report.feeds if report else []):
            if check.status not in ("ok", "stale"):
                continue
            feeds.append(
                {
                    "slug": f"{pub['slug']}-{i + 1}",
                    "url": check.url,
                    "language": check.language or pub.get("language", "ar"),
                    "countries": pub.get("countries", [pub.get("country")] if pub.get("country") else []),
                    "categories": pub.get("categories", []),
                    "enabled": False,
                    "vetting": {
                        "status": check.status,
                        "items": check.items,
                        "newest_age_hours": check.newest_age_hours,
                        "with_summary_pct": check.with_summary_pct,
                    },
                }
            )
        entry["feeds"] = feeds
        entry["vetting"] = {
            "homepage_robots": report.homepage_robots if report else "not_checked",
            "rejected": [
                {"url": c.url, "status": c.status, "error": c.error}
                for c in (report.feeds if report else [])
                if c.status not in ("ok", "stale")
            ],
        }
        out.append(entry)
    return {"publishers": out}


def write_yaml(data: dict, path: str | Path) -> None:
    Path(path).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
