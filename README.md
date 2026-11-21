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

Everything runs as one Fly.io app, `ijaz` (`fly.toml`, `Dockerfile`), with Postgres 18 on Neon. One image holds both parts and runs as two process groups with two machines each (shared-cpu-1x, 512 MB):

| Group | Runs | Machine |
| --- | --- | --- |
| `web` | The River page at https://ijaz.fly.dev (Next.js on port 8080) and its read API (FastAPI on 127.0.0.1:8000, not reachable from outside). `/` redirects to `/river` until the homepage exists | Stops when the site is idle and starts on the next visit |
| `worker` | `collector -v run`: polls feeds, then runs the story step. One worker holds a lease in the database and does each round; the other stands by and takes over within about 40 minutes if the first stops | Always on, no web traffic |

Each deploy first runs `collector init-db && collector load-sources sources/sources.yaml` as the release command, so new tables and indexes exist before the new code starts.

```
fly secrets set -a ijaz \
  DATABASE_URL='postgresql://USER:PASSWORD@ep-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require' \
  COLLECTOR_USER_AGENT='SiteName/0.1 (+https://site.example/about-our-crawler)'
fly deploy --ha=false
fly scale count web=2 worker=2 -a ijaz
fly logs -a ijaz
```

Use Neon's direct connection string (host without `-pooler`).

After the first manual deploy, merging a pull request into `main` deploys automatically (`.github/workflows/deploy.yml`). It needs one repository secret, `FLY_API_TOKEN`: run `fly tokens create deploy -a ijaz` and add the output under the repository's Settings, Secrets and variables, Actions. Two workers never poll twice: `collector status` shows which one holds the lease.

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

The header and page titles show the site name «إيجاز»; `SITE_NAME` overrides it.

Browser checks (need Playwright with Chromium; not a project dependency):

```
node web/scripts/check-page.mjs http://localhost:3000 out/    # no scripts, 375 px width, pager, filters; saves screenshots
node web/scripts/measure-lcp.mjs http://localhost:3000        # LCP on slow 4G with a 4x slower CPU
```

## Stories, part 1: measuring the grouping

`src/stories` holds the grouping and language model steps from spec section 15, measured against a hand-made answer key before anything runs on the live site. It writes nothing to the database.

- Grouping: half word overlap (TF-IDF over the headline and excerpt, with Arabic spelling normalised: alef, ya, ta marbuta and hamza forms, diacritics, Arabic-Indic digits, and a leading «ال», «و», «ب», «ل» or «ف» removed) and half meaning similarity from `openai/text-embedding-3-small`. Articles are grouped one at a time in publishing order; an article joins the best matching story within 48 hours if the score reaches the threshold.
- Language model: `google/gemini-3.1-flash-lite` writes an Arabic headline, rates importance from 1 to 5, translates non-Arabic headlines and reads the event date, in one call. A second call checks that headline against the reports.
- Both go through OpenRouter with the `IJAZ_OPENROUTER_KEY` secret. Only headlines and short excerpts are sent. Model names, the word weight and the threshold are environment settings (`STORIES_EMBEDDING_MODEL`, `STORIES_LLM_MODEL`, `STORIES_WORD_WEIGHT`, `STORIES_THRESHOLD`).
- `eval/stories_gold.json` is the answer key: 471 articles from 21 Nov 2026, 03:00 to 11:38 UTC, with 64 stories of two or more reports. It holds article ids only; the articles are read from the production database.

Run the measurement on Fly, where the key and the articles are. `fly console` starts a temporary machine from the current image, with the app's secrets, and removes it afterwards, so a long run neither competes with the worker for memory nor dies with it:

```
fly console -a ijaz --vm-memory 1024 -C "collector stories-eval" > stories-report.txt
```

It prints precision and recall at each threshold (targets: 95% and 85%), the best results for words alone and meaning alone, the pairs it got wrong, the language model's output for the 20 largest stories, and the cost. `--json` prints everything as JSON; `--enrich N` changes how many stories go to the language model.

`collector stories-eval --compare` (same `fly console` command) prints each result as soon as it is ready, with progress in the terminal, so an interrupted run keeps what finished. It measures the agreed design next to candidate fixes on the same answer key: `text-embedding-3-large` and `qwen/qwen3-embedding-8b`, no word weight for pairs in different languages, and a judge (Gemini 3.1 Flash Lite with two band widths, and Claude Haiku 5.5) that decides matches in the uncertain band. Each row shows precision, recall, recall across languages and cost per 1,000 articles. The judge runs and a headline test compare six language models: `google/gemini-3.1-flash-lite` and `anthropic/claude-haiku-5.5` as baselines, `deepseek/deepseek-v4.1-flash-1017`, `openai/gpt-6-luna`, `google/gemini-4-hydrogen` (which cannot switch reasoning off, so it runs at low effort) and `meta/muse-ember`. For headlines, each model writes the 12 largest answer-key stories and Haiku 5.5 checks all of them, so pass rates compare; the output shows every model's headline side by side, with cost per story and per 1,000 articles.

## Stories, part 2: the homepage (preview)

After each collector round (every 20 minutes, so Neon can sleep in between), the collector runs the story step in `src/stories/live.py`:

1. **Group.** New first reports from the last 48 hours are placed into stories one at a time, as in the measurement. Syndicated copies stay with their original.
2. **Write.** Stories with two or more reports go to the language model for a headline, importance, translations and event date, then the quality check. A story goes again each time it has grown by half. At most `STORIES_LIVE_ENRICH` (40) stories per round.
3. **Rank.** The spec section 5 score: independent coverage by owner group, E from importance, a 12-hour recency half-life, velocity, and the caps (4 lead stories per publisher in the top 30, 8 of the top 20 per country, single-source stories below the top 10). G, C and M are 1 until page profiles and editor tools exist. The lead article is the one from the publisher with the highest authority.
4. **Snapshot.** The ranked 30 with each story's score breakdown go into `homepage_snapshot` (kept 7 days).

The live step's settings are the best measured so far, not the agreed starting design, and each is an environment variable:

| Setting | Default | Variable |
| --- | --- | --- |
| Embedding model | `qwen/qwen3-embedding-8b` | `STORIES_LIVE_EMBEDDING_MODEL` |
| Word weight, same language | 0.5 | `STORIES_LIVE_WORD_WEIGHT` |
| Word weight, different languages | 0 | `STORIES_LIVE_WORD_WEIGHT_CROSS` |
| Join on the score alone at | 0.625 | `STORIES_LIVE_THRESHOLD` |
| Judge decides from | 0.35 (0 turns the judge off) | `STORIES_LIVE_JUDGE_LOW` |
| Headlines, importance, judge | `google/gemini-3.1-flash-lite` | `STORIES_LLM_MODEL` |
| Recency half-life | 12 hours | `STORIES_HALF_LIFE_HOURS` |

`STORIES_LIVE=0` turns the step off. `collector stories-run` runs it once by hand.

Pages: `/preview` is the homepage (hidden from search with `noindex` until it moves to `/`), and `/story/<number>` lists every report in a story, newest first. A merged story's address redirects. Each homepage story shows the headline written by the language model when its quality check passed, with the publisher's own headline beneath it, or the publisher's headline alone otherwise; `HOMEPAGE_AI_HEADLINES=0` on the web process shows only publisher headlines. The API behind them is `/api/home` and `/api/story/<number>`.

## Not in this increment

The sidebar modules, category and country pages, the password-protected score page, editor tools, search, GDELT and Google News discovery, Scrapy for outlets without feeds, Bluesky, and database migrations (Alembic arrives once the schema settles; for now `init-db` creates tables).
