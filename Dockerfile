# One image for the whole Fly app `ijaz` (see fly.toml), run as two process groups:
#   web        the River page (Next.js) and its read API (FastAPI)
#   collector  the feed collector worker
# Later the story worker joins as a third group, from the same image.

# 1. Build the Next.js app into a self-contained server.
FROM node:24-trixie-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
RUN npm run build

# 2. Runtime: Python 3.14 for the API and the collector, plus the Node 24 binary for the web server.
FROM python:3.14-slim-trixie
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=8080 \
    HOSTNAME=0.0.0.0 \
    API_URL=http://127.0.0.1:8000
COPY --from=web /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
# Python dependencies first so code changes do not re-download them.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY sources ./sources
COPY --from=web /web/.next/standalone ./web
COPY --from=web /web/.next/static ./web/.next/static
COPY --from=web /web/public ./web/public
COPY deploy/start-web.sh ./start-web.sh

RUN useradd --system --no-create-home site
USER site
EXPOSE 8080
# Each process group sets its own command in fly.toml; this default runs the website.
CMD ["sh", "./start-web.sh"]
