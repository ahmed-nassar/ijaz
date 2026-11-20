# News collector (first increment)

This is the first approved build increment of the Middle East news aggregator: the source registry and the feed collector (layers 1 to 3 of the agreed collection design). It fills a database with clean, deduplicated article records from publishers' own RSS and Atom feeds. Nothing reader-facing yet; the River page is the next increment.

The product spec, with every agreed decision, lives in the project's living spec document. Decisions referenced in code comments (D8, D12, D16 and so on) point to it.

## What it does

| Layer | Module | What happens |
| --- | --- | --- |
| 1. Discovery | `feeds.py`, `robots.py`, `http.py` | Checks robots.txt before every fetch, downloads each feed with conditional requests (ETag, Last-Modified), parses RSS and Atom with feedparser |
| 2. Page check | `pagecheck.py` | Loads each new article page once and reads only the `<head>`: canonical link, publish time, description, image. Stops at `</head>` or 256 KB and never reads body text |
| 3. Clean-up | `urls.py`, `text.py`, `ingest.py` | Removes tracking parameters, deduplicates on the canonical link, marks near-identical copies from other publishers as syndicated, checks the language, caps excerpts at 300 characters |

Scheduling (`scheduler.py`) polls each feed every 5 to 30 minutes depending on how often it publishes, one request at a time per site and about 20 in parallel overall. A failing feed backs off exponentially, and after 20 failures in a row it is paused and an alert is recorded.

## Rules built in

- Publishers' feeds are the only content source. Excerpts come from the feed summary, or from the page's description tag when the feed has none. Full text (for example `content:encoded`) is never stored.
- A path that robots.txt disallows is never requested. If robots.txt cannot be read (server error, network failure), the site is treated as disallowed for an hour, as RFC 9309 says.
- A canonical link is trusted only when it points to the publisher's own site.
- No source is enabled automatically. The vetting tool produces a draft list with every feed disabled; a feed is enabled only after its publisher's terms are reviewed (decision D16).

## Using it

Requires Python 3.14+ and PostgreSQL 18.

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export COLLECTOR_DATABASE_URL=postgresql+psycopg://user@localhost/collector
export COLLECTOR_USER_AGENT="SiteName/0.1 (+https://site.example/about-our-crawler)"

collector init-db
collector vet sources/candidates.yaml --out sources/draft.yaml   # find and test feeds
# review sources/draft.yaml: read each publisher's terms, set enabled: true where allowed,
# save the reviewed list as sources/sources.yaml
collector load-sources sources/sources.yaml
collector poll                     # one round, prints a line per feed
collector run                      # poll continuously
collector status --hours 24        # per-feed report, article counts, open alerts
```

## Deploying (Fly.io + Neon)

The collector runs on Fly.io as one always-on worker machine (`fly.toml`), with Postgres 18 on Neon. Each deploy first runs `collector init-db && collector load-sources sources/sources.yaml` as the release command, then starts `collector -v run`.

```
fly apps create ijaz-collector
fly secrets set -a ijaz-collector \
  DATABASE_URL='postgresql://USER:PASSWORD@ep-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require' \
  COLLECTOR_USER_AGENT='SiteName/0.1 (+https://site.example/about-our-crawler)'
fly deploy
fly scale count 1 -a ijaz-collector   # exactly one worker
fly logs -a ijaz-collector
```

Use Neon's direct connection string (host without `-pooler`).

`fly.toml` sets `COLLECTOR_ROUND_MINUTES=20`: the worker polls every feed that is due within the next 20 minutes, closes its database connections and sleeps until the next round. That lets Neon's compute scale to zero between rounds (about 55 compute hours a month at 0.25 CU; the free plan allows 100). Without the variable, `run` polls continuously and checks for due feeds every 30 seconds, which keeps the database awake.

## Tests

```
pytest                                                    # SQLite
COLLECTOR_TEST_DATABASE_URL=postgresql+psycopg://... pytest   # PostgreSQL
```

The tests use saved sample feeds and a fake web server, so they need no network. They cover the spec's rules: conditional requests, robots.txt (disallowed and unreachable), no body text stored, canonical deduplication, syndication marking, back-off and pausing.

## Not in this increment

Clustering, ranking, AI, the website, GDELT and Google News discovery, Scrapy for outlets without feeds, Bluesky, and database migrations (Alembic arrives once the schema settles; for now `init-db` creates tables).
