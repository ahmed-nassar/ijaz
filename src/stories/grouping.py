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


def group_with_judge(items: list[Item], score: np.ndarray, low: float, high: float, judge, window_hours: float = 48.0, max_candidates: int = 3) -> list[list[int]]:
    """Like ``group``, but a story scoring between ``low`` and ``high`` is not joined
    or refused on the score alone: ``judge(i, candidates)`` decides, where
    ``candidates`` lists up to ``max_candidates`` stories (lists of item indexes),
    best first, and the answer is the chosen position or None. A story scoring
    ``high`` or more is joined without asking."""
    order = sorted(range(len(items)), key=lambda i: (items[i].time, items[i].id))
    stories: list[list[int]] = []
    window = window_hours * 3600
    for i in order:
        ranked = []
        for s, members in enumerate(stories):
            recent = [m for m in members if (items[i].time - items[m].time).total_seconds() <= window]
            if not recent:
                continue
            top = sorted((float(score[i, m]) for m in recent), reverse=True)[:2]
            value = sum(top) / len(top)
            if value >= low:
                ranked.append((value, s))
        ranked.sort(reverse=True)
        chosen = None
        if ranked and ranked[0][0] >= high:
            chosen = ranked[0][1]
        elif ranked:
            candidates = [s for _, s in ranked[:max_candidates]]
            pick = judge(i, [stories[s] for s in candidates])
            if pick is not None and 0 <= pick < len(candidates):
                chosen = candidates[pick]
        if chosen is None:
            stories.append([i])
        else:
            stories[chosen].append(i)
    return [[items[i].id for i in members] for members in stories]


def extend(
    items: list[Item],
    score: np.ndarray,
    stories: list[list[int]],
    new: list[int],
    low: float,
    high: float,
    judge=None,
    window_hours: float = 48.0,
    max_candidates: int = 3,
) -> list[tuple[int, int, float | None, str]]:
    """The live version of ``group_with_judge``: ``stories`` already exist (lists of
    item indexes) and only the items at indexes ``new`` are placed, oldest first.

    ``stories`` is changed in place: new items are appended and new stories added at
    the end. Returns, per new item, (item index, story position, score, how), where how
    is "score", "judge" or "new". Without a judge, the band is skipped and ``high`` is
    the only threshold.
    """
    window = window_hours * 3600
    placed = []
    for i in sorted(new, key=lambda i: (items[i].time, items[i].id)):
        ranked = []
        for s, members in enumerate(stories):
            recent = [m for m in members if abs((items[i].time - items[m].time).total_seconds()) <= window]
            if not recent:
                continue
            top = sorted((float(score[i, m]) for m in recent), reverse=True)[:2]
            value = sum(top) / len(top)
            if value >= (low if judge else high):
                ranked.append((value, s))
        ranked.sort(reverse=True)
        chosen, how, value = None, "new", None
        if ranked and ranked[0][0] >= high:
            chosen, how, value = ranked[0][1], "score", ranked[0][0]
        elif ranked and judge is not None:
            candidates = ranked[:max_candidates]
            pick = judge(i, [stories[s] for _, s in candidates])
            if pick is not None and 0 <= pick < len(candidates):
                value, chosen = candidates[pick]
                how = "judge"
        if chosen is None:
            stories.append([i])
            chosen = len(stories) - 1
        else:
            stories[chosen].append(i)
        placed.append((i, chosen, value, how))
    return placed
