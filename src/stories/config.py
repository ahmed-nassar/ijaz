"""Settings for the story work, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


@dataclass(frozen=True)
class StorySettings:
    # The OpenRouter key, set as a Fly secret on the ijaz app.
    api_key: str = field(default_factory=lambda: os.environ.get("IJAZ_OPENROUTER_KEY", ""))
    base_url: str = field(default_factory=lambda: os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    embedding_model: str = field(default_factory=lambda: os.environ.get("STORIES_EMBEDDING_MODEL", "openai/text-embedding-3-small"))
    llm_model: str = field(default_factory=lambda: os.environ.get("STORIES_LLM_MODEL", "openai/gpt-6-luna"))
    # Grouping score = word_weight x word overlap + (1 - word_weight) x meaning similarity.
    word_weight: float = field(default_factory=lambda: _float("STORIES_WORD_WEIGHT", 0.5))
    threshold: float = field(default_factory=lambda: _float("STORIES_THRESHOLD", 0.45))
    # Articles further apart than this are never put in the same story.
    window_hours: float = 48.0
    # Sent to OpenRouter as the referring site, as its API docs ask.
    site_url: str = "https://ijaz.fly.dev"
    site_name: str = "ijaz"


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


@dataclass(frozen=True)
class LiveSettings:
    """The story step that runs on live articles after each collector round.

    The defaults are the best setup measured so far on the answer key (stories-compare,
    21 November 2026), not the agreed starting design: Qwen3 embeddings, word overlap
    counted only between articles in the same language, and the language model judging
    the uncertain band. Each can be changed with its environment variable.
    """

    enabled: bool = field(default_factory=lambda: os.environ.get("STORIES_LIVE", "1") != "0")
    embedding_model: str = field(default_factory=lambda: os.environ.get("STORIES_LIVE_EMBEDDING_MODEL", "qwen/qwen3-embedding-8b"))
    word_weight_same: float = field(default_factory=lambda: _float("STORIES_LIVE_WORD_WEIGHT", 0.5))
    word_weight_cross: float = field(default_factory=lambda: _float("STORIES_LIVE_WORD_WEIGHT_CROSS", 0.0))
    # At or above: joins on the score alone. Between judge_low and this: the judge decides.
    threshold: float = field(default_factory=lambda: _float("STORIES_LIVE_THRESHOLD", 0.625))
    judge_low: float = field(default_factory=lambda: _float("STORIES_LIVE_JUDGE_LOW", 0.375))  # 0 turns the judge off
    window_hours: float = 48.0
    max_new_per_run: int = field(default_factory=lambda: _int("STORIES_LIVE_MAX_NEW", 400))
    enrich_per_run: int = field(default_factory=lambda: _int("STORIES_LIVE_ENRICH", 40))
    # Ranking (spec section 5). One profile for now: the homepage.
    half_life_hours: float = field(default_factory=lambda: _float("STORIES_HALF_LIFE_HOURS", 12.0))
    velocity_beta: float = 0.15
    homepage_size: int = 30
