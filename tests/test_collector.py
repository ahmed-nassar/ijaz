import asyncio

import httpx
from sqlalchemy import select

from collector.http import Fetcher
from collector.ingest import Collector
from collector.models import Alert, Article, FetchLog, Publisher, Source

from conftest import fixture

ARTICLE_HEAD = fixture("article_head.html") + b"<p>" + b"SECRET BODY TEXT " * 50000 + b"</p></body></html>"


def run(coro):
    return asyncio.run(coro)


def make_collector(settings, sessions, web):
    client = httpx.AsyncClient(transport=httpx.MockTransport(web))
    return Collector(settings, sessions, Fetcher(settings, client))


def add_source(sessions, slug, feed_url, publisher, language="ar"):
    with sessions() as s:
        pub = s.scalar(select(Publisher).where(Publisher.slug == publisher))
        if pub is None:
            pub = Publisher(slug=publisher, name=publisher, website=f"https://{publisher}.example/")
            s.add(pub)
            s.flush()
        src = Source(slug=slug, feed_url=feed_url, language=language, publisher_id=pub.id)
        s.add(src)
        s.commit()
        return src.id


def agency_web(web):
    web.add("https://agency.example/robots.txt", fixture("robots_agency.txt"))
    web.add("https://agency.example/rss", fixture("agency_ar.xml"), headers={"ETag": '"v1"'})
    web.add("https://agency.example/news/123", ARTICLE_HEAD, headers={"content-type": "text/html; charset=utf-8"})
    web.add("https://agency.example/news/124", b"<html><head></head><body>x</body></html>", headers={"content-type": "text/html"})


def test_scenario_feed_to_clean_articles(settings, sessions, web):
    agency_web(web)
    source_id = add_source(sessions, "agency-ar", "https://agency.example/rss", "agency")
    collector = make_collector(settings, sessions, web)

    report = run(collector.poll(source_id))
    assert report.outcome == "ok"
    assert (report.items_seen, report.items_new) == (2, 2)

    with sessions() as s:
        a = s.scalar(select(Article).where(Article.guid == "agency-123"))
        # Canonical link from the page header, tracking parameters gone.
        assert a.canonical_url == "https://agency.example/news/123-saudi-energy"
        assert a.feed_url == "https://agency.example/news/123"
        assert a.excerpt_origin == "feed" and a.excerpt.startswith("أعلنت المملكة")
        assert a.published_at_origin == "page"
        assert a.image_url == "https://agency.example/img/123.jpg"
        assert a.language == "ar" and a.page_check == "ok"
        # No body text anywhere in the record.
        for value in (a.title, a.excerpt, a.author or ""):
            assert "SECRET" not in value and "FULL ARTICLE" not in value


def test_scenario_second_poll_is_conditional_and_stores_nothing(settings, sessions, web):
    agency_web(web)
    source_id = add_source(sessions, "agency-ar", "https://agency.example/rss", "agency")
    collector = make_collector(settings, sessions, web)
    run(collector.poll(source_id))

    def conditional(request):
        assert request.headers.get("If-None-Match") == '"v1"'
        return httpx.Response(304)

    web.add_handler("https://agency.example/rss", conditional)
    report = run(collector.poll(source_id))
    assert report.outcome == "not_modified"
    with sessions() as s:
        assert s.scalar(select(Article).where(Article.id > 2)) is None


def test_scenario_feed_unchanged_but_resent_is_not_reprocessed(settings, sessions, web):
    agency_web(web)
    source_id = add_source(sessions, "agency-ar", "https://agency.example/rss", "agency")
    collector = make_collector(settings, sessions, web)
    run(collector.poll(source_id))
    page_loads = web.urls().count("https://agency.example/news/123")
    report = run(collector.poll(source_id))  # server ignores ETag and resends the feed
    assert report.items_new == 0
    assert web.urls().count("https://agency.example/news/123") == page_loads  # no second page check


def test_scenario_syndicated_copy_and_robots_disallowed_page(settings, sessions, web):
    agency_web(web)
    web.add("https://paper.example/robots.txt", fixture("robots_paper.txt"))
    web.add("https://paper.example/rss", fixture("paper_ar.xml"))
    web.add("https://paper.example/economy/987", b"<html><head></head></html>", headers={"content-type": "text/html"})
    agency = add_source(sessions, "agency-ar", "https://agency.example/rss", "agency")
    paper = add_source(sessions, "paper-ar", "https://paper.example/rss", "paper")
    collector = make_collector(settings, sessions, web)
    run(collector.poll(agency))
    run(collector.poll(paper))

    with sessions() as s:
        original = s.scalar(select(Article).where(Article.guid == "agency-123"))
        copy = s.scalar(select(Article).where(Article.canonical_url == "https://paper.example/economy/987"))
        assert copy.syndicated_of_id == original.id
        aramco = s.scalar(select(Article).where(Article.canonical_url == "https://paper.example/companies/988"))
        assert aramco.page_check == "disallowed"
        assert aramco.syndicated_of_id is None
    # The disallowed page was never requested.
    assert "https://paper.example/companies/988" not in web.urls()


