"""Command line: collector init-db | load-sources | poll | run | status | vet"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from .config import get_settings
from .db import init_db, make_engine, session_factory
from .http import Fetcher
from .ingest import Collector
from .models import Alert, Article, FetchLog, Publisher, Source
from .registry import load_source_list, read_source_list
from .robots import RobotsChecker
from .scheduler import poll_once, run_forever, run_rounds
from .vet import draft_source_list, vet_all, write_yaml


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collector")
    parser.add_argument("--db", help="database URL (default: COLLECTOR_DATABASE_URL)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create tables")
    p = sub.add_parser("load-sources", help="load a reviewed source list into the registry")
    p.add_argument("path")
    p = sub.add_parser("poll", help="poll due sources once (or the named ones)")
    p.add_argument("--source", action="append", help="source slug; repeatable")
    p.add_argument("--no-page-check", action="store_true")
    sub.add_parser("run", help="poll continuously")
    p = sub.add_parser("status", help="per-source report for the last N hours")
    p.add_argument("--hours", type=int, default=24)
    p = sub.add_parser("vet", help="find and test feeds for candidate publishers")
    p.add_argument("candidates")
    p.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    engine = make_engine(args.db or settings.database_url)
    sessions = session_factory(engine)

    if args.command == "init-db":
        init_db(engine)
        print("tables created")
        return 0
    if args.command == "load-sources":
        with sessions() as session:
            pubs, active = load_source_list(session, args.path)
        print(f"loaded {pubs} publishers, {active} active feeds")
        return 0
    if args.command == "status":
        return _status(sessions, args.hours)

    async def with_fetcher(fn):
        fetcher = Fetcher(settings)
        try:
            return await fn(fetcher)
        finally:
            await fetcher.aclose()

    if args.command == "poll":
        async def go(fetcher):
            collector = Collector(settings, sessions, fetcher, page_check=not args.no_page_check)
            for r in await poll_once(collector, args.source):
                print(f"source {r.source_id}: {r.outcome}, {r.items_seen} seen, {r.items_new} new")
        asyncio.run(with_fetcher(go))
        return 0
    if args.command == "run":
        if settings.round_minutes > 0:
            asyncio.run(with_fetcher(lambda f: run_rounds(
                Collector(settings, sessions, f), settings.round_minutes * 60, engine.dispose)))
        else:
            asyncio.run(with_fetcher(lambda f: run_forever(Collector(settings, sessions, f))))
        return 0
    if args.command == "vet":
        init_db(engine)  # robots cache lives in the database
        publishers = read_source_list(args.candidates)

        async def go(fetcher):
            return await vet_all(settings, fetcher, RobotsChecker(fetcher, sessions, settings), publishers)

        reports = asyncio.run(with_fetcher(go))
        write_yaml(draft_source_list(publishers, reports), args.out)
        ok = sum(1 for r in reports if any(f.status == "ok" for f in r.feeds))
        print(f"{ok} of {len(reports)} publishers have at least one fresh, valid feed; draft written to {args.out}")
        return 0
    return 1


def _status(sessions, hours: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with sessions() as s:
        rows = s.execute(
            select(
                Source.slug,
                Source.active,
                Source.paused,
                Source.consecutive_failures,
                Source.last_success_at,
                func.count(FetchLog.id),
                func.sum(case((FetchLog.outcome.in_(("ok", "not_modified")), 1), else_=0)),
                func.sum(case((FetchLog.outcome == "not_modified", 1), else_=0)),
                func.coalesce(func.sum(FetchLog.items_new), 0),
            )
            .join(FetchLog, (FetchLog.source_id == Source.id) & (FetchLog.started_at >= since), isouter=True)
            .group_by(Source.id)
            .order_by(Source.slug)
        ).all()
        articles = s.scalar(select(func.count(Article.id)).where(Article.fetched_at >= since))
        syndicated = s.scalar(select(func.count(Article.id)).where(Article.fetched_at >= since, Article.syndicated_of_id.is_not(None)))
        open_alerts = s.scalars(select(Alert).where(Alert.resolved.is_(False))).all()
        publishers = s.scalar(select(func.count(Publisher.id)))

    print(f"{'source':32} {'state':8} {'polls':>5} {'ok':>4} {'304':>4} {'new':>5}  last success")
    active = healthy = 0
    for slug, is_active, paused, failures, last_ok, polls, ok, not_mod, new in rows:
        state = "paused" if paused else ("active" if is_active else "off")
        if is_active and not paused:
            active += 1
            if polls and ok == polls:
                healthy += 1
        last = last_ok.strftime("%Y-%m-%d %H:%M") if last_ok else "never"
        print(f"{slug:32} {state:8} {polls:>5} {ok or 0:>4} {not_mod or 0:>4} {new:>5}  {last}")
    print(f"\n{publishers} publishers; {active} active feeds; {healthy} with every poll successful in the last {hours} h")
    print(f"{articles} articles stored in the last {hours} h, {syndicated} marked as syndicated copies")
    for alert in open_alerts:
        print(f"ALERT {alert.created_at:%Y-%m-%d %H:%M} {alert.kind}: {alert.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
