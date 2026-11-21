"""Measure grouping against the hand-made answer key (eval/stories_gold.json).

Precision: of the article pairs we put in the same story, the share that the key also
puts together. Recall: of the pairs the key puts together, the share we found.
Targets from spec section 15: precision at least 95%, recall at least 85%.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from sqlalchemy import select

from collector.models import Article, Publisher

from .config import StorySettings
from .enrich import Report, enrich
from .grouping import Item, group, group_with_judge
from .openrouter import OpenRouter
from .similarity import combine, embedding_similarity, word_similarity
from .text import article_words, embedding_text

PRECISION_TARGET = 0.95
RECALL_TARGET = 0.85


@dataclass
class Row:
    id: int
    publisher: str
    language: str
    title: str
    excerpt: str | None
    time: object


def load_gold(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_rows(sessions, ids: list[int]) -> list[Row]:
    with sessions() as s:
        result = s.execute(
            select(Article, Publisher.slug).join(Publisher, Publisher.id == Article.publisher_id).where(Article.id.in_(ids))
        ).all()
    rows = []
    for a, slug in result:
        t = a.published_at if a.published_at and a.published_at < a.fetched_at else a.fetched_at
        rows.append(Row(a.id, slug, a.language or "", a.title, a.excerpt, t))
    return sorted(rows, key=lambda r: r.id)


def pairs(groups) -> set[tuple[int, int]]:
    out = set()
    for g in groups:
        out.update(combinations(sorted(g), 2))
    return out


def scores(predicted: set, gold: set) -> tuple[float, float, float]:
    hit = len(predicted & gold)
    p = hit / len(predicted) if predicted else 1.0
    r = hit / len(gold) if gold else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def sweep(items, matrix, gold_pairs, window_hours, thresholds):
    out = []
    for t in thresholds:
        groups = group(items, matrix, t, window_hours)
        p, r, f = scores(pairs(groups), gold_pairs)
        out.append({"threshold": round(t, 3), "precision": p, "recall": r, "f1": f, "stories": len(groups), "groups": groups})
    return out


def choose(results):
    """Highest recall with precision on target; otherwise the best F1."""
    ok = [r for r in results if r["precision"] >= PRECISION_TARGET]
    if ok:
        return max(ok, key=lambda r: (r["recall"], r["precision"]))
    return max(results, key=lambda r: r["f1"])


def run(sessions, gold_path: str, enrich_count: int = 20, settings: StorySettings | None = None, client: OpenRouter | None = None) -> dict:
    settings = settings or StorySettings()
    gold = load_gold(gold_path)
    rows = load_rows(sessions, gold["articles"])
    found = {r.id for r in rows}
    missing = [i for i in gold["articles"] if i not in found]
    if missing:
        raise SystemExit(f"{len(missing)} answer-key articles are not in this database (first: {missing[:5]})")
    by_id = {r.id: r for r in rows}
    gold_groups = list(gold["groups"].values())
    gold_pairs = pairs(gold_groups)
    cross = {p for p in gold_pairs if by_id[p[0]].language != by_id[p[1]].language}

    client = client or OpenRouter(settings)
    word = word_similarity([article_words(r.title, r.excerpt) for r in rows])
    meaning = embedding_similarity(client.embed([embedding_text(r.title, r.excerpt) for r in rows]))
    items = [Item(r.id, r.time) for r in rows]
    thresholds = list(np.arange(0.20, 0.801, 0.025))

    combined = sweep(items, combine(word, meaning, settings.word_weight), gold_pairs, settings.window_hours, thresholds)
    word_only = sweep(items, word, gold_pairs, settings.window_hours, thresholds)
    meaning_only = sweep(items, meaning, gold_pairs, settings.window_hours, thresholds)
    best = choose(combined)
    predicted = pairs(best["groups"])

    def describe(pair):
        a, b = by_id[pair[0]], by_id[pair[1]]
        return {"a": [a.id, a.publisher, a.title], "b": [b.id, b.publisher, b.title]}

    stories = sorted(best["groups"], key=len, reverse=True)
    enriched = []
    for g in stories[:enrich_count]:
        reports = [Report(i, by_id[i].publisher, by_id[i].language, by_id[i].time.strftime("%Y-%m-%d %H:%M"), by_id[i].title, by_id[i].excerpt)
                   for i in sorted(g, key=lambda i: by_id[i].time)]
        try:
            result = enrich(client, reports)
        except Exception as e:  # one bad response should not lose the whole measurement
            result = {"error": str(e)}
        enriched.append({"articles": [[i, by_id[i].publisher, by_id[i].title] for i in g], "result": result})

    n = len(rows)
    emb_cost = client.embedding_usage.cost_usd
    llm_calls_per_story = 2
    llm_cost_per_story = client.llm_usage.cost_usd / (len(enriched) or 1)
    stories_per_article = best["stories"] / n
    return {
        "articles": n,
        "gold_stories_with_2_or_more": len(gold_groups),
        "gold_pairs": len(gold_pairs),
        "cross_language_gold_pairs": len(cross),
        "weights": {"word": settings.word_weight, "meaning": 1 - settings.word_weight},
        "models": {"embedding": settings.embedding_model, "llm": settings.llm_model},
        "best": {k: best[k] for k in ("threshold", "precision", "recall", "f1", "stories")}
        | {"cross_language_recall": scores(predicted, cross)[1] if cross else None},
        "sweep": {
            "combined": [{k: r[k] for k in ("threshold", "precision", "recall", "f1", "stories")} for r in combined],
            "word_only_best": {k: choose(word_only)[k] for k in ("threshold", "precision", "recall", "f1")},
            "meaning_only_best": {k: choose(meaning_only)[k] for k in ("threshold", "precision", "recall", "f1")},
        },
        "wrongly_joined": [describe(p) for p in sorted(predicted - gold_pairs)][:25],
        "missed": [describe(p) for p in sorted(gold_pairs - predicted)][:25],
        "enriched": enriched,
        "cost": {
            "embedding_usd": emb_cost,
            "llm_usd": client.llm_usage.cost_usd,
            "llm_calls": client.llm_usage.calls,
            "llm_calls_per_story": llm_calls_per_story,
            "reported_by_openrouter": client.embedding_usage.cost_reported and client.llm_usage.cost_reported,
            "embedding_per_1000_articles_usd": emb_cost / n * 1000,
            "llm_per_1000_articles_usd": llm_cost_per_story * stories_per_article * 1000,
        },
    }


def render(report: dict) -> str:
    b = report["best"]
    c = report["cost"]
    lines = [
        f"Answer key: {report['articles']} articles, {report['gold_stories_with_2_or_more']} stories with 2 or more, "
        f"{report['gold_pairs']} same-story pairs ({report['cross_language_gold_pairs']} across languages)",
        f"Weights: words {report['weights']['word']:.2f}, meaning {report['weights']['meaning']:.2f}; "
        f"models {report['models']['embedding']} and {report['models']['llm']}",
        "",
        f"Chosen threshold {b['threshold']}: precision {b['precision']:.1%}, recall {b['recall']:.1%}, "
        f"cross-language recall {b['cross_language_recall']:.1%}" if b["cross_language_recall"] is not None else
        f"Chosen threshold {b['threshold']}: precision {b['precision']:.1%}, recall {b['recall']:.1%}",
        f"Targets: precision {PRECISION_TARGET:.0%}, recall {RECALL_TARGET:.0%}",
        "",
        "threshold  precision  recall  stories",
    ]
    for r in report["sweep"]["combined"]:
        lines.append(f"{r['threshold']:>9}  {r['precision']:>9.1%}  {r['recall']:>6.1%}  {r['stories']:>7}")
    w, m = report["sweep"]["word_only_best"], report["sweep"]["meaning_only_best"]
    lines += [
        "",
        f"Words alone, best: threshold {w['threshold']}, precision {w['precision']:.1%}, recall {w['recall']:.1%}",
        f"Meaning alone, best: threshold {m['threshold']}, precision {m['precision']:.1%}, recall {m['recall']:.1%}",
        "",
        "Wrongly joined (first 25):",
    ]
    lines += [f"  {x['a'][1]}: {x['a'][2]}\n    + {x['b'][1]}: {x['b'][2]}" for x in report["wrongly_joined"]]
    lines += ["", "Missed (first 25):"]
    lines += [f"  {x['a'][1]}: {x['a'][2]}\n    + {x['b'][1]}: {x['b'][2]}" for x in report["missed"]]
    lines += ["", f"Language model output for the {len(report['enriched'])} largest stories:"]
    for e in report["enriched"]:
        r = e["result"]
        if "error" in r:
            lines.append(f"  ERROR {r['error']}")
            continue
        q = "ok" if r["quality"]["ok"] else "CHECK: " + "; ".join(r["quality"]["issues"])
        lines.append(f"  [{r['importance']}] {r['headline_ar']}  ({len(e['articles'])} reports, date {r['event_date']}, {q})")
    lines += [
        "",
        f"Cost: embeddings ${c['embedding_usd']:.4f}, language model ${c['llm_usd']:.4f} for {c['llm_calls']} calls"
        + ("" if c["reported_by_openrouter"] else " (OpenRouter did not report cost for every call)"),
        f"Estimated per 1,000 articles: embeddings ${c['embedding_per_1000_articles_usd']:.4f}, "
        f"language model ${c['llm_per_1000_articles_usd']:.3f} (each story written once)",
    ]
    return "\n".join(lines)


COMPARE_MODELS = ["openai/text-embedding-3-small", "openai/text-embedding-3-large", "qwen/qwen3-embedding-8b"]


def compare(sessions, gold_path: str, settings: StorySettings | None = None, client: OpenRouter | None = None,
            models: list[str] | None = None) -> dict:
    """Measure the agreed design next to the candidate fixes, on the same answer key:
    stronger embedding models, no word weight across languages, and a Gemini judge
    for uncertain matches. Every row reports precision, recall and cost."""
    from dataclasses import replace

    from .enrich import judge as judge_call
    from .similarity import combine_by_language

    settings = settings or StorySettings()
    gold = load_gold(gold_path)
    rows = load_rows(sessions, gold["articles"])
    if len(rows) != len(gold["articles"]):
        raise SystemExit("some answer-key articles are not in this database")
    by_id = {r.id: r for r in rows}
    gold_pairs = pairs(list(gold["groups"].values()))
    cross = {p for p in gold_pairs if by_id[p[0]].language != by_id[p[1]].language}
    items = [Item(r.id, r.time) for r in rows]
    langs = [r.language for r in rows]
    thresholds = list(np.arange(0.20, 0.801, 0.025))
    word = word_similarity([article_words(r.title, r.excerpt) for r in rows])
    texts = [embedding_text(r.title, r.excerpt) for r in rows]
    n = len(rows)

    out: list[dict] = []
    matrices: dict[str, np.ndarray] = {}

    def add(name, matrix, model, emb_cost):
        best = choose(sweep(items, matrix, gold_pairs, settings.window_hours, thresholds))
        predicted = pairs(best["groups"])
        out.append({"variant": name, "model": model, "threshold": best["threshold"], "precision": best["precision"],
                    "recall": best["recall"], "cross_language_recall": scores(predicted, cross)[1] if cross else None,
                    "llm_calls": 0, "cost_per_1000_usd": emb_cost / n * 1000})
        matrices[name] = matrix

    for model in models or COMPARE_MODELS:
        c = client or OpenRouter(replace(settings, embedding_model=model))
        if client is not None:
            c.settings = replace(settings, embedding_model=model)
        before = c.embedding_usage.cost_usd
        try:
            meaning = embedding_similarity(c.embed(texts))
        except Exception as e:
            out.append({"variant": "error", "model": model, "error": str(e)[:200]})
            continue
        cost = c.embedding_usage.cost_usd - before
        short = model.split("/")[-1]
        add(f"{short}: words 0.5, meaning 0.5 (agreed design)" if model == settings.embedding_model else f"{short}: words 0.5, meaning 0.5",
            combine(word, meaning, 0.5), model, cost)
        add(f"{short}: words 0.5 same language, 0 across languages", combine_by_language(word, meaning, langs, 0.5, 0.0), model, cost)
        add(f"{short}: meaning only", meaning, model, cost)

    ok = [r for r in out if "error" not in r and r["precision"] >= PRECISION_TARGET]
    if ok:
        base = max(ok, key=lambda r: (r["recall"], r["precision"]))
        for judge_model, width in JUDGE_RUNS:
            out.append(_judged(base, matrices[base["variant"]], judge_model, width, settings, client, rows, by_id, items,
                               gold_pairs, cross, n))
    return {"articles": n, "gold_pairs": len(gold_pairs), "cross_language_gold_pairs": len(cross), "rows": out}


# (judge model, width of the uncertain band below the threshold)
JUDGE_RUNS = [
    ("google/gemini-3.1-flash-lite", 0.15),
    ("google/gemini-3.1-flash-lite", 0.25),
    ("anthropic/claude-haiku-5.5", 0.25),
]


def _judged(base, matrix, judge_model, width, settings, client, rows, by_id, items, gold_pairs, cross, n):
    from dataclasses import replace

    from .enrich import judge as judge_call

    high = base["threshold"]
    low = max(0.2, high - width)
    c = client or OpenRouter(replace(settings, llm_model=judge_model))
    if client is not None:
        c.settings = replace(settings, llm_model=judge_model)
    before_cost, before_calls = c.llm_usage.cost_usd, c.llm_usage.calls
    errors: list[str] = []

    def report(i):
        r = rows[i]
        return Report(r.id, r.publisher, r.language, r.time.strftime("%Y-%m-%d %H:%M"), r.title, r.excerpt)

    def ask(i, candidates):
        try:
            return judge_call(c, report(i), [[report(m) for m in story] for story in candidates])
        except Exception as e:
            errors.append(str(e)[:200])
            return None

    groups = group_with_judge(items, matrix, low, high, ask, settings.window_hours)
    predicted = pairs(groups)
    p, r, _ = scores(predicted, gold_pairs)
    return {"variant": f"{base['variant']} + {judge_model.split('/')[-1]} judge between {low:.3f} and {high:.3f}",
            "model": base["model"], "threshold": high, "precision": p, "recall": r,
            "cross_language_recall": scores(predicted, cross)[1] if cross else None,
            "llm_calls": c.llm_usage.calls - before_calls, "judge_errors": len(errors), "first_error": errors[0] if errors else None,
            "cost_per_1000_usd": base["cost_per_1000_usd"] + (c.llm_usage.cost_usd - before_cost) / n * 1000,
            "wrongly_joined": [[by_id[a].title, by_id[b].title] for a, b in sorted(predicted - gold_pairs)][:15],
            "missed": [[by_id[a].title, by_id[b].title] for a, b in sorted(gold_pairs - predicted)][:15]}




def render_compare(report: dict) -> str:
    lines = [f"Answer key: {report['articles']} articles, {report['gold_pairs']} same-story pairs "
             f"({report['cross_language_gold_pairs']} across languages). Targets: precision {PRECISION_TARGET:.0%}, recall {RECALL_TARGET:.0%}.",
             "Each row uses the threshold with the highest recall at precision 95% or more (or the best balance if none reaches 95%).", ""]
    for r in report["rows"]:
        if "error" in r:
            lines.append(f"{r['model']}: could not run ({r['error']})")
            continue
        x = f"{r['cross_language_recall']:.0%}" if r["cross_language_recall"] is not None else "n/a"
        lines.append(f"{r['variant']}\n    threshold {r['threshold']}: precision {r['precision']:.1%}, recall {r['recall']:.1%}, "
                     f"across languages {x}; {r['llm_calls']} judge calls; ${r['cost_per_1000_usd']:.3f} per 1,000 articles")
        if r.get("judge_errors"):
            lines.append(f"    {r['judge_errors']} judge calls failed, first: {r['first_error']}")
    for r in report["rows"]:
        if "wrongly_joined" not in r:
            continue
        lines += ["", f"{r['variant']}", "  Wrongly joined (first 15):"] + [f"    {a}\n      + {b}" for a, b in r["wrongly_joined"]]
        lines += ["  Missed (first 15):"] + [f"    {a}\n      + {b}" for a, b in r["missed"]]
    return "\n".join(lines)