def test_scenario_feed_disallowed_by_robots_is_never_fetched(settings, sessions, web):
    web.add("https://blocked.example/robots.txt", "User-agent: *\nDisallow: /")
    web.add("https://blocked.example/rss", fixture("agency_ar.xml"))
    source_id = add_source(sessions, "blocked", "https://blocked.example/rss", "blocked")
    report = run(make_collector(settings, sessions, web).poll(source_id))
    assert report.outcome == "robots_disallowed"
    assert "https://blocked.example/rss" not in web.urls()


def test_scenario_robots_server_error_means_disallow(settings, sessions, web):
    web.add("https://flaky.example/robots.txt", b"", status=503)
    web.add("https://flaky.example/rss", fixture("agency_ar.xml"))
    source_id = add_source(sessions, "flaky", "https://flaky.example/rss", "flaky")
    report = run(make_collector(settings, sessions, web).poll(source_id))
    assert report.outcome == "robots_unreachable"
    assert "https://flaky.example/rss" not in web.urls()


def test_scenario_same_canonical_from_two_links_is_one_article(settings, sessions, web):
    web.add("https://agency.example/robots.txt", b"", status=404)
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>One story</title><link>https://agency.example/a?utm_campaign=x</link></item>
    <item><title>One story again</title><link>https://agency.example/a-mobile</link></item>
    </channel></rss>"""
    web.add("https://agency.example/rss", feed)
    head = b'<html><head><link rel="canonical" href="https://agency.example/a"></head><body>'
    web.add("https://agency.example/a", head, headers={"content-type": "text/html"})
    web.add("https://agency.example/a-mobile", head, headers={"content-type": "text/html"})
    source_id = add_source(sessions, "agency-ar", "https://agency.example/rss", "agency", language="en")
    report = run(make_collector(settings, sessions, web).poll(source_id))
    assert report.items_new == 1


def test_scenario_foreign_canonical_is_ignored(settings, sessions, web):
    web.add("https://agency.example/robots.txt", b"", status=404)
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Story</title><link>https://agency.example/s</link></item></channel></rss>"""
    web.add("https://agency.example/rss", feed)
    web.add("https://agency.example/s", b'<html><head><link rel="canonical" href="https://evil.example/s"></head>', headers={"content-type": "text/html"})
    source_id = add_source(sessions, "agency", "https://agency.example/rss", "agency", language="en")
    run(make_collector(settings, sessions, web).poll(source_id))
    with sessions() as s:
        assert s.scalar(select(Article.canonical_url)) == "https://agency.example/s"


def test_scenario_failing_source_backs_off_then_pauses_with_alert(settings, sessions, web):
    web.add("https://down.example/robots.txt", b"", status=404)
    web.add("https://down.example/rss", b"", status=500)
    source_id = add_source(sessions, "down", "https://down.example/rss", "down")
    collector = make_collector(settings, sessions, web)
    intervals = []
    for _ in range(settings.pause_after_failures):
        run(collector.poll(source_id))
        with sessions() as s:
            intervals.append(s.get(Source, source_id).poll_interval)
    assert intervals[0] < intervals[3] <= settings.max_backoff
    with sessions() as s:
        src = s.get(Source, source_id)
        assert src.paused and src.consecutive_failures == settings.pause_after_failures
        assert s.scalar(select(Alert).where(Alert.kind == "source_paused")) is not None
        assert s.scalar(select(FetchLog).where(FetchLog.outcome == "http_error")) is not None


def test_page_check_stops_at_head(settings, sessions, web):
    from collector.pagecheck import check_page

    sent = {"bytes": 0}

    async def stream_body():
        chunk = fixture("article_head.html")
        sent["bytes"] += len(chunk)
        yield chunk
        for _ in range(1000):
            data = b"<p>" + b"SECRET BODY TEXT " * 100 + b"</p>"
            sent["bytes"] += len(data)
            yield data

    web.add_handler(
        "https://agency.example/long",
        lambda request: httpx.Response(200, content=stream_body(), headers={"content-type": "text/html"}),
    )
    fetcher = Fetcher(settings, httpx.AsyncClient(transport=httpx.MockTransport(web)))
    header = run(check_page(fetcher, "https://agency.example/long", settings.page_check_max_bytes))
    assert header.status == "ok" and header.canonical
    assert sent["bytes"] < 10_000  # stopped long before the 1.7 MB body


def test_rounds_poll_sources_due_before_next_round_then_release_connections(settings, sessions, web):
    from datetime import datetime, timedelta, timezone

    from collector.scheduler import run_rounds

    agency_web(web)
    soon = add_source(sessions, "agency-ar", "https://agency.example/rss", "agency")
    later = add_source(sessions, "agency-late", "https://agency.example/late", "agency")
    now = datetime.now(timezone.utc)
    with sessions() as s:
        s.get(Source, soon).next_poll_at = now + timedelta(minutes=10)  # before the next round
        s.get(Source, later).next_poll_at = now + timedelta(minutes=30)  # after it
        s.commit()
    released = []

    run(run_rounds(make_collector(settings, sessions, web), 20 * 60, lambda: released.append(1), rounds=1))

    assert "https://agency.example/rss" in web.urls()
    assert "https://agency.example/late" not in web.urls()
    assert released == [1]


