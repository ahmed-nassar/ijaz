"""Run due polls continuously, within the global and per-host limits."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .ingest import Collector, PollReport
from .models import Source

log = logging.getLogger("collector")


def due_sources(
    sessions: sessionmaker[Session], only: list[str] | None = None, horizon_seconds: int = 0
) -> list[int]:
    """Active, unpaused sources due now, or within ``horizon_seconds`` from now."""
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=horizon_seconds)
    with sessions() as session:
        query = select(Source.id).where(Source.active.is_(True), Source.paused.is_(False))
        if only:
            query = query.where(Source.slug.in_(only))
        else:
            query = query.where(
                or_(Source.next_poll_at.is_(None), Source.next_poll_at <= cutoff)
            )
        return list(session.scalars(query.order_by(Source.next_poll_at)))


async def poll_once(
    collector: Collector, only: list[str] | None = None, horizon_seconds: int = 0
) -> list[PollReport]:
    ids = due_sources(collector.sessions, only, horizon_seconds)
    return await asyncio.gather(*(collector.poll(i) for i in ids))


async def run_rounds(
    collector: Collector,
    round_seconds: int,
    release_connections: Callable[[], None] = lambda: None,
    rounds: int | None = None,
    after_round: Callable[[], None] | None = None,
    claim: Callable[[], bool] | None = None,
    standby_can_stop: Callable[[], bool] | None = None,
) -> None:
    """Poll in rounds, then leave the database alone until the next round.

    Each round polls every source due before the next round starts, waits for all of
    them, and closes the pooled connections. Between rounds nothing touches the
    database, so a serverless Postgres such as Neon can scale its compute to zero.

    ``after_round`` (the story step) runs in a worker thread once the polls are done,
    before the connections are closed.

    ``claim`` (the worker lease) is asked at the start of each round; when it says no,
    another worker is doing the work and this one stands by until the next round.

    ``standby_can_stop`` is asked on each standby round; when it says yes (the other
    worker is alive and renewing the lease), this function returns so the process can
    exit and its machine stay stopped.
    """
    done = 0
    while rounds is None or done < rounds:
        started = time.monotonic()
        try:
            leading = True
            if claim is not None:
                leading = claim()
            if not leading:
                if standby_can_stop is not None and standby_can_stop():
                    log.info("standby: the other worker is renewing its lease; stopping")
                    return
                log.info("standby: another worker holds this round")
            else:
                reports = await poll_once(collector, horizon_seconds=round_seconds)
                log.info("round finished: %d feeds polled", len(reports))
                if after_round is not None:
                    try:
                        await asyncio.to_thread(after_round)
                    except Exception:
                        log.exception("after-round step failed")
        except Exception:
            log.exception("round failed")
        finally:
            release_connections()
        done += 1
        if rounds is None or done < rounds:
            await asyncio.sleep(max(0.0, round_seconds - (time.monotonic() - started)))


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
