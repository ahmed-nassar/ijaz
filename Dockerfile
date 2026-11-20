# Explicit image so Railway installs the dependencies from pyproject.toml
# instead of relying on builder auto-detection.
FROM python:3.14-slim

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

# Railway's startCommand (railway.json) overrides this; kept for local `docker run`.
CMD ["collector", "-v", "run"]
