"""The live story step (spec section 15, part 2): grouping new articles, the language
model step, ranking with the section 5 caps, the snapshot and the homepage API."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.app import app, get_session
from collector.models import Article, HomepageSnapshot, Publisher, Source, Story, StoryArticle
from collector.urls import url_hash
from stories import rank as ranking
from stories.config import LiveSettings, StorySettings
from stories.grouping import Item, extend
from stories.live import needs_enrichment, run_step
from stories.openrouter import OpenRouter
from test_stories import FakeOpenRouter

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)

PUBS = [
    # slug, country, language, owner group, state affiliated
    ("gulf", "AE", "ar", "Gulf Media", False),
    ("gulf-sister", "AE", "ar", "Gulf Media", False),
    ("nile", "EG", "ar", None, False),
    ("wire", "TR", "en", None, True),
]


def _setup(session):
    ids = {}
    for slug, country, language, owner, official in PUBS:
        pub = Publisher(slug=slug, name=slug.title(), name_ar=None, website=f"https://{slug}.example",
                        country=country, owner_group=owner, state_affiliated=official)
        session.add(pub)
        session.flush()
        src = Source(publisher_id=pub.id, slug=slug, feed_url=f"https://{slug}.example/feed", language=language)
        session.add(src)
        session.flush()
        ids[slug] = (pub.id, src.id, language)
    session.commit()
    return ids


def _article(session, ids, slug, title, minutes_ago, excerpt=None):
    pub_id, src_id, language = ids[slug]
    url = f"https://{slug}.example/{abs(hash(title))}"
    when = NOW - timedelta(minutes=minutes_ago)
    a = Article(source_id=src_id, publisher_id=pub_id, feed_url=url, feed_url_hash=url_hash(url), canonical_url=url,
                canonical_url_hash=url_hash(url), title=title, excerpt=excerpt, language=language,
                published_at=when, fetched_at=when + timedelta(minutes=1), fingerprint=0)
    session.add(a)
    session.flush()
    return a.id


def _client(fake):
    settings = StorySettings(api_key="test-key", base_url="https://openrouter.test/api/v1")
    return OpenRouter(settings, client=httpx.Client(transport=httpx.MockTransport(fake)))


LIVE = LiveSettings(enabled=True, embedding_model="test/embed", threshold=0.5, judge_low=0.0, enrich_per_run=10)


def test_extend_places_only_new_items_and_keeps_existing_stories():
    items = [Item(i, NOW + timedelta(minutes=i)) for i in range(4)]
    # Item 3 matches both members of the story (mean of its top two: 0.75); item 2 matches nothing.
    score = np.array([[1, .9, .1, .7], [.9, 1, .1, .8], [.1, .1, 1, .1], [.7, .8, .1, 1]], dtype=float)
    stories = [[0, 1]]
    placed = extend(items, score, stories, [2, 3], low=0.5, high=0.5)
    assert stories == [[0, 1, 3], [2]]
    assert [(i, s, how) for i, s, _, how in placed] == [(2, 1, "new"), (3, 0, "score")]


def test_extend_asks_the_judge_only_in_the_band():
    items = [Item(i, NOW + timedelta(minutes=i)) for i in range(3)]
    score = np.array([[1, .4, .9], [.4, 1, .8], [.9, .8, 1]], dtype=float)
    asked = []
    def judge(i, candidates):
        asked.append(i)
        return 0
    stories = [[0]]
    placed = extend(items, score, stories, [1, 2], low=0.3, high=0.6, judge=judge)
    assert asked == [1]  # item 2 scored 0.85 (mean of 0.9 and 0.8): joined without asking
    assert stories == [[0, 1, 2]]
    assert [how for *_, how in placed] == ["judge", "score"]


def test_enrichment_runs_at_two_reports_and_again_when_the_story_grows_by_half():
    assert not needs_enrichment(1, 0)
    assert needs_enrichment(2, 0)
    assert not needs_enrichment(2, 2) and needs_enrichment(3, 2)
    assert not needs_enrichment(5, 4) and needs_enrichment(6, 4)


def _rep(slug, owner=None, authority=0.5, official=False, minutes_ago=30):
    return ranking.Report(slug, owner, authority, official, NOW - timedelta(minutes=minutes_ago))


def test_owner_group_counts_once_and_syndicated_style_repeats_add_little():
    one_group = ranking.Candidate(1, "a", "AE", 3, NOW, [_rep("a", "G"), _rep("b", "G"), _rep("c", "G")])
    two_groups = ranking.Candidate(2, "a", "AE", 3, NOW, [_rep("a", "G"), _rep("d")])
    s1, s2 = (ranking.score(c, NOW, 12, 0.15) for c in (one_group, two_groups))
    assert s1["groups"] == 1 and s2["groups"] == 2
    assert s2["coverage"] > s1["coverage"]
    assert not s1["confirmed"] and s2["confirmed"]


def test_importance_and_recency_change_the_score():
    fresh = ranking.Candidate(1, "a", None, 5, NOW, [_rep("a"), _rep("b")])
    old = ranking.Candidate(2, "a", None, 5, NOW - timedelta(hours=12), [_rep("a", minutes_ago=800), _rep("b", minutes_ago=800)])
    minor = ranking.Candidate(3, "a", None, 2, NOW, [_rep("a"), _rep("b")])
    f, o, m = (ranking.score(c, NOW, 12, 0.15) for c in (fresh, old, minor))
    assert o["recency"] == pytest.approx(0.5, abs=0.01)
    assert f["score"] > m["score"] and f["score"] > o["score"]


def test_caps_single_source_publisher_and_country():
    def story(i, pub, country, groups=2):
        reports = [_rep(f"{pub}-x{k}" if k else pub) for k in range(groups)]
        return ranking.Candidate(i, pub, country, 3, NOW - timedelta(minutes=i), reports)

    # One unconfirmed story that would be first on score alone.
    lone = ranking.Candidate(0, "solo", "SA", 5, NOW, [_rep("solo", authority=0.79)])
    many = [story(i, "big", "AE") for i in range(1, 7)] + [story(i, f"p{i}", "EG") for i in range(7, 40)]
    ranked = ranking.rank([lone] + many, NOW, size=30)
    positions = {e["story_id"]: e["rank"] for e in ranked}
    assert positions[0] >= 11 and ranked[positions[0] - 1]["moved_down_from"] < positions[0]
    assert sum(1 for e in ranked if e["story_id"] in range(1, 7)) == 4  # publisher cap
    assert sum(1 for e in ranked[:20] if 7 <= e["story_id"] < 40) <= 8  # country cap


def test_official_source_confirms_a_single_source_story():
    c = ranking.Candidate(1, "wire", "TR", 3, NOW, [_rep("wire", official=True)])
    assert ranking.score(c, NOW, 12, 0.15)["confirmed"]


@pytest.fixture
def seeded(sessions):
    with sessions() as s:
        ids = _setup(s)
        basra = [
            _article(s, ids, "gulf", "حريق في محطة كهرباء البصرة يقتل أربعة", 300, "حريق محطة الهارثة في البصرة"),
            _article(s, ids, "nile", "مقتل أربعة في حريق محطة كهرباء البصرة", 200, "حريق محطة الهارثة في البصرة"),
            _article(s, ids, "wire", "Fire at Basra power station kills four", 100, "Basra Hartha power station fire"),
        ]
        gold = [_article(s, ids, "gulf-sister", "سعر الذهب اليوم في مصر يتراجع", 50)]
        s.commit()
    return basra, gold


def test_step_groups_enriches_ranks_and_serves_the_homepage(sessions, seeded):
    basra, gold = seeded
    fake = FakeOpenRouter()
    report = run_step(sessions, LIVE, client=_client(fake), now=NOW)
    assert report.new_articles == 4 and report.embedded == 4
    with sessions() as s:
        links = dict(s.execute(select(StoryArticle.article_id, StoryArticle.story_id)).all())
        assert links[basra[0]] == links[basra[1]]
        assert links[gold[0]] != links[basra[0]]
        story = s.get(Story, links[basra[0]])
        assert story.headline_ar == "عنوان" and story.headline_ok and story.importance == 3
        assert story.enriched_count == story.article_count
        shot = s.scalars(select(HomepageSnapshot)).one()
        assert shot.stories[0]["story_id"] == story.id
        assert {"coverage", "E", "recency", "velocity", "confirmed"} <= set(shot.stories[0])
        assert shot.settings["embedding_model"] == "test/embed"
    # Embedding requests used the live model; nothing but headline and excerpt was sent.
    embeds = [r for r in fake.requests if r["path"].endswith("/embeddings")]
    assert embeds and all(r["body"]["model"] == "test/embed" for r in embeds)

    # A second run with nothing new adds no stories and reuses stored embeddings.
    again = run_step(sessions, LIVE, client=_client(FakeOpenRouter()), now=NOW + timedelta(minutes=20))
    assert again.new_articles == 0 and again.embedded == 0 and again.enriched == 0

    def override():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_session] = override
    try:
        api = TestClient(app)
        home = api.get("/api/home").json()
        top = home["stories"][0]
        assert top["headline"] == "عنوان" and top["headline_by"] == "ijaz"
        assert top["lead"]["publisher_slug"] in {"gulf", "nile", "wire"}
        assert {m["url"] for m in top["more"]}.isdisjoint({top["lead"]["url"]})
        page = api.get(f"/api/story/{top['id']}").json()
        times = [a["item"]["time"] for a in page["articles"]]
        assert times == sorted(times, reverse=True)
        assert api.get("/api/story/999999").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_failed_quality_check_falls_back_to_the_publisher_headline(sessions, seeded):
    class Failing(FakeOpenRouter):
        def __call__(self, request):
            body = json.loads(request.content)
            if "response_format" in body and body["response_format"]["json_schema"]["name"] == "check":
                content = {"ok": False, "issues": ["number not in reports"]}
                return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}], "usage": {"cost": 0.0}})
            return super().__call__(request)

    run_step(sessions, LIVE, client=_client(Failing()), now=NOW)
    def override():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_session] = override
    try:
        top = TestClient(app).get("/api/home").json()["stories"][0]
        assert top["headline_by"] == "publisher" and top["headline"] == top["lead"]["title"]
    finally:
        app.dependency_overrides.clear()


def test_merged_story_redirects(sessions, seeded):
    run_step(sessions, LIVE, client=_client(FakeOpenRouter()), now=NOW)
    with sessions() as s:
        a, b = s.scalars(select(Story).order_by(Story.id)).all()[:2]
        b.merged_into_id = a.id
        s.commit()
        a_id, b_id = a.id, b.id
    def override():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_session] = override
    try:
        assert TestClient(app).get(f"/api/story/{b_id}").json()["redirect_to"] == a_id
    finally:
        app.dependency_overrides.clear()
