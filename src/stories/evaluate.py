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
            models: list[str] | None = None, on_row=None, progress=None) -> dict:
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
        matrices[name] = matrix
        emit({"variant": name, "model": model, "threshold": best["threshold"], "precision": best["precision"],
                    "recall": best["recall"], "cross_language_recall": scores(predicted, cross)[1] if cross else None,
                    "llm_calls": 0, "cost_per_1000_usd": emb_cost / n * 1000})

    def emit(row):
        out.append(row)
        if on_row:
            on_row(row)

    def say(text):
        if progress:
            progress(text)

    for model in models or COMPARE_MODELS:
        say(f"embedding {n} articles with {model}")
        c = client or OpenRouter(replace(settings, embedding_model=model))
        if client is not None:
            c.settings = replace(settings, embedding_model=model)
        before = c.embedding_usage.cost_usd
        try:
            meaning = embedding_similarity(c.embed(texts))
        except Exception as e:
            emit({"variant": "error", "model": model, "error": str(e)[:200]})
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
        # The judge runs are independent, so they run side by side (each makes its
        # calls in article order, as grouping needs). A shared test client stays sequential.
        say(f"{len(JUDGE_RUNS)} judge runs on: {base['variant']}")
        args = [(base, matrices[base["variant"]], m, w, settings, client, rows, by_id, items, gold_pairs, cross, n)
                for m, w in JUDGE_RUNS]
        if client is not None:
            for a in args:
                emit(_judged(*a))
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=len(args)) as pool:
                futures = [pool.submit(_judged, *a, progress=say) for a in args]
                for f in futures:
                    try:
                        emit(f.result())
                    except Exception as e:  # one failed run must not lose the others
                        emit({"variant": "error", "model": "judge", "error": str(e)[:200]})
    headlines = _headline_comparison(gold, rows, by_id, n, settings, client, say)
    return {"articles": n, "gold_pairs": len(gold_pairs), "cross_language_gold_pairs": len(cross), "rows": out,
            "headlines": headlines}


def _headline_comparison(gold, rows, by_id, n, settings, client, say) -> dict:
    """Each model writes headlines for the same largest answer-key stories; one fixed
    model checks them all. Cost per 1,000 articles assumes each story with two or more
    reports is written once (re-writes as stories grow add to it)."""
    from dataclasses import replace

    stories = sorted(gold["groups"].items(), key=lambda kv: len(kv[1]), reverse=True)[:HEADLINE_STORIES]
    reports = {
        name: [Report(i, by_id[i].publisher, by_id[i].language, by_id[i].time.strftime("%Y-%m-%d %H:%M"), by_id[i].title, by_id[i].excerpt)
               for i in sorted(ids, key=lambda i: by_id[i].time)]
        for name, ids in stories
    }
    stories_per_article = len(gold["groups"]) / n

    def one(model):
        say(f"headlines: {model}")
        writer = client or OpenRouter(replace(settings, llm_model=model))
        checker = client or OpenRouter(replace(settings, llm_model=HEADLINE_CHECKER))
        if client is not None:
            writer.settings = replace(settings, llm_model=model)
        out, cost, calls, errors = {}, 0.0, 0, []
        for name, rs in reports.items():
            before, before_calls = writer.llm_usage.cost_usd, writer.llm_usage.calls
            try:
                r = enrich(writer, rs, checker=checker if client is None else None)
                out[name] = {"headline": r["headline_ar"], "importance": r.get("importance"),
                             "ok": bool(r["quality"].get("ok")), "issues": r["quality"].get("issues") or []}
            except Exception as e:
                out[name] = {"error": str(e)[:200]}
                errors.append(str(e)[:200])
            # Only the writing call is the model's own cost; the check is the shared referee.
            cost += writer.llm_usage.cost_usd - before if client is None else (writer.llm_usage.cost_usd - before) / 2
            calls += writer.llm_usage.calls - before_calls
        written = [v for v in out.values() if "error" not in v]
        per_story = cost / (len(written) or 1)
        return {"model": model, "stories": len(reports), "written": len(written),
                "passed_check": sum(1 for v in written if v["ok"]), "errors": len(errors), "first_error": errors[0] if errors else None,
                "cost_per_story_usd": per_story, "cost_per_1000_usd": per_story * stories_per_article * 1000, "by_story": out}

    if client is not None:
        results = [one(m) for m in LLM_MODELS]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(LLM_MODELS)) as pool:
            results = list(pool.map(one, LLM_MODELS))
    return {"checker": HEADLINE_CHECKER, "stories": [name for name, _ in stories],
            "story_sizes": {name: len(ids) for name, ids in stories}, "models": results}


# Language models compared as judge and as headline writer (Ahmed, 21 Nov 2026):
# the two measured before as baselines, then DeepSeek V4.1 Flash, GPT-6 Luna, Gemini 4 Hydrogen
# and Meta's Muse Ember (Haiku's price and benchmark score, about twice as fast).
LLM_MODELS = [
    "google/gemini-3.1-flash-lite",
    "anthropic/claude-haiku-5.5",
    "deepseek/deepseek-v4.1-flash-1017",
    "openai/gpt-6-luna",
    "google/gemini-4-hydrogen",
    "meta/muse-ember",
]
# GPT-6 Luna is the chosen judge (21 Nov 2026), so the judge runs now compare how far
# below the threshold its band reaches: (judge model, band width below the threshold).
# 0.25 is the live setting (judge from 0.375 to 0.625).
JUDGE_MODEL = "openai/gpt-6-luna"
JUDGE_RUNS = [(JUDGE_MODEL, w) for w in (0.25, 0.325, 0.4)]
# Every writer's headline is checked by the same model, so the pass rates compare.
HEADLINE_CHECKER = "anthropic/claude-haiku-5.5"
HEADLINE_STORIES = 12


