"""Story grouping (spec section 15, part 1): similarity, grouping, the language model
step and the measurement, all without network access."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np

from api.sample_data import seed
from collector.models import Article
from stories.config import StorySettings
from stories.enrich import Report, enrich
from stories.evaluate import pairs, render, run, scores
from stories.grouping import Item, group
from stories.openrouter import OpenRouter
from stories.similarity import combine, embedding_similarity, word_similarity
from stories.text import article_words, embedding_text, words

T0 = datetime(2026, 11, 21, 8, tzinfo=timezone.utc)


def fake_vector(text: str, dims: int = 64) -> list[float]:
    """A deterministic stand-in for an embedding: hashed bag of words."""
    v = [0.0] * dims
    for w in words(text):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dims] += 1.0
    return v


class FakeOpenRouter:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"path": request.url.path, "body": body, "auth": request.headers.get("authorization")})
        if request.url.path.endswith("/embeddings"):
            data = [{"index": i, "embedding": fake_vector(t)} for i, t in enumerate(body["input"])]
            return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 10 * len(data), "cost": 0.00001 * len(data)}})
        schema = body["response_format"]["json_schema"]["name"]
        if schema == "story":
            content = {"headline_ar": "عنوان", "importance": 3, "importance_reason": "test", "event_date": None, "translations": []}
        else:
            content = {"ok": True, "issues": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 500, "completion_tokens": 50, "cost": 0.0001}})


def client(fake: FakeOpenRouter) -> OpenRouter:
    settings = StorySettings(api_key="test-key", base_url="https://openrouter.test/api/v1")
    return OpenRouter(settings, client=httpx.Client(transport=httpx.MockTransport(fake)))


def test_words_keep_numbers_and_drop_single_letters():
    assert words("Fire at Iraq's Basra power station kills 4") == ["fire", "at", "iraq", "basra", "power", "station", "kills", "4"]
    assert words("حريق في محطة كهرباء البصرة") == ["حريق", "في", "محطة", "كهرباء", "البصرة"]


def test_headline_counts_twice_and_only_headline_and_excerpt_are_sent():
    assert article_words("Basra fire", "Four dead") == ["basra", "fire", "basra", "fire", "four", "dead"]
    assert embedding_text("Basra fire", None) == "Basra fire"
    assert embedding_text("Basra fire", "Four dead") == "Basra fire\nFour dead"


def test_word_similarity_ignores_words_common_to_everything():
    docs = [article_words(t, None) for t in [
        "Fire at Basra power station kills four today",
        "Basra power station fire: four dead today",
        "Gold prices fall today",
        "Dollar steady today",
    ]]
    sim = word_similarity(docs)
    assert sim[0, 1] > 0.5
    assert sim[0, 2] < 0.1 and sim[2, 3] < 0.1
    assert np.allclose(np.diag(sim), 1.0)


def test_combined_score_weights_words_and_meaning():
    word = np.array([[1.0, 0.2], [0.2, 1.0]])
    meaning = np.array([[1.0, 0.8], [0.8, 1.0]])
    assert combine(word, meaning, 0.5)[0, 1] == 0.5
    assert embedding_similarity([[1, 0], [-1, 0]])[0, 1] == 0.0  # opposite vectors clip to 0


def test_grouping_joins_matches_and_respects_the_time_window():
    items = [Item(1, T0), Item(2, T0 + timedelta(hours=1)), Item(3, T0 + timedelta(hours=2)), Item(4, T0 + timedelta(hours=60))]
    score = np.array([
        [1, .9, .1, .9],
        [.9, 1, .1, .9],
        [.1, .1, 1, .1],
        [.9, .9, .1, 1],
    ])
    assert group(items, score, 0.5) == [[1, 2], [3], [4]]  # 4 is 59 hours after 2: too late
    assert group(items, score, 0.95) == [[1], [2], [3], [4]]


def test_one_loose_link_does_not_chain_stories_together():
    items = [Item(i, T0 + timedelta(minutes=i)) for i in range(4)]
    # 0, 1 and 2 are one story; 3 matches only 2, loosely.
    score = np.array([
        [1, .9, .9, .1],
        [.9, 1, .9, .1],
        [.9, .9, 1, .6],
        [.1, .1, .6, 1],
    ])
    assert group(items, score, 0.5) == [[0, 1, 2], [3]]


def test_pair_scores():
    gold = pairs([[1, 2, 3]])
    assert scores(pairs([[1, 2], [3]]), gold) == (1.0, 1 / 3, 0.5)
    assert scores(pairs([[1, 2, 3, 4]]), gold)[0] == 0.5


def test_enrich_writes_then_checks_and_tracks_cost():
    fake = FakeOpenRouter()
    c = client(fake)
    result = enrich(c, [Report(1, "aa-en", "en", "2026-11-21 08:00", "Basra fire kills four", "Four dead at Hartha.")])
    assert result["headline_ar"] == "عنوان" and result["quality"]["ok"] is True
    assert [r["body"]["response_format"]["json_schema"]["name"] for r in fake.requests] == ["story", "check"]
    assert fake.requests[0]["auth"] == "Bearer test-key"
    assert fake.requests[0]["body"]["model"] == "google/gemini-3.1-flash-lite"
    assert "Four dead at Hartha." in fake.requests[0]["body"]["messages"][1]["content"]
    assert c.llm_usage.calls == 2 and abs(c.llm_usage.cost_usd - 0.0002) < 1e-9


def test_evaluation_end_to_end(sessions, tmp_path):
    with sessions() as s:
        seed(s, 60, now=T0)
        articles = s.query(Article).all()
    by_title = defaultdict(list)
    for a in articles:
        by_title[a.title].append(a.id)
    groups = {f"g{i}": ids for i, ids in enumerate(v for v in by_title.values() if len(v) > 1)}
    assert groups, "sample data should repeat some headlines"
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"articles": [a.id for a in articles], "groups": groups}))

    fake = FakeOpenRouter()
    report = run(sessions, str(gold), enrich_count=3, client=client(fake))
    assert report["articles"] == 60
    assert report["best"]["precision"] >= 0.9 and report["best"]["recall"] >= 0.9
    assert len(report["enriched"]) == 3
    assert report["cost"]["llm_calls"] == 6
    sent = [t for r in fake.requests if r["path"].endswith("/embeddings") for t in r["body"]["input"]]
    assert len(sent) == 60 and all(len(t) <= 400 for t in sent)  # headline + capped excerpt only
    text = render(report)
    assert "Chosen threshold" in text and "Estimated per 1,000 articles" in text


def test_missing_key_is_a_clear_error():
    import pytest

    from stories.openrouter import OpenRouterError

    with pytest.raises(OpenRouterError, match="IJAZ_OPENROUTER_KEY"):
        OpenRouter(StorySettings(api_key=""))


def test_answer_key_is_consistent():
    from pathlib import Path

    gold = json.loads((Path(__file__).parent.parent / "eval" / "stories_gold.json").read_text(encoding="utf-8"))
    articles = set(gold["articles"])
    seen = set()
    for name, ids in gold["groups"].items():
        assert len(ids) >= 2, name
        assert set(ids) <= articles, name
        assert not seen & set(ids), name
        seen |= set(ids)
