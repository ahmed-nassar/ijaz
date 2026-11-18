"""Layer 2: header-only page check.

Loads an article page once and reads only the <head>: canonical link, publish time,
description and image. It stops at </head> (or a byte cap) and never reads body text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

from .http import Fetcher
from .text import clean_whitespace

_HEAD_END = re.compile(rb"</head\s*>|<body[\s>]", re.IGNORECASE)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.IGNORECASE)


@dataclass
class PageHeader:
    status: str  # ok | failed
    final_url: str | None = None
    canonical: str | None = None
    published: datetime | None = None
    description: str | None = None
    image: str | None = None
    error: str | None = None


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.jsonld: list[str] = []
        self._in_jsonld = False
        self._buf: list[str] = []
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "body":
            self.done = True
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical = self.canonical or a.get("href") or None
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"]
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "head":
            self.done = True
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append("".join(self._buf))

    def handle_data(self, data):
        if self._in_jsonld:
            self._buf.append(data)


async def check_page(fetcher: Fetcher, url: str, max_bytes: int) -> PageHeader:
    head = b""
    try:
        async with fetcher.limits(url):
            async with fetcher.stream(url, headers={"Accept": "text/html"}) as response:
                if response.status_code >= 400:
                    return PageHeader("failed", str(response.url), error=f"HTTP {response.status_code}")
                ctype = response.headers.get("content-type", "")
                if "html" not in ctype.lower():
                    return PageHeader("failed", str(response.url), error=f"not HTML: {ctype[:60]}")
                async for chunk in response.aiter_bytes():
                    head += chunk
                    match = _HEAD_END.search(head)
                    if match:
                        head = head[: match.end()]
                        break
                    if len(head) >= max_bytes:
                        head = head[:max_bytes]
                        break
                final_url = str(response.url)
                charset = response.charset_encoding
    except httpx.HTTPError as exc:
        return PageHeader("failed", error=f"{type(exc).__name__}: {exc}"[:300])

    return parse_head(head, final_url, charset)


def parse_head(head: bytes, final_url: str | None = None, charset: str | None = None) -> PageHeader:
    if not charset:
        found = _META_CHARSET.search(head)
        charset = found.group(1).decode("ascii", "ignore") if found else "utf-8"
    try:
        text = head.decode(charset, errors="replace")
    except LookupError:
        text = head.decode("utf-8", errors="replace")

    parser = _HeadParser()
    try:
        parser.feed(text)
    except Exception:  # malformed markup: keep whatever was read
        pass
    meta = parser.meta
    ld = _from_jsonld(parser.jsonld)

    canonical = parser.canonical or meta.get("og:url") or ld.get("url")
    published = (
        _parse_date(meta.get("article:published_time"))
        or _parse_date(ld.get("datePublished"))
        or _parse_date(meta.get("pubdate") or meta.get("publishdate") or meta.get("date"))
    )
    description = meta.get("og:description") or meta.get("description") or meta.get("twitter:description") or ld.get("description")
    image = meta.get("og:image") or meta.get("og:image:url") or meta.get("twitter:image") or _ld_image(ld)
    return PageHeader(
        "ok",
        final_url=final_url,
        canonical=canonical.strip() if canonical else None,
        published=published,
        description=clean_whitespace(description) if description else None,
        image=image.strip() if image else None,
    )


def _from_jsonld(blocks: list[str]) -> dict:
    for block in blocks:
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(k in ("NewsArticle", "Article", "ReportageNewsArticle", "AnalysisNewsArticle") for k in kinds):
                return item
    return {}


def _ld_image(ld: dict) -> str | None:
    image = ld.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    return image if isinstance(image, str) else None


def _parse_date(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Date without zone: unknown local time. Do not guess; caller falls back to feed date.
        return None
    return parsed.astimezone(timezone.utc)
