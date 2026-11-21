"""Command line: collector init-db | load-sources | poll | run | status | vet | export-articles | stories-eval"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
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
    p = sub.add_parser("export-articles", help="print collected articles as JSON lines (headline, excerpt, link; no body text)")
    p.add_argument("--days", type=int, default=14)
    p = sub.add_parser("stories-eval", help="measure story grouping against the answer key (needs IJAZ_OPENROUTER_KEY)")
    p.add_argument("gold", nargs="?", default=os.environ.get("STORIES_GOLD", "eval/stories_gold.json"))
    p.add_argument("--enrich", type=int, default=20, help="run the language model on this many of the largest stories")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    p.add_argument("--compare", action="store_true", help="compare the agreed design with stronger embedding models, language weights and a Gemini judge")
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
    if args.command == "export-articles":
        return _export_articles(sessions, args.days)
    if args.command == "stories-eval":
        from stories.evaluate import compare, render, render_compare, run

        if args.compare:
            report = compare(sessions, args.gold)
            print(json.dumps(report, ensure_ascii=False, indent=1, default=str) if args.json else render_compare(report))
            return 0
        report = run(sessions, args.gold, enrich_count=args.enrich)
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str) if args.json else render(report))
        return 0

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


def _export_articles(sessions, days: int) -> int:
    """Articles fetched in the last ``days`` days, oldest first, one JSON object per line.

    Used to build the clustering test set (spec section 15). It holds only what the
    database holds: headline, capped excerpt, link and metadata, never article text.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = (
        select(Article, Publisher.slug, Publisher.country, Publisher.owner_group)
        .join(Publisher, Publisher.id == Article.publisher_id)
        .where(Article.fetched_at >= since)
        .order_by(Article.fetched_at, Article.id)
    )
    count = 0
    with sessions() as session:
        for article, slug, country, owner in session.execute(query).yield_per(1000):
            row = {
                "id": article.id,
                "publisher": slug,
                "country": country,
                "owner_group": owner,
                "language": article.language,
                "title": article.title,
                "excerpt": article.excerpt,
                "url": article.canonical_url,
                "published_at": article.published_at.isoformat() if article.published_at else None,
                "fetched_at": article.fetched_at.isoformat(),
                "syndicated_of_id": article.syndicated_of_id,
            }
            sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    print(f"exported {count} articles", file=sys.stderr)
    return 0
