# Python 3.14 on Debian 13 (trixie). Fly.io builds this image (see fly.toml).
FROM python:3.14-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so code changes do not re-download them.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY sources ./sources

# The worker's start command on Fly.io and for local `docker run`.
CMD ["collector", "-v", "run"]
