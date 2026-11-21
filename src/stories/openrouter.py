"""A small OpenRouter client: embeddings and JSON chat completions, with cost tracking."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx
import numpy as np

from .config import StorySettings


class OpenRouterError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cost_reported: bool = True  # false once any response came without a cost figure

    def add(self, usage: dict | None) -> None:
        self.calls += 1
        usage = usage or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        if usage.get("cost") is None:
            self.cost_reported = False
        else:
            self.cost_usd += float(usage["cost"])


@dataclass
class OpenRouter:
    settings: StorySettings
    client: httpx.Client | None = None
    embedding_usage: Usage = field(default_factory=Usage)
    llm_usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        if not self.settings.api_key:
            raise OpenRouterError("IJAZ_OPENROUTER_KEY is not set")
        if self.client is None:
            self.client = httpx.Client(timeout=60.0)

    def _post(self, path: str, body: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "HTTP-Referer": self.settings.site_url,
            "X-Title": self.settings.site_name,
        }
        for attempt in range(4):
            r = self.client.post(f"{self.settings.base_url}{path}", json=body, headers=headers)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise OpenRouterError(f"{path} {r.status_code}: {r.text[:300]}")
            return r.json()
        raise OpenRouterError(f"{path}: gave up after retries")

    def embed(self, texts: list[str], batch: int = 100, model: str | None = None) -> list[np.ndarray]:
        """One float32 vector per text. Converted batch by batch: a 4,096-number vector
        takes 16 KB this way and about 130 KB as a Python list."""
        out: list[np.ndarray] = []
        for start in range(0, len(texts), batch):
            data = self._post("/embeddings", {"model": model or self.settings.embedding_model, "input": texts[start:start + batch]})
            self.embedding_usage.add(data.get("usage"))
            rows = sorted(data["data"], key=lambda d: d["index"])
            out.extend(np.asarray(row["embedding"], dtype=np.float32) for row in rows)
            del data, rows
        return out

    def chat_json(self, system: str, user: str, schema_name: str, schema: dict) -> dict:
        body = {
            "model": self.settings.llm_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
            "temperature": 0.2,
            "reasoning": {"enabled": False},
            "usage": {"include": True},
        }
        data = self._post("/chat/completions", body)
        self.llm_usage.add(data.get("usage"))
        content = data["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise OpenRouterError(f"model did not return JSON: {content[:200]}") from e

    def close(self) -> None:
        self.client.close()
