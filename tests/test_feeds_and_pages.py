from datetime import datetime, timezone

from collector.feeds import excerpt_from, parse_feed
from collector.pagecheck import parse_head

from conftest import fixture


def test_parse_arabic_rss():
    result = parse_feed(fixture("agency_ar.xml"), base_url="https://agency.example/rss")
    assert result.outcome == "ok"
    assert result.language == "ar"
    assert len(result.items) == 2  # the item without a title is skipped
    first = result.items[0]
    assert first.title.startswith("السعودية تعلن")
    assert first.author == "أحمد محمد"
    assert first.published == datetime(2026, 11, 17, 5, 0, tzinfo=timezone.utc)
    assert first.image == "https://agency.example/img/123.jpg"
    # The excerpt comes from the description, never from content:encoded.
    excerpt = excerpt_from(first, 300)
    assert "FULL ARTICLE TEXT" not in excerpt
    assert excerpt.startswith("أعلنت المملكة")


def test_parse_atom():
    result = parse_feed(fixture("english_atom.xml"))
    assert result.outcome == "ok"
    item = result.items[0]
    assert item.link.startswith("https://gulf.example/business/saudi-energy-package")
    assert item.author == "Jane Doe"
    assert excerpt_from(item, 300) == "Riyadh announced a package aimed at energy and infrastructure projects."


def test_not_a_feed():
    assert parse_feed(b"<html><body>hello</body></html>").outcome == "parse_error"


def test_head_parsing_prefers_canonical_and_meta():
    header = parse_head(fixture("article_head.html"), "https://agency.example/news/123")
    assert header.canonical == "https://agency.example/news/123-saudi-energy"
    assert header.description == "وصف الصفحة من رأس المقال."
    assert header.image == "/img/og-123.jpg"
    assert header.published == datetime(2026, 11, 17, 5, 1, tzinfo=timezone.utc)


def test_head_parsing_uses_jsonld_and_ignores_zoneless_dates():
    html = b"""<html><head>
    <meta name="date" content="2026-11-17 10:00">
    <script type="application/ld+json">{"@graph":[{"@type":"WebPage"},{"@type":"NewsArticle","datePublished":"2026-11-17T06:00:00+03:00","url":"https://x.example/a"}]}</script>
    </head><body>text</body></html>"""
    header = parse_head(html)
    assert header.published == datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
    assert header.canonical == "https://x.example/a"
