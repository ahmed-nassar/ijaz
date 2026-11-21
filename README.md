# Middle East news aggregator

Two approved build increments so far:

1. **The collector** (`src/collector`): the source registry and feed collector (layers 1 to 3 of the agreed collection design). It fills a database with clean, deduplicated article records from publishers' own RSS and Atom feeds.
2. **The River** (`src/api` and `web/`): كل الأخبار, a server-rendered Arabic page listing every collected article newest first, with no clustering (spec section 14).

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

Everything runs as one Fly.io app, `ijaz` (`fly.toml`, `Dockerfile`), with Postgres 18 on Neon. One image holds both parts and runs as two process groups with one machine each:

| Group | Runs | Machine |
| --- | --- | --- |
| `web` | The River page at https://ijaz.fly.dev (Next.js on port 8080) and its read API (FastAPI on 127.0.0.1:8000, not reachable from outside). `/` redirects to `/river` until the homepage exists | Stops when the site is idle and starts on the next visit |
| `collector` | `collector -v run` | Always on, no web traffic |

Each deploy first runs `collector init-db && collector load-sources sources/sources.yaml` as the release command, so new tables and indexes exist before the new code starts.

```
fly secrets set -a ijaz \
  DATABASE_URL='postgresql://USER:PASSWORD@ep-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require' \
  COLLECTOR_USER_AGENT='SiteName/0.1 (+https://site.example/about-our-crawler)'
fly deploy --ha=false
fly scale count web=1 collector=1 -a ijaz
fly logs -a ijaz
```

Use Neon's direct connection string (host without `-pooler`).

`fly.toml` sets `COLLECTOR_ROUND_MINUTES=20`: the collector polls every feed that is due within the next 20 minutes, closes its database connections and sleeps until the next round. That lets Neon's compute scale to zero between rounds (about 55 compute hours a month at 0.25 CU; the free plan allows 100). Without the variable, `run` polls continuously and checks for due feeds every 30 seconds, which keeps the database awake.

## Tests

```
pytest                                                    # SQLite
COLLECTOR_TEST_DATABASE_URL=postgresql+psycopg://... pytest   # PostgreSQL
```

The tests use saved sample feeds and a fake web server, so they need no network. They cover the spec's rules: conditional requests, robots.txt (disallowed and unreachable), no body text stored, canonical deduplication, syndication marking, back-off and pausing.

## The River page

A small read API in Python (`src/api`, FastAPI) serves articles from the collector's database; a Next.js app (`web/`) renders them as an Arabic, right-to-left page at `/river`.

- Newest first, by publish time, or by collection time when the publish time is missing or later than collection. Grouped under hour headings, 50 per page, with an «أقدم» link (keyset pagination, so deep pages stay fast).
- Times in UTC+3 (Riyadh and Istanbul) with Western digits. Headlines link to the publisher's page in a new tab; English and Turkish headlines carry an «EN» or «TR» label. Syndicated copies fold under the first report as «+3 نسخ».
- Filters for language, country and publisher, as a plain form.
- **No JavaScript is sent to the browser.** An App Router page always ships the React runtime, so the page is rendered with `renderToStaticMarkup` in `web/lib/render-river.tsx` and served from `pages/api/river.ts` through a rewrite. Its Content-Security-Policy forbids scripts.
- IBM Plex Sans Arabic is self-hosted (Arabic, Latin and Turkish subsets, 400 and 600 weights; SIL Open Font License). Latin names and figures inside Arabic headlines are wrapped in `<bdi>`, and the CSS uses logical properties only.

Running it locally, with made-up sample articles:

```
export COLLECTOR_DATABASE_URL=sqlite:///dev.db       # or PostgreSQL
python -m api.sample_data --count 150                # development only; invented publishers
uvicorn api.app:app --port 8000

cd web                                               # Node 22 or later (24 LTS for deployment)
npm install
npm run build && API_URL=http://127.0.0.1:8000 npm start   # http://localhost:3000/river
npm test                                             # time, grouping, plural and bidi helpers
```

`SITE_NAME` sets the header name; until branding is decided it shows the placeholder «اسم الموقع».

Browser checks (need Playwright with Chromium; not a project dependency):

```
node web/scripts/check-page.mjs http://localhost:3000 out/    # no scripts, 375 px width, pager, filters; saves screenshots
node web/scripts/measure-lcp.mjs http://localhost:3000        # LCP on slow 4G with a 4x slower CPU
```

## Not in this increment

Clustering, ranking, AI, the homepage, search, GDELT and Google News discovery, Scrapy for outlets without feeds, Bluesky, and database migrations (Alembic arrives once the schema settles; for now `init-db` creates tables).
