"""Grouping articles into stories, one article at a time in publishing order.

Each new article joins the existing story it matches best, if that match reaches the
threshold; otherwise it starts a story. A story's match is the average of the new
article's two best scores against the story's members (the single score for a
one-article story). Using two members rather than one keeps a single loose link
from chaining unrelated articles together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np


@dataclass
class Item:
    id: int
    time: datetime


def group(items: list[Item], score: np.ndarray, threshold: float, window_hours: float = 48.0) -> list[list[int]]:
    """``score[i, j]`` is the similarity of items[i] and items[j]. Returns lists of item ids."""
    order = sorted(range(len(items)), key=lambda i: (items[i].time, items[i].id))
    stories: list[list[int]] = []  # indexes into items
    window = window_hours * 3600
    for i in order:
        best, best_score = None, threshold
        for s, members in enumerate(stories):
            recent = [m for m in members if (items[i].time - items[m].time).total_seconds() <= window]
            if not recent:
                continue
            top = sorted((float(score[i, m]) for m in recent), reverse=True)[:2]
            value = sum(top) / len(top)
            if value >= best_score:
                best, best_score = s, value
        if best is None:
            stories.append([i])
        else:
            stories[best].append(i)
    return [[items[i].id for i in members] for members in stories]
