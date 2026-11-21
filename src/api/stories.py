"""Homepage and story page queries (spec section 15)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from collector.models import Article, HomepageSnapshot, Publisher, Story, StoryArticle, river_time

from .river import Copy, _attach_copies, RiverItem

MORE_OUTLETS = 6


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def ai_headlines() -> bool:
    """Show the language model's headline when its quality check passed (on unless HOMEPAGE_AI_HEADLINES=0)."""
    return os.environ.get("HOMEPAGE_AI_HEADLINES", "1") != "0"


@dataclass
class Outlet:
    publisher: str
    publisher_ar: str | None
    url: str
    language: str


@dataclass
class HomeStory:
    id: int
    rank: int
    headline: str
    headline_by: str  # "ijaz" (language model, checked) | "publisher" (the lead article's own headline)
    lead: RiverItem
    lead_title_ar: str | None
    more: list[Outlet]
    article_count: int
    importance: int | None
    score: dict = field(default_factory=dict)


def _articles(session: Session, story_ids: list[int]) -> dict[int, list[tuple]]:
    when = river_time().label("when")
    rows = session.execute(
        select(StoryArticle.story_id, Article.id, Article.title, Article.canonical_url, Article.language, when,
               Publisher.slug, Publisher.name, Publisher.name_ar, Publisher.authority, StoryArticle.title_ar)
        .join(Article, Article.id == StoryArticle.article_id)
        .join(Publisher, Publisher.id == Article.publisher_id)
        .where(StoryArticle.story_id.in_(story_ids))
        .order_by(river_time().desc(), Article.id.desc())
    ).all()
    out: dict[int, list[tuple]] = {}
    for r in rows:
        out.setdefault(r[0], []).append(r)
    return out


def _item(r) -> RiverItem:
    return RiverItem(id=r[1], title=r[2], url=r[3], language=r[4], time=_utc(r[5]),
                     publisher_slug=r[6], publisher=r[7], publisher_ar=r[8])


def homepage(session: Session) -> tuple[datetime | None, list[HomeStory]]:
    shot = session.scalars(select(HomepageSnapshot).order_by(HomepageSnapshot.created_at.desc()).limit(1)).first()
    if shot is None:
        return None, []
    entries = shot.stories or []
    ids = [e["story_id"] for e in entries]
    stories = {s.id: s for s in session.scalars(select(Story).where(Story.id.in_(ids)))}
    articles = _articles(session, ids)
    use_ai = ai_headlines()
    out = []
    for e in entries:
        story, rows = stories.get(e["story_id"]), articles.get(e["story_id"], [])
        if story is None or not rows:
            continue
        lead_row = next((r for r in rows if r[1] == story.lead_article_id), rows[-1])
        lead = _item(lead_row)
        if use_ai and story.headline_ok and story.headline_ar:
            headline, by = story.headline_ar, "ijaz"
        else:
            headline, by = lead.title, "publisher"
        # «المزيد:» names the other outlets, strongest first, one link each (their latest report).
        more, seen = [], {lead.publisher_slug}
        for r in sorted(rows, key=lambda r: -(r[9] or 0)):
            if r[6] in seen:
                continue
            seen.add(r[6])
            more.append(Outlet(r[7], r[8], r[3], r[4]))
        out.append(HomeStory(story.id, e["rank"], headline, by, lead, lead_row[10], more[:MORE_OUTLETS],
                             story.article_count, story.importance, {k: v for k, v in e.items() if k not in ("story_id", "rank")}))
    _attach_copies(session, [s.lead for s in out])
    return _utc(shot.created_at), out


@dataclass
class StoryPage:
    id: int
    headline: str
    headline_by: str
    importance: int | None
    event_date: str | None
    first_time: datetime
    last_time: datetime
    articles: list[tuple[RiverItem, str | None]]  # newest first, with the Arabic translation of a foreign headline
    redirect_to: int | None = None


def story_page(session: Session, story_id: int) -> StoryPage | None:
    story = session.get(Story, story_id)
    if story is None:
        return None
    seen = set()
    while story.merged_into_id is not None and story.id not in seen:  # merged stories keep their address
        seen.add(story.id)
        story = session.get(Story, story.merged_into_id)
    if story.id != story_id:
        return StoryPage(story.id, "", "", None, None, story.first_time, story.last_time, [], redirect_to=story.id)
    rows = _articles(session, [story.id]).get(story.id, [])
    items = [(_item(r), r[10]) for r in rows]
    _attach_copies(session, [i for i, _ in items])
    lead = next((i for i, _ in items if i.id == story.lead_article_id), items[-1][0] if items else None)
    if ai_headlines() and story.headline_ok and story.headline_ar:
        headline, by = story.headline_ar, "ijaz"
    else:
        headline, by = (lead.title if lead else ""), "publisher"
    return StoryPage(story.id, headline, by, story.importance, story.event_date,
                     _utc(story.first_time), _utc(story.last_time), items)


__all__ = ["Copy", "HomeStory", "Outlet", "StoryPage", "homepage", "story_page"]
