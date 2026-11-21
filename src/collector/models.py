"""Database tables for the source registry and collected articles (spec section 4).

Types are kept portable so the test suite can run on SQLite; production uses PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    case,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Publisher(Base):
    """The organization behind one or more feeds. Ranking caps apply here, not per feed."""

    __tablename__ = "publisher"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str | None] = mapped_column(String(200))
    website: Mapped[str] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(2))  # ISO 3166-1 alpha-2
    owner_group: Mapped[str | None] = mapped_column(String(100))
    state_affiliated: Mapped[bool] = mapped_column(Boolean, default=False)
    authority: Mapped[float] = mapped_column(default=0.5)  # 0..1, tuned later
    paywall: Mapped[str] = mapped_column(String(20), default="none")  # none | metered | hard

    sources: Mapped[list["Source"]] = relationship(back_populates="publisher")


class Source(Base):
    """One feed endpoint."""

    __tablename__ = "source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publisher_id: Mapped[int] = mapped_column(ForeignKey("publisher.id"))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    feed_url: Mapped[str] = mapped_column(String(1000), unique=True)
    feed_type: Mapped[str] = mapped_column(String(20), default="rss")  # rss | atom
    language: Mapped[str] = mapped_column(String(8))  # BCP 47, e.g. ar, en, tr
    countries: Mapped[list] = mapped_column(JSON, default=list)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)  # set after repeated failures
    licence_note: Mapped[str | None] = mapped_column(Text)
    poll_interval: Mapped[int] = mapped_column(Integer, default=15 * 60)  # seconds
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(String(500))
    last_modified: Mapped[str | None] = mapped_column(String(100))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_new_item_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    publisher: Mapped[Publisher] = relationship(back_populates="sources")


class FetchLog(Base):
    """One row per poll of a feed."""

    __tablename__ = "fetch_log"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    # ok | not_modified | http_error | network_error | parse_error | robots_disallowed | robots_unreachable
    outcome: Mapped[str] = mapped_column(String(30))
    http_status: Mapped[int | None] = mapped_column(Integer)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class Article(Base):
    """One published piece. Never holds article body text: excerpt only, capped."""

    __tablename__ = "article"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"), index=True)
    publisher_id: Mapped[int] = mapped_column(ForeignKey("publisher.id"), index=True)
    feed_url: Mapped[str] = mapped_column(String(2000))  # link as it appeared in the feed
    feed_url_hash: Mapped[str] = mapped_column(String(64), index=True)
    canonical_url: Mapped[str] = mapped_column(String(2000))
    canonical_url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    guid: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(300))
    excerpt: Mapped[str | None] = mapped_column(Text)
    excerpt_origin: Mapped[str | None] = mapped_column(String(10))  # feed | page
    image_url: Mapped[str | None] = mapped_column(String(2000))
    language: Mapped[str] = mapped_column(String(8))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at_origin: Mapped[str | None] = mapped_column(String(10))  # feed | page
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # none | ok | disallowed | failed | skipped
    page_check: Mapped[str] = mapped_column(String(12), default="none")
    fingerprint: Mapped[int] = mapped_column(BigInteger)  # 64-bit SimHash, signed
    syndicated_of_id: Mapped[int | None] = mapped_column(ForeignKey("article.id"))

    __table_args__ = (Index("ix_article_fetched_fingerprint", "fetched_at", "fingerprint"),)


class RobotsCache(Base):
    """Cached robots.txt per origin (scheme + host)."""

    __tablename__ = "robots_cache"

    origin: Mapped[str] = mapped_column(String(300), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # allow_all | disallow_all | rules
    mode: Mapped[str] = mapped_column(String(15))
    body: Mapped[str | None] = mapped_column(Text)


class Alert(Base):
    """Something the on-call person (D17) should look at."""

    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"))
    message: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class Story(Base):
    """A group of articles about one event (spec section 15). Built by the story step."""

    __tablename__ = "story"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # river time of its first article
    last_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # and of its latest
    article_count: Mapped[int] = mapped_column(Integer, default=1)
    lead_article_id: Mapped[int | None] = mapped_column(ForeignKey("article.id"))
    # Written by the language model; shown only when the quality check passed.
    headline_ar: Mapped[str | None] = mapped_column(Text)
    headline_ok: Mapped[bool | None] = mapped_column(Boolean)
    headline_issues: Mapped[list | None] = mapped_column(JSON)
    importance: Mapped[int | None] = mapped_column(Integer)  # 1..5
    importance_reason: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[str | None] = mapped_column(String(10))
    enriched_count: Mapped[int] = mapped_column(Integer, default=0)  # article_count at the last model call
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when an editor or a later step merges this story into another; its address redirects there.
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("story.id"))


class StoryArticle(Base):
    """Which story an article belongs to. Only first reports; syndicated copies follow their original."""

    __tablename__ = "story_article"

    article_id: Mapped[int] = mapped_column(ForeignKey("article.id"), primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    score: Mapped[float | None] = mapped_column()  # grouping score when it joined; null for a story's first article
    how: Mapped[str] = mapped_column(String(10), default="new")  # new | score | judge
    title_ar: Mapped[str | None] = mapped_column(Text)  # Arabic translation of a foreign-language headline


class ArticleEmbedding(Base):
    """Meaning vector of an article's headline and excerpt, as float32 bytes."""

    __tablename__ = "article_embedding"

    article_id: Mapped[int] = mapped_column(ForeignKey("article.id"), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class HomepageSnapshot(Base):
    """One computed homepage: the ranked stories with each one's score breakdown (spec section 5)."""

    __tablename__ = "homepage_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    settings: Mapped[dict] = mapped_column(JSON)
    stories: Mapped[list] = mapped_column(JSON)  # [{story_id, rank, score, parts: {...}}]


def river_time():
    """When an article is shown as published on the River.

    The publisher's own time, unless it is missing or later than when we collected the
    article (some feeds carry future or wrong dates); then the collection time.
    """
    return case(
        (
            (Article.published_at.is_not(None)) & (Article.published_at < Article.fetched_at),
            Article.published_at,
        ),
        else_=Article.fetched_at,
    )


# Keyset pagination for the River: newest first, first reports only (copies fold under them).
# The expression is written out in parentheses because PostgreSQL requires them in an index
# and SQLAlchemy drops a Grouping. Keep it identical to river_time() so the planner uses it.
Index(
    "ix_article_river",
    text("(CASE WHEN (published_at IS NOT NULL AND published_at < fetched_at) THEN published_at ELSE fetched_at END)"),
    Article.id,
    postgresql_where=Article.syndicated_of_id.is_(None),
    sqlite_where=Article.syndicated_of_id.is_(None),
)
Index("ix_article_syndicated_of", Article.syndicated_of_id)
