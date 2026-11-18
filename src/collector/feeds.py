"""Layer 1: download a feed with conditional requests and turn it into clean item records."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser
import httpx

from .http import Fetcher
from .text import cap_excerpt, clean_whitespace, strip_html

MAX_FEED_BYTES = 5 * 1024 * 1024


@dataclass
class FeedItem:
    link: str
    title: str
    guid: str | None = None
    author: str | None = None
    summary: str | None = None
    published: datetime | None = None
    image: str | None = None


@dataclass
class FeedResult:
    outcome: str  # ok | not_modified | http_error | network_error | parse_error
    http_status: int | None = None
    items: list[FeedItem] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    language: str | None = None
    error: str | None = None


async def fetch_feed(
    fetcher: Fetcher, url: str, etag: str | None = None, last_modified: str | None = None
) -> FeedResult:
    headers = {"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        response = await fetcher.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return FeedResult("network_error", error=f"{type(exc).__name__}: {exc}"[:500])

    if response.status_code == 304:
        return FeedResult("not_modified", 304, etag=etag, last_modified=last_modified)
    if response.status_code >= 400:
        return FeedResult("http_error", response.status_code, error=f"HTTP {response.status_code}")

    result = parse_feed(response.content[:MAX_FEED_BYTES], base_url=str(response.url))
    result.http_status = response.status_code
    result.etag = response.headers.get("ETag")
    result.last_modified = response.headers.get("Last-Modified")
    return result


def parse_feed(content: bytes, base_url: str | None = None) -> FeedResult:
    parsed = feedparser.parse(content, response_headers={"content-location": base_url} if base_url else None)
    if parsed.bozo and not parsed.entries:
        return FeedResult("parse_error", error=str(parsed.get("bozo_exception", "not a feed"))[:500])
    if not parsed.get("version") and not parsed.entries:
        return FeedResult("parse_error", error="not an RSS or Atom document")

    items: list[FeedItem] = []
    for entry in parsed.entries:
        link = _entry_link(entry)
        title = clean_whitespace(strip_html(entry.get("title", "")))
        if not link or not title:
            continue
        items.append(
            FeedItem(
                link=link,
                title=title,
                guid=entry.get("id") or None,
                author=_author(entry),
                summary=entry.get("summary") or _first_content(entry),
                published=_date(entry),
                image=_image(entry),
            )
        )
    language = parsed.feed.get("language") if parsed.get("feed") else None
    return FeedResult("ok", items=items, language=language)


def _entry_link(entry) -> str | None:
    link = entry.get("link")
    if link:
        return link
    for candidate in entry.get("links", []):
        if candidate.get("rel", "alternate") == "alternate" and candidate.get("href"):
            return candidate["href"]
    guid = entry.get("id", "")
    return guid if guid.startswith("http") else None


def _author(entry) -> str | None:
    name = entry.get("author") or (entry.get("author_detail") or {}).get("name")
    if not name and entry.get("authors"):
        name = ", ".join(a.get("name", "") for a in entry["authors"] if a.get("name"))
    name = clean_whitespace(strip_html(name or ""))
    return name[:300] or None


def _first_content(entry) -> str | None:
    # Some feeds put only full text in content:encoded. We take it only to build a
    # capped excerpt; the full text is never stored (cap_excerpt runs before saving).
    content = entry.get("content") or []
    return content[0].get("value") if content else None


def _date(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    return None


def _image(entry) -> str | None:
    for media in entry.get("media_thumbnail", []) + entry.get("media_content", []):
        url = media.get("url")
        if url and (media.get("medium", "image") == "image" or "type" not in media or media.get("type", "").startswith("image")):
            return url
    for enclosure in entry.get("enclosures", []):
        if enclosure.get("type", "").startswith("image") and enclosure.get("href"):
            return enclosure["href"]
    return None


def excerpt_from(item: FeedItem, max_chars: int) -> str | None:
    return cap_excerpt(item.summary, max_chars)