def test_round_minutes_setting(monkeypatch):
    from collector.config import get_settings

    monkeypatch.delenv("COLLECTOR_ROUND_MINUTES", raising=False)
    assert get_settings().round_minutes == 0
    monkeypatch.setenv("COLLECTOR_ROUND_MINUTES", "20")
    assert get_settings().round_minutes == 20


def test_lease_one_worker_at_a_time_and_takeover_after_expiry(sessions):
    from datetime import datetime, timedelta, timezone

    from collector.lease import claim, holder

    t = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    assert claim(sessions, "rounds", "a", 2460, now=t)
    assert not claim(sessions, "rounds", "b", 2460, now=t + timedelta(minutes=1))
    assert claim(sessions, "rounds", "a", 2460, now=t + timedelta(minutes=20))  # renewal
    assert not claim(sessions, "rounds", "b", 2460, now=t + timedelta(minutes=40))
    # a stopped renewing: b takes over once the lease runs out
    assert claim(sessions, "rounds", "b", 2460, now=t + timedelta(minutes=62))
    assert holder(sessions, "rounds")[0] == "b"


def test_standby_worker_polls_nothing_and_skips_the_story_step(settings, sessions, web):
    from collector.scheduler import run_rounds

    agency_web(web)
    add_source(sessions, "agency-ar", "https://agency.example/rss", "agency")
    steps, released = [], []
    run(run_rounds(make_collector(settings, sessions, web), 20 * 60, lambda: released.append(1), rounds=1,
                   after_round=lambda: steps.append(1), claim=lambda: False))
    assert web.urls() == [] and steps == [] and released == [1]
    run(run_rounds(make_collector(settings, sessions, web), 20 * 60, lambda: released.append(1), rounds=1,
                   after_round=lambda: steps.append(1), claim=lambda: True))
    assert "https://agency.example/rss" in web.urls() and steps == [1]


def test_standby_stops_only_once_the_main_worker_renews_its_lease(settings, sessions, web):
    from datetime import datetime, timedelta, timezone

    from collector.lease import StandbyCheck, claim

    t = datetime.now(timezone.utc)
    check = StandbyCheck(sessions, "rounds", "b")
    assert not check()  # no lease yet
    assert claim(sessions, "rounds", "a", 2460, now=t)
    assert not check()  # first look at a's lease
    assert not check()  # a has not renewed: maybe dead, keep standing by
    assert claim(sessions, "rounds", "a", 2460, now=t + timedelta(minutes=20))
    assert check()  # a renewed: alive, the standby can stop


def test_standby_round_returns_when_it_can_stop(settings, sessions, web):
    from collector.scheduler import run_rounds

    released = []
    # rounds=None would loop forever unless the standby stops itself
    run(run_rounds(make_collector(settings, sessions, web), 0, lambda: released.append(1),
                   claim=lambda: False, standby_can_stop=lambda: True))
    assert web.urls() == [] and released == [1]


def test_watchdog_starts_a_stopped_worker_after_two_empty_checks():
    import httpx

    from collector.watchdog import check_once

    machines = [
        {"id": "w1", "state": "stopped", "config": {"metadata": {"fly_process_group": "worker"}}},
        {"id": "w2", "state": "stopped", "config": {"metadata": {"fly_process_group": "worker"}}},
        {"id": "web1", "state": "started", "config": {"metadata": {"fly_process_group": "web"}}},
    ]
    started = []

    def handler(request):
        if request.method == "POST":
            started.append(request.url.path)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json=machines)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert check_once(client, "ijaz", 0) == 1 and started == []  # first miss: wait
    assert check_once(client, "ijaz", 1) == 0
    assert started == ["/v1/apps/ijaz/machines/w1/start"]
    machines[0]["state"] = "started"
    assert check_once(client, "ijaz", 0) == 0 and len(started) == 1  # one is running: nothing to do


def test_relabel_corrects_stored_languages_and_is_idempotent(sessions):
    from datetime import datetime, timezone

    from collector.registry import relabel_languages
    from collector.urls import url_hash

    source_id = add_source(sessions, "hdn", "https://hdn.example/rss", "hdn", language="en")
    now = datetime.now(timezone.utc)
    with sessions() as s:
        pub_id = s.get(Source, source_id).publisher_id
        for i, (title, stored) in enumerate([("Erdoğan says Türkiye entering new era on world stage", "tr"),
                                              ("Fidan holds talks with US envoy", "en")]):
            url = f"https://hdn.example/{i}"
            s.add(Article(source_id=source_id, publisher_id=pub_id, feed_url=url, feed_url_hash=url_hash(url),
                          canonical_url=url, canonical_url_hash=url_hash(url), title=title, language=stored,
                          published_at=now, fetched_at=now, fingerprint=0))
        s.commit()
        assert relabel_languages(s) == 1
        assert relabel_languages(s) == 0
        assert set(s.scalars(select(Article.language))) == {"en"}
