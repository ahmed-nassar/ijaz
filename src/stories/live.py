"""The story step on live articles: group new articles into stories, ask the language
model for headlines and importance, rank, and save a homepage snapshot.

Runs after each collector round (``collector run``), and once with ``collector stories-run``.
Only first reports are grouped; syndicated copies are shown with their original.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from collector.models import (
    Article,
    ArticleEmbedding,
    HomepageSnapshot,
    Publisher,
    Story,
    StoryArticle,
    river_time,
)

from . import rank as ranking
from .config import LiveSettings, StorySettings
from .enrich import Report, enrich, judge
from .grouping import Item, extend
from .openrouter import OpenRouter, OpenRouterError
from .similarity import combine_by_language, embedding_similarity, word_similarity
from .text import article_words, embedding_text

log = logging.getLogger("stories")

SNAPSHOT_KEEP_DAYS = 7


@dataclass
class StepReport:
    articles_in_window: int = 0
    new_articles: int = 0
    joined_by_score: int = 0
    joined_by_judge: int = 0
    new_stories: int = 0
    embedded: int = 0
    judge_calls: int = 0
    judge_errors: int = 0
    enriched: int = 0
    enrich_errors: int = 0
    snapshot_id: int | None = None
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def run_step(
    sessions: sessionmaker[Session],
    live: LiveSettings | None = None,
    settings: StorySettings | None = None,
    client: OpenRouter | None = None,
    now: datetime | None = None,
) -> StepReport:
    live = live or LiveSettings()
    settings = replace(settings or StorySettings(), embedding_model=live.embedding_model)
    own_client = client is None
    client = client or OpenRouter(settings)
    now = now or datetime.now(timezone.utc)
    report = StepReport()
    try:
        with sessions() as session:
            _group(session, client, live, now, report)
            session.commit()
            _enrich(session, client, live, now, report)
            report.snapshot_id = snapshot(session, live, now)
            session.commit()
    finally:
        report.cost_usd = round(client.embedding_usage.cost_usd + client.llm_usage.cost_usd, 5)
        if own_client:
            client.close()
    return report


# --- grouping -------------------------------------------------------------------------

def _group(session: Session, client: OpenRouter, live: LiveSettings, now: datetime, report: StepReport) -> None:
    when = river_time().label("when")
    since = now - timedelta(hours=live.window_hours)
    rows = session.execute(
        select(Article.id, Article.title, Article.excerpt, Article.language, when, Publisher.name)
        .join(Publisher, Publisher.id == Article.publisher_id)
        .where(Article.syndicated_of_id.is_(None), river_time() >= since)
        .order_by(river_time(), Article.id)
    ).all()
    if not rows:
        return
    ids = [r[0] for r in rows]
    assigned = dict(session.execute(
        select(StoryArticle.article_id, StoryArticle.story_id).where(StoryArticle.article_id.in_(ids))
    ).all())
    new_ids = [i for i in ids if i not in assigned][: live.max_new_per_run]
    if not new_ids:
        report.articles_in_window = len(rows)
        return
    keep = set(assigned) | set(new_ids)
    rows = [r for r in rows if r[0] in keep]
    ids = [r[0] for r in rows]
    report.articles_in_window = len(rows)
    report.new_articles = len(new_ids)

    vectors = _embeddings(session, client, live.embedding_model, rows, report)
    items = [Item(r[0], _utc(r[4])) for r in rows]
    word = word_similarity([article_words(r[1], r[2]) for r in rows])
    meaning = embedding_similarity([vectors[i] for i in ids])
    score = combine_by_language(word, meaning, [r[3] for r in rows], live.word_weight_same, live.word_weight_cross)

    index = {article_id: n for n, article_id in enumerate(ids)}
    story_ids: list[int | None] = []
    stories: list[list[int]] = []
    by_story: dict[int, list[int]] = {}
    for article_id, story_id in assigned.items():
        if article_id in index:
            by_story.setdefault(story_id, []).append(index[article_id])
    for story_id, members in by_story.items():
        story_ids.append(story_id)
        stories.append(members)

    reports = [Report(r[0], r[5], r[3], _utc(r[4]).strftime("%Y-%m-%d %H:%M"), r[1], r[2]) for r in rows]

    def ask(i: int, candidates: list[list[int]]) -> int | None:
        report.judge_calls += 1
        try:
            return judge(client, reports[i], [[reports[m] for m in sorted(c, key=lambda m: items[m].time)] for c in candidates])
        except (OpenRouterError, KeyError, ValueError, TypeError) as exc:
            report.judge_errors += 1
            if len(report.errors) < 5:
                report.errors.append(f"judge: {exc}")
            return None

    low = live.judge_low if live.judge_low > 0 else live.threshold
    placed = extend(items, score, stories, [index[i] for i in new_ids], low, live.threshold,
                    judge=ask if live.judge_low > 0 else None, window_hours=live.window_hours)

    touched: set[int] = set()
    for i, position, value, how in placed:
        while len(story_ids) <= position:
            story_ids.append(None)
        if story_ids[position] is None:
            story = Story(created_at=now, first_time=items[i].time, last_time=items[i].time, article_count=0)
            session.add(story)
            session.flush()
            story_ids[position] = story.id
            report.new_stories += 1
        if how == "score":
            report.joined_by_score += 1
        elif how == "judge":
            report.joined_by_judge += 1
        session.add(StoryArticle(article_id=ids[i], story_id=story_ids[position], joined_at=now,
                                 score=None if value is None else round(value, 4), how=how))
        touched.add(story_ids[position])
    session.flush()
    refresh_stories(session, touched)


def _embeddings(session: Session, client: OpenRouter, model: str, rows, report: StepReport) -> dict[int, np.ndarray]:
    ids = [r[0] for r in rows]
    stored = {
        article_id: np.frombuffer(vector, dtype=np.float32)
        for article_id, vector in session.execute(
            select(ArticleEmbedding.article_id, ArticleEmbedding.vector)
            .where(ArticleEmbedding.model == model, ArticleEmbedding.article_id.in_(ids))
        )
    }
    missing = [r for r in rows if r[0] not in stored]
    if missing:
        fresh = client.embed([embedding_text(r[1], r[2]) for r in missing], model=model)
        for r, vector in zip(missing, fresh, strict=True):
            array = np.asarray(vector, dtype=np.float32)
            stored[r[0]] = array
            session.add(ArticleEmbedding(article_id=r[0], model=model, vector=array.tobytes()))
        report.embedded = len(missing)
    return stored


def refresh_stories(session: Session, story_ids: set[int]) -> None:
    """Recompute article count, first and last time and the lead article.

    The lead is the report from the publisher with the highest authority; among equals,
    the earliest (spec section 5: chosen by authority, not by who published first)."""
    if not story_ids:
        return
    when = river_time().label("when")
    rows = session.execute(
        select(StoryArticle.story_id, Article.id, when, Publisher.authority)
        .join(Article, Article.id == StoryArticle.article_id)
        .join(Publisher, Publisher.id == Article.publisher_id)
        .where(StoryArticle.story_id.in_(story_ids))
    ).all()
    members: dict[int, list] = {}
    for story_id, article_id, at, authority in rows:
        members.setdefault(story_id, []).append((article_id, _utc(at), authority or 0.0))
    for story in session.scalars(select(Story).where(Story.id.in_(story_ids))):
        m = members.get(story.id, [])
        if not m:
            continue
        story.article_count = len(m)
        story.first_time = min(t for _, t, _ in m)
        story.last_time = max(t for _, t, _ in m)
        story.lead_article_id = min(m, key=lambda x: (-x[2], x[1], x[0]))[0]


# --- language model -------------------------------------------------------------------

def needs_enrichment(article_count: int, enriched_count: int) -> bool:
    """First when a story has two reports; again each time it has grown by half."""
    if article_count < 2:
        return False
    if enriched_count == 0:
        return True
    return article_count >= enriched_count + max(1, (enriched_count + 1) // 2)


def _enrich(session: Session, client: OpenRouter, live: LiveSettings, now: datetime, report: StepReport) -> None:
    since = now - timedelta(hours=live.window_hours)
    stories = [
        s for s in session.scalars(
            select(Story).where(Story.merged_into_id.is_(None), Story.article_count >= 2, Story.last_time >= since)
            .order_by(Story.article_count.desc(), Story.last_time.desc())
        )
        if needs_enrichment(s.article_count, s.enriched_count)
    ][: live.enrich_per_run]
    for story in stories:
        reports = story_reports(session, story.id)
        try:
            result = enrich(client, reports)
        except (OpenRouterError, KeyError, ValueError, TypeError) as exc:
            report.enrich_errors += 1
            if len(report.errors) < 5:
                report.errors.append(f"story {story.id}: {exc}")
            continue
        quality = result.get("quality") or {}
        story.headline_ar = (result.get("headline_ar") or "").strip()[:200] or None
        story.headline_ok = bool(quality.get("ok")) and story.headline_ar is not None
        story.headline_issues = list(quality.get("issues") or [])
        story.importance = int(result["importance"]) if result.get("importance") else None
        story.importance_reason = result.get("importance_reason")
        story.event_date = (result.get("event_date") or None) and str(result["event_date"])[:10]
        story.enriched_count = story.article_count
        story.enriched_at = now
        translations = {int(t["id"]): t["title_ar"] for t in result.get("translations") or [] if t.get("title_ar")}
        if translations:
            for link in session.scalars(select(StoryArticle).where(
                StoryArticle.story_id == story.id, StoryArticle.article_id.in_(translations)
            )):
                link.title_ar = translations[link.article_id][:500]
        session.commit()
        report.enriched += 1


def story_reports(session: Session, story_id: int, limit: int = 12) -> list[Report]:
    """The reports sent to the language model: the lead first, then the newest."""
    when = river_time().label("when")
    rows = session.execute(
        select(Article.id, Publisher.name, Article.language, when, Article.title, Article.excerpt)
        .join(StoryArticle, StoryArticle.article_id == Article.id)
        .join(Publisher, Publisher.id == Article.publisher_id)
        .where(StoryArticle.story_id == story_id)
        .order_by(river_time().desc())
    ).all()
    lead = session.scalar(select(Story.lead_article_id).where(Story.id == story_id))
    rows.sort(key=lambda r: r[0] != lead)
    return [Report(r[0], r[1], r[2], _utc(r[3]).strftime("%Y-%m-%d %H:%M"), r[4], r[5]) for r in rows[:limit]]


# --- ranking and snapshot -------------------------------------------------------------

def snapshot(session: Session, live: LiveSettings, now: datetime) -> int | None:
    since = now - timedelta(hours=live.window_hours)
    when = river_time().label("when")
    rows = session.execute(
        select(Story.id, Story.importance, Story.last_time, Story.lead_article_id,
               Article.id, when, Publisher.slug, Publisher.owner_group, Publisher.authority,
               Publisher.state_affiliated, Publisher.country)
        .join(StoryArticle, StoryArticle.story_id == Story.id)
        .join(Article, Article.id == StoryArticle.article_id)
        .join(Publisher, Publisher.id == Article.publisher_id)
        .where(Story.merged_into_id.is_(None), Story.last_time >= since)
    ).all()
    if not rows:
        return None
    candidates: dict[int, ranking.Candidate] = {}
    for story_id, importance, last_time, lead_id, article_id, at, slug, owner, authority, official, country in rows:
        c = candidates.get(story_id)
        if c is None:
            c = candidates[story_id] = ranking.Candidate(story_id, "", None, importance, _utc(last_time))
        c.reports.append(ranking.Report(slug, owner, authority if authority is not None else 0.5, bool(official), _utc(at)))
        if article_id == lead_id:
            c.lead_publisher, c.lead_country = slug, country
    ranked = ranking.rank(list(candidates.values()), now, live.half_life_hours, live.velocity_beta, live.homepage_size)
    shot = HomepageSnapshot(
        created_at=now,
        settings={
            "embedding_model": live.embedding_model, "word_weight_same": live.word_weight_same,
            "word_weight_cross": live.word_weight_cross, "threshold": live.threshold,
            "judge_low": live.judge_low, "half_life_hours": live.half_life_hours,
            "velocity_beta": live.velocity_beta,
        },
        stories=ranked,
    )
    session.add(shot)
    session.execute(delete(HomepageSnapshot).where(HomepageSnapshot.created_at < now - timedelta(days=SNAPSHOT_KEEP_DAYS)))
    session.flush()
    return shot.id


def counts(session: Session) -> dict:
    return {
        "stories": session.scalar(select(func.count(Story.id))),
        "with_headline": session.scalar(select(func.count(Story.id)).where(Story.headline_ok.is_(True))),
    }
