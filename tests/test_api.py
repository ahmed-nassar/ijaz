from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.app import app, get_session
from api.river import decode_cursor, encode_cursor, river_page
from api.sample_data import seed
from collector.models import Article, Publisher, Source
from collector.urls import url_hash

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(sessions, monkeypatch):
    monkeypatch.setattr("api.app._filters_cache", None)
    def override():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _all_items(client, **params):
    items, cursor = [], None
    while True:
        query = dict(params, limit=7)
        if cursor:
            query["cursor"] = cursor
        body = client.get("/api/river", params=query).json()
        items += body["items"]
        cursor = body["next_cursor"]
        if not cursor:
            return items


def test_cursor_round_trip():
    when = datetime(2026, 11, 21, 9, 30, 15, 123456, tzinfo=timezone.utc)
    assert decode_cursor(encode_cursor(when, 42)) == (when, 42)


def test_river_pages_cover_every_original_once_newest_first(sessions, client):
    with sessions() as s:
        seed(s, 60, now=NOW)
        originals = s.query(Article).filter(Article.syndicated_of_id.is_(None)).count()
        copies = s.query(Article).filter(Article.syndicated_of_id.is_not(None)).count()
    assert copies > 0
    items = _all_items(client)
    assert len(items) == originals
    assert len({i["id"] for i in items}) == originals
    keys = [(i["time"], i["id"]) for i in items]
    assert keys == sorted(keys, reverse=True)
    assert sum(len(i["copies"]) for i in items) == copies


def test_river_time_ignores_future_publish_time_and_falls_back_to_fetch(sessions):
    with sessions() as s:
        pub = Publisher(slug="p", name="P", website="https://p.example", country="SA")
        s.add(pub)
        s.flush()
        src = Source(publisher_id=pub.id, slug="p", feed_url="https://p.example/feed", language="ar")
        s.add(src)
        s.flush()

        def add(n, published, fetched):
            url = f"https://p.example/{n}"
            s.add(Article(source_id=src.id, publisher_id=pub.id, feed_url=url, feed_url_hash=url_hash(url),
                          canonical_url=url, canonical_url_hash=url_hash(url), title=f"خبر {n}", language="ar",
                          published_at=published, fetched_at=fetched, fingerprint=n))

        add(1, NOW - timedelta(hours=2), NOW - timedelta(hours=1))   # river time: published
        add(2, NOW + timedelta(hours=5), NOW - timedelta(minutes=30))  # future: fetch time
        add(3, None, NOW - timedelta(minutes=90))                    # missing: fetch time
        s.commit()
        page = river_page(s)
    assert [(i.title, i.time) for i in page.items] == [
        ("خبر 2", NOW - timedelta(minutes=30)),
        ("خبر 3", NOW - timedelta(minutes=90)),
        ("خبر 1", NOW - timedelta(hours=2)),
    ]


def test_filters(sessions, client):
    with sessions() as s:
        seed(s, 60, now=NOW)
    every = _all_items(client)
    turkish = _all_items(client, lang="tr")
    assert turkish and all(i["language"] == "tr" for i in turkish)
    egypt = _all_items(client, country="eg")
    assert egypt and all(i["publisher_slug"] == "sample-nile-post" for i in egypt)
    one = _all_items(client, publisher="sample-gulf-daily")
    assert one and {i["publisher_slug"] for i in one} == {"sample-gulf-daily"}
    assert len(turkish) < len(every)

    options = client.get("/api/river/filters").json()
    assert set(options["languages"]) == {"ar", "en", "tr"}
    assert "TR" in options["countries"]
    assert any(p["slug"] == "sample-bosphorus" for p in options["publishers"])


def test_bad_input_is_rejected(client):
    assert client.get("/api/river", params={"cursor": "not-a-cursor"}).status_code == 400
    assert client.get("/api/river", params={"limit": 500}).status_code == 422
    assert client.get("/api/river", params={"lang": "<script>"}).status_code == 422


def test_empty_river(client):
    body = client.get("/api/river").json()
    assert body == {"items": [], "next_cursor": None}


def test_export_articles_writes_json_lines(sessions, capsys):
    import json as _json

    from collector.cli import _export_articles

    with sessions() as s:
        seed(s, 20, now=datetime.now(timezone.utc))
    assert _export_articles(sessions, 14) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 20
    row = _json.loads(lines[0])
    assert {"id", "title", "excerpt", "url", "language", "publisher", "fetched_at"} <= row.keys()
