"""Source registry: load the reviewed source list (YAML) into the database."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Publisher, Source

PUBLISHER_FIELDS = ("name", "name_ar", "website", "country", "owner_group", "state_affiliated", "authority", "paywall")


class RegistryError(ValueError):
    pass


def read_source_list(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    publishers = data.get("publishers")
    if not isinstance(publishers, list):
        raise RegistryError("source list must have a top-level 'publishers' list")
    seen_pub, seen_feed = set(), set()
    for pub in publishers:
        for key in ("slug", "name", "website"):
            if not pub.get(key):
                raise RegistryError(f"publisher {pub!r} is missing '{key}'")
        if pub["slug"] in seen_pub:
            raise RegistryError(f"duplicate publisher slug {pub['slug']}")
        seen_pub.add(pub["slug"])
        for feed in pub.get("feeds") or []:
            for key in ("slug", "url", "language"):
                if not feed.get(key):
                    raise RegistryError(f"feed in {pub['slug']} is missing '{key}'")
            if feed["slug"] in seen_feed:
                raise RegistryError(f"duplicate feed slug {feed['slug']}")
            seen_feed.add(feed["slug"])
    return publishers


def load_source_list(session: Session, path: str | Path) -> tuple[int, int]:
    """Insert or update publishers and feeds. Only feeds marked ``enabled: true`` are active.

    Returns (publishers, active feeds).
    """
    publishers = read_source_list(path)
    active = 0
    for pub in publishers:
        row = session.scalar(select(Publisher).where(Publisher.slug == pub["slug"]))
        if row is None:
            row = Publisher(slug=pub["slug"])
            session.add(row)
        for field in PUBLISHER_FIELDS:
            if field in pub:
                setattr(row, field, pub[field])
        session.flush()
        for feed in pub.get("feeds") or []:
            src = session.scalar(select(Source).where(Source.slug == feed["slug"]))
            if src is None:
                src = Source(slug=feed["slug"], next_poll_at=datetime.now(timezone.utc))
                session.add(src)
            src.publisher_id = row.id
            src.feed_url = feed["url"]
            src.feed_type = feed.get("type", "rss")
            src.language = feed["language"]
            src.countries = feed.get("countries", [])
            src.categories = feed.get("categories", [])
            src.licence_note = feed.get("licence_note")
            src.active = bool(feed.get("enabled", False))
            if src.active:
                active += 1
    session.commit()
    return len(publishers), active