def _judged(base, matrix, judge_model, width, settings, client, rows, by_id, items, gold_pairs, cross, n, progress=None):
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
        if progress and (c.llm_usage.calls - before_calls) % 50 == 49:
            progress(f"{judge_model} (band {width}): {c.llm_usage.calls - before_calls + 1} calls")
        try:
            return judge_call(c, report(i), [[report(m) for m in story] for story in candidates])
        except Exception as e:
            errors.append(str(e)[:200])
            return None

    groups = group_with_judge(items, matrix, low, high, ask, settings.window_hours)
    predicted = pairs(groups)
    p, r, _ = scores(predicted, gold_pairs)
    index = {row.id: k for k, row in enumerate(rows)}

    def pair_score(pair):
        return float(matrix[index[pair[0]], index[pair[1]]])

    # Where recall is lost: a missed pair scoring below the band was never shown to the
    # judge; one in the band was refused (or another story was picked); one above the
    # threshold scored high as a pair but its story's two-best average did not.
    missed_pairs = sorted(gold_pairs - predicted, key=pair_score, reverse=True)
    reasons = {"below the band": 0, "in the band": 0, "above the threshold": 0}
    reasons_cross = dict(reasons)
    for pair in missed_pairs:
        value = pair_score(pair)
        key = "below the band" if value < low else "in the band" if value < high else "above the threshold"
        reasons[key] += 1
        if pair in cross:
            reasons_cross[key] += 1
    return {"variant": f"{base['variant']} + {judge_model.split('/')[-1]} judge between {low:.3f} and {high:.3f} (band {width})",
            "model": base["model"], "threshold": high, "precision": p, "recall": r,
            "cross_language_recall": scores(predicted, cross)[1] if cross else None,
            "llm_calls": c.llm_usage.calls - before_calls, "judge_errors": len(errors), "first_error": errors[0] if errors else None,
            "cost_per_1000_usd": base["cost_per_1000_usd"] + (c.llm_usage.cost_usd - before_cost) / n * 1000,
            "missed_reasons": reasons, "missed_reasons_cross_language": reasons_cross,
            "wrongly_joined": [[by_id[a].title, by_id[b].title] for a, b in sorted(predicted - gold_pairs)][:25],
            "missed": [[by_id[a].title, by_id[b].title, round(pair_score((a, b)), 3), (a, b) in cross]
                       for a, b in missed_pairs][:40]}




def render_row(r: dict) -> str:
    if "error" in r:
        return f"{r['model']}: could not run ({r['error']})"
    x = f"{r['cross_language_recall']:.0%}" if r["cross_language_recall"] is not None else "n/a"
    line = (f"{r['variant']}\n    threshold {r['threshold']}: precision {r['precision']:.1%}, recall {r['recall']:.1%}, "
            f"across languages {x}; {r['llm_calls']} judge calls; ${r['cost_per_1000_usd']:.3f} per 1,000 articles")
    if r.get("judge_errors"):
        line += f"\n    {r['judge_errors']} judge calls failed, first: {r['first_error']}"
    return line


def compare_header(articles: int, gold_pairs: int, cross: int) -> str:
    return "\n".join([
        f"Answer key: {articles} articles, {gold_pairs} same-story pairs ({cross} across languages). "
        f"Targets: precision {PRECISION_TARGET:.0%}, recall {RECALL_TARGET:.0%}.",
        "Each row uses the threshold with the highest recall at precision 95% or more (or the best balance if none reaches 95%).", ""])


def render_headlines(h: dict | None) -> list[str]:
    if not h:
        return []
    lines = ["", f"Headlines: each model writes the {len(h['stories'])} largest answer-key stories; {h['checker']} checks all of them.", ""]
    for m in h["models"]:
        lines.append(f"{m['model']}: {m['passed_check']} of {m['written']} passed the check"
                     + (f", {m['errors']} failed ({m['first_error']})" if m["errors"] else "")
                     + f"; ${m['cost_per_story_usd']:.5f} per story, about ${m['cost_per_1000_usd']:.3f} per 1,000 articles")
    for name in h["stories"]:
        lines += ["", f"{name} ({h['story_sizes'][name]} reports)"]
        for m in h["models"]:
            v = m["by_story"].get(name, {})
            if "error" in v:
                lines.append(f"  {m['model'].split('/')[-1]}: ERROR")
                continue
            mark = "ok" if v.get("ok") else "CHECK: " + "; ".join(v.get("issues") or [])
            lines.append(f"  {m['model'].split('/')[-1]} [{v.get('importance')}]: {v.get('headline')}  ({mark})")
    return lines


def render_compare(report: dict, rows: bool = True) -> str:
    lines = [compare_header(report["articles"], report["gold_pairs"], report["cross_language_gold_pairs"])] if rows else []
    if rows:
        lines += [render_row(r) for r in report["rows"]]
    lines += render_headlines(report.get("headlines"))
    for r in report["rows"]:
        if "wrongly_joined" not in r:
            continue
        lines += ["", f"{r['variant']}", "  Wrongly joined (first 25):"] + [f"    {a}\n      + {b}" for a, b in r["wrongly_joined"]]
        if "missed_reasons" in r:
            why = ", ".join(f"{k} {v} ({r['missed_reasons_cross_language'][k]} across languages)" for k, v in r["missed_reasons"].items())
            lines += [f"  Missed pairs by pair score: {why}"]
        lines += ["  Missed (highest pair score first, up to 40):"]
        for m in r["missed"]:
            a, b = m[0], m[1]
            note = f" [score {m[2]}{', across languages' if m[3] else ''}]" if len(m) > 2 else ""
            lines.append(f"    {a}\n      + {b}{note}")
    return "\n".join(lines)
