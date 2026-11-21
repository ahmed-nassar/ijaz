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
    llm_model: str = field(default_factory=lambda: os.environ.get("STORIES_LLM_MODEL", "google/gemini-3.1-flash-lite"))
    # Grouping score = word_weight x word overlap + (1 - word_weight) x meaning similarity.
    word_weight: float = field(default_factory=lambda: _float("STORIES_WORD_WEIGHT", 0.5))
    threshold: float = field(default_factory=lambda: _float("STORIES_THRESHOLD", 0.45))
    # Articles further apart than this are never put in the same story.
    window_hours: float = 48.0
    # Sent to OpenRouter as the referring site, as its API docs ask.
    site_url: str = "https://ijaz.fly.dev"
    site_name: str = "ijaz"
