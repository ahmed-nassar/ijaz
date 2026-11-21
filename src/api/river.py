"""River queries: every collected article, newest first, syndicated copies folded in."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from collector.models import Article, Publisher, river_time

MAX_LIMIT = 100


@dataclass
class Copy:
    publisher: str
    publisher_ar: str | None
    url: str


@dataclass
class RiverItem:
    id: int
    title: str
    url: str
    language: str
    time: datetime
    publisher_slug: str
    publisher: str
    publisher_ar: str | None
    copies: list[Copy] = field(default_factory=list)


@dataclass
class RiverPage:
    items: list[RiverItem]
    next_cursor: str | None


class BadCursor(ValueError):
    pass


def encode_cursor(time: datetime, article_id: int) -> str:
    raw = f"{_utc(time).isoformat()}|{article_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, article_id = base64.urlsafe_b64decode(padded.encode()).decode().split("|")
        return _utc(datetime.fromisoformat(stamp)), int(article_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadCursor(str(exc)) from exc


def _utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored in UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def river_page(
    session: Session,
    *,
    limit: int = 50,
    cursor: str | None = None,
    language: str | None = None,
    country: str | None = None,
    publisher: str | None = None,
) -> RiverPage:
    limit = max(1, min(limit, MAX_LIMIT))
    when = river_time().label("when")
    query = (
        select(Article.id, Article.title, Article.canonical_url, Article.language, when,
               Publisher.slug, Publisher.name, Publisher.name_ar)
        .join(Publisher, Publisher.id == Article.publisher_id)
        .where(Article.syndicated_of_id.is_(None))
    )
    if language:
        query = query.where(Article.language == language)
    if country:
        query = query.where(Publisher.country == country.upper())
    if publisher:
        query = query.where(Publisher.slug == publisher)
    if cursor:
        before_time, before_id = decode_cursor(cursor)
        query = query.where(tuple_(river_time(), Article.id) < tuple_(before_time, before_id))
    rows = session.execute(query.order_by(river_time().desc(), Article.id.desc()).limit(limit + 1)).all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        RiverItem(id=r[0], title=r[1], url=r[2], language=r[3], time=_utc(r[4]),
                  publisher_slug=r[5], publisher=r[6], publisher_ar=r[7])
        for r in rows
    ]
    _attach_copies(session, items)
    next_cursor = encode_cursor(items[-1].time, items[-1].id) if has_more and items else None
    return RiverPage(items, next_cursor)


def _attach_copies(session: Session, items: list[RiverItem]) -> None:
    if not items:
        return
    by_id = {item.id: item for item in items}
    rows = session.execute(
        select(Article.syndicated_of_id, Article.canonical_url, Publisher.name, Publisher.name_ar)
        .join(Publisher, Publisher.id == Article.publisher_id)
        .where(Article.syndicated_of_id.in_(by_id))
        .order_by(Article.fetched_at)
    )
    for original_id, url, name, name_ar in rows:
        by_id[original_id].copies.append(Copy(name, name_ar, url))


def filter_options(session: Session) -> dict:
    """Languages, countries and publishers that have at least one article, for the filter menus."""
    languages = session.scalars(select(Article.language).group_by(Article.language).order_by(func.count().desc())).all()
    countries = session.scalars(
        select(Publisher.country).join(Article, Article.publisher_id == Publisher.id)
        .where(Publisher.country.is_not(None)).group_by(Publisher.country).order_by(Publisher.country)
    ).all()
    publishers = session.execute(
        select(Publisher.slug, Publisher.name, Publisher.name_ar)
        .join(Article, Article.publisher_id == Publisher.id)
        .group_by(Publisher.id).order_by(func.coalesce(Publisher.name_ar, Publisher.name))
    ).all()
    return {
        "languages": list(languages),
        "countries": list(countries),
        "publishers": [{"slug": s, "name": n, "name_ar": a} for s, n, a in publishers],
    }
