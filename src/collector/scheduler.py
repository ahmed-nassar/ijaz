"""Run due polls continuously, within the global and per-host limits."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .ingest import Collector, PollReport
from .models import Source

log = logging.getLogger("collector")


def due_sources(sessions: sessionmaker[Session], only: list[str] | None = None) -> list[int]:
    with sessions() as session:
        query = select(Source.id).where(Source.active.is_(True), Source.paused.is_(False))
        if only:
            query = query.where(Source.slug.in_(only))
        else:
            query = query.where(
                or_(Source.next_poll_at.is_(None), Source.next_poll_at <= datetime.now(timezone.utc))
            )
        return list(session.scalars(query.order_by(Source.next_poll_at)))


async def poll_once(collector: Collector, only: list[str] | None = None) -> list[PollReport]:
    ids = due_sources(collector.sessions, only)
    return await asyncio.gather(*(collector.poll(i) for i in ids))


async def run_forever(collector: Collector, tick_seconds: int = 30) -> None:
    running: dict[int, asyncio.Task] = {}
    while True:
        for source_id in due_sources(collector.sessions):
            if source_id not in running:
                running[source_id] = asyncio.create_task(collector.poll(source_id))
        await asyncio.sleep(tick_seconds)
        for source_id, task in list(running.items()):
            if task.done():
                running.pop(source_id)
                if task.exception():
                    log.error("poll of source %s crashed: %r", source_id, task.exception())
