import asyncio

import httpx
import pytest
from sqlalchemy import select

from collector.http import Fetcher
from collector.models import Source
from collector.registry import RegistryError, load_source_list
from collector.robots import RobotsChecker
from collector.vet import draft_source_list, vet_publisher

SOURCE_LIST = """
publishers:
  - slug: agency
    name: Example Agency
    name_ar: الوكالة
    website: https://agency.example/
    country: SA
    state_affiliated: true
    feeds:
      - slug: agency-ar
        url: https://agency.example/rss
        language: ar
        countries: [SA]
        categories: [politics]
        enabled: true
      - slug: agency-en
        url: https://agency.example/en/rss
        language: en
        enabled: false
"""


def test_load_source_list_only_enables_marked_feeds(tmp_path, sessions):
    path = tmp_path / "sources.yaml"
    path.write_text(SOURCE_LIST, encoding="utf-8")
    with sessions() as s:
        assert load_source_list(s, path) == (1, 1)
        assert load_source_list(s, path) == (1, 1)  # loading twice updates, never duplicates
        feeds = {f.slug: f.active for f in s.scalars(select(Source))}
    assert feeds == {"agency-ar": True, "agency-en": False}


def test_source_list_validation(tmp_path, sessions):
    path = tmp_path / "bad.yaml"
    path.write_text("publishers:\n  - slug: x\n    name: X\n", encoding="utf-8")
    with sessions() as s, pytest.raises(RegistryError):
        load_source_list(s, path)


def test_vet_discovers_feed_from_homepage_and_drafts_disabled(settings, sessions, web):
    web.add("https://site.example/robots.txt", b"", status=404)
    web.add(
        "https://site.example/",
        b'<html><head><link rel="alternate" type="application/rss+xml" href="/feeds/all.xml"></head><body>',
        headers={"content-type": "text/html"},
    )
    feed = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><language>ar</language>
    <item><title>خبر</title><link>https://site.example/n/1</link><description>وصف</description>
    <pubDate>%s</pubDate></item></channel></rss>"""
    from email.utils import format_datetime
    from datetime import datetime, timezone

    web.add("https://site.example/feeds/all.xml", (feed % format_datetime(datetime.now(timezone.utc))).encode("utf-8"))
    fetcher = Fetcher(settings, httpx.AsyncClient(transport=httpx.MockTransport(web)))
    pub = {"slug": "site", "name": "Site", "website": "https://site.example/", "language": "ar"}
    report = asyncio.run(vet_publisher(fetcher, RobotsChecker(fetcher, sessions, settings), pub))
    assert report.discovered == ["https://site.example/feeds/all.xml"]
    assert report.feeds[0].status == "ok" and report.feeds[0].language == "ar"
    draft = draft_source_list([pub], [report])
    feed_entry = draft["publishers"][0]["feeds"][0]
    assert feed_entry["enabled"] is False and feed_entry["url"].endswith("/feeds/all.xml")


def test_database_url_from_host_env(monkeypatch):
    from collector.config import get_settings

    monkeypatch.delenv("COLLECTOR_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.railway.internal:5432/railway")
    assert get_settings().database_url == "postgresql+psycopg://u:p@db.railway.internal:5432/railway"
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    assert get_settings().database_url == "postgresql+psycopg://u:p@h/db"
    monkeypatch.setenv("COLLECTOR_DATABASE_URL", "sqlite:///x.db")
    assert get_settings().database_url == "sqlite:///x.db"
