"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _database_url() -> str:
    """COLLECTOR_DATABASE_URL, else DATABASE_URL (what Fly.io secrets and Heroku-style hosts use).

    Hosts hand out ``postgres://`` or ``postgresql://`` URLs; SQLAlchemy reads those as
    psycopg2, which is not installed, so they are switched to the psycopg (v3) driver.
    """
    url = os.environ.get("COLLECTOR_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        return "postgresql+psycopg://localhost/collector"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=_database_url)
    # Honest identification: publishers must be able to see who we are and reach us.
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "COLLECTOR_USER_AGENT",
            "NewsCollector/0.1 (+https://example.invalid/about-our-crawler)",
        )
    )
    request_timeout: float = 15.0
    global_concurrency: int = field(default_factory=lambda: _int("COLLECTOR_CONCURRENCY", 20))
    # Polling interval bounds, in seconds (section 13: every 5 to 30 minutes).
    min_interval: int = 5 * 60
    max_interval: int = 30 * 60
    max_backoff: int = 6 * 60 * 60
    # When set, `run` polls in rounds this many minutes apart and keeps no database
    # connection open in between, so a serverless database (Neon) can sleep. 0 means
    # poll continuously, checking for due feeds every 30 seconds.
    round_minutes: int = field(default_factory=lambda: _int("COLLECTOR_ROUND_MINUTES", 0))
    pause_after_failures: int = 20
    # Page check reads at most this many bytes, and stops at </head>.
    page_check_max_bytes: int = 256 * 1024
    # Excerpts are capped when stored, so full text never reaches the database.
    excerpt_max_chars: int = 300
    robots_ttl: int = 24 * 60 * 60
    # Near-duplicate (syndication) window and threshold.
    syndication_window_hours: int = 48
    syndication_max_distance: int = 3


def get_settings() -> Settings:
    return Settings()
