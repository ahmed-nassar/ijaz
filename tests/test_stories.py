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
        if schema == "judge":
            content = {"reason": "same event", "match": 1}
        elif schema == "story":
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
    assert fake.requests[0]["body"]["model"] == "openai/gpt-6-luna"  # the default writer
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


def test_cross_language_pairs_can_ignore_words():
    from stories.similarity import combine_by_language

    word = np.array([[1.0, 0.4, 0.0], [0.4, 1.0, 0.0], [0.0, 0.0, 1.0]])
    meaning = np.array([[1.0, 0.6, 0.8], [0.6, 1.0, 0.8], [0.8, 0.8, 1.0]])
    m = combine_by_language(word, meaning, ["en", "en", "ar"], 0.5, 0.0)
    assert m[0, 1] == 0.5  # same language: half and half
    assert m[0, 2] == 0.8  # across languages: meaning alone


def test_judge_decides_only_the_uncertain_band():
    from stories.grouping import group_with_judge

    items = [Item(i, T0 + timedelta(minutes=i)) for i in range(4)]
    score = np.array([
        [1, .9, .4, .1],
        [.9, 1, .4, .1],
        [.4, .4, 1, .1],
        [.1, .1, .1, 1],
    ])
    asked = []

    def judge(i, candidates):
        asked.append((i, [list(c) for c in candidates]))
        return 0

    groups = group_with_judge(items, score, low=0.3, high=0.8, judge=judge)
    assert groups == [[0, 1, 2], [3]]
    assert asked == [(2, [[0, 1]])]  # 1 joined on score alone; 3 is below the band
    assert group_with_judge(items, score, 0.3, 0.8, lambda i, c: None) == [[0, 1], [2], [3]]


def test_judge_call_maps_the_answer_to_a_candidate():
    from stories.enrich import judge

    fake = FakeOpenRouter()
    r = Report(1, "aa-en", "en", "2026-11-21 08:00", "Basra fire kills four", None)
    assert judge(client(fake), r, [[r], [r]]) == 0
    body = fake.requests[0]["body"]
    assert body["response_format"]["json_schema"]["name"] == "judge"
    assert "Candidate story 2" in body["messages"][1]["content"]


def test_comparison_end_to_end(sessions, tmp_path):
    from stories.evaluate import compare, render_compare

    with sessions() as s:
        seed(s, 40, now=T0)
        articles = s.query(Article).all()
    by_title = defaultdict(list)
    for a in articles:
        by_title[a.title].append(a.id)
    groups = {f"g{i}": ids for i, ids in enumerate(v for v in by_title.values() if len(v) > 1)}
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"articles": [a.id for a in articles], "groups": groups}))
    streamed, notes = [], []
    report = compare(sessions, str(gold), client=client(FakeOpenRouter()), models=["openai/text-embedding-3-small", "qwen/qwen3-embedding-8b"],
                     on_row=streamed.append, progress=notes.append)
    assert streamed == report["rows"]  # every row is handed out as soon as it is ready
    assert any("embedding" in n for n in notes)
    variants = [r["variant"] for r in report["rows"]]
    assert len(variants) == 12 and "muse-ember judge" in variants[-1] and "gemini-3.1-flash-lite judge" in variants[-6]
    h = report["headlines"]
    assert [m["model"] for m in h["models"]][-1] == "meta/muse-ember"
    assert all(m["written"] == len(h["stories"]) and m["passed_check"] == m["written"] for m in h["models"])
    assert "passed the check" in render_compare(report)
    assert "agreed design" in variants[0]
    assert "per 1,000 articles" in render_compare(report)


def test_word_similarity_matches_the_full_matrix():
    """Leaving out words that only one article uses must not change any score."""
    import math as _math
    from collections import Counter as _Counter

    docs = [article_words(t, None) for t in [
        "Fire at Basra power station kills four", "Basra power station fire: four dead",
        "Gold prices fall in Cairo", "Unique words only here", "Gold falls again in Cairo trading",
    ]]
    df = _Counter(w for d in docs for w in set(d))
    vocab = {w: i for i, w in enumerate(df)}
    m = np.zeros((len(docs), len(vocab)))
    for r, d in enumerate(docs):
        for w, c in _Counter(d).items():
            m[r, vocab[w]] = (1 + _math.log(c)) * (_math.log((1 + len(docs)) / (1 + df[w])) + 1)
    unit = m / np.linalg.norm(m, axis=1, keepdims=True)
    assert np.allclose(word_similarity(docs), unit @ unit.T, atol=1e-6)


def test_comparison_judge_runs_in_parallel_with_their_own_clients(sessions, tmp_path, monkeypatch):
    """Without a shared test client, each judge run gets its own client and they run side by side."""
    import stories.evaluate as evaluate

    with sessions() as s:
        seed(s, 40, now=T0)
        articles = s.query(Article).all()
    by_title = defaultdict(list)
    for a in articles:
        by_title[a.title].append(a.id)
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"articles": [a.id for a in articles],
                                "groups": {f"g{i}": v for i, v in enumerate(v for v in by_title.values() if len(v) > 1)}}))
    fake = FakeOpenRouter()
    made = []

    def factory(settings, client=None):
        c = OpenRouter(StorySettings(api_key="k", base_url="https://openrouter.test/api/v1",
                                     embedding_model=settings.embedding_model, llm_model=settings.llm_model),
                       client=httpx.Client(transport=httpx.MockTransport(fake)))
        made.append(settings.llm_model)
        return c

    monkeypatch.setattr(evaluate, "OpenRouter", factory)
    report = evaluate.compare(sessions, str(gold), settings=StorySettings(api_key="k"), models=["qwen/qwen3-embedding-8b"])
    judged = [r for r in report["rows"] if "judge between" in r.get("variant", "")]
    assert len(judged) == 6 and all(r["llm_calls"] >= 0 for r in judged)
    assert {"anthropic/claude-haiku-5.5", "openai/gpt-6-luna", "google/gemini-4-hydrogen"} <= set(made)
    # Headlines: every writer used its own model; the check always went to the fixed checker.
    chats = [r["body"] for r in fake.requests if r["path"].endswith("/chat/completions")]
    story_models = {b["model"] for b in chats if b["response_format"]["json_schema"]["name"] == "story"}
    check_models = {b["model"] for b in chats if b["response_format"]["json_schema"]["name"] == "check"}
    assert story_models == set(evaluate.LLM_MODELS) and check_models == {evaluate.HEADLINE_CHECKER}
    hydrogen = [b for b in chats if b["model"] == "google/gemini-4-hydrogen"]
    assert hydrogen and all(b["reasoning"] == {"effort": "low"} for b in hydrogen)


def test_json_replies_wrapped_in_text_or_fences_are_read():
    from stories.openrouter import parse_json

    assert parse_json('{"match": 1}') == {"match": 1}
    assert parse_json('```json\n{"match": 2}\n```') == {"match": 2}
    assert parse_json('Here is the answer: {"ok": true, "issues": []} Done.') == {"ok": True, "issues": []}
