"""Homepage ranking (spec section 5).

    score = log(1 + sum over owner groups of a_g) x E x G x C x 2^(-age / h) x (1 + beta V) x M

then a re-ranking pass applies the caps: at most 4 lead stories per publisher in the
top 30, at most 8 of the top 20 from one country, and a story with a single
independent source stays out of the top 10 until a second group or an official or
high-authority source reports it.

What is not built yet, and so is fixed at 1: G and C (page profiles; the homepage is
the only page), M (no editor tools yet). E comes from the language model's importance
rating while event types are not classified. The weights are starting guesses, to be
tuned against an editor's order.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Importance 1..5 from the language model, mapped onto the spec's E range (0.6 to 1.6).
IMPORTANCE_E = {1: 0.6, 2: 0.8, 3: 1.0, 4: 1.3, 5: 1.6}
UNRATED_E = 1.0  # single-article stories are not sent to the model
EXTRA_OUTLET_BONUS = 0.1  # per extra outlet of the same owner group, up to 3
HIGH_AUTHORITY = 0.8
VELOCITY_WINDOW = timedelta(hours=2)
PUBLISHER_CAP, PUBLISHER_CAP_SPAN = 4, 30
COUNTRY_CAP, COUNTRY_CAP_SPAN = 8, 20
SINGLE_SOURCE_FLOOR = 10  # unconfirmed stories start at position 11


@dataclass
class Report:
    publisher_slug: str
    owner_group: str | None
    authority: float
    official: bool  # state-affiliated publisher
    time: datetime


@dataclass
class Candidate:
    story_id: int
    lead_publisher: str
    lead_country: str | None
    importance: int | None
    last_time: datetime
    reports: list[Report] = field(default_factory=list)


def score(c: Candidate, now: datetime, half_life_hours: float, beta: float) -> dict:
    groups: dict[str, list[Report]] = defaultdict(list)
    for r in c.reports:
        groups[r.owner_group or f"publisher:{r.publisher_slug}"].append(r)
    total = 0.0
    for members in groups.values():
        outlets = len({r.publisher_slug for r in members})
        best = max(r.authority for r in members)
        total += best * (1 + EXTRA_OUTLET_BONUS * min(outlets - 1, 3))
    coverage = math.log(1 + total)
    e = IMPORTANCE_E.get(c.importance, UNRATED_E)
    age_hours = max(0.0, (now - c.last_time).total_seconds() / 3600)
    recency = 2 ** (-age_hours / half_life_hours)
    # A group is new if its first report in the story came in the last two hours.
    recent_groups = sum(1 for members in groups.values() if now - min(r.time for r in members) <= VELOCITY_WINDOW)
    velocity = recent_groups if len(groups) > 1 else 0  # a lone first report is not momentum
    confirmed = len(groups) >= 2 or any(r.official or r.authority >= HIGH_AUTHORITY for r in c.reports)
    value = coverage * e * 1.0 * 1.0 * recency * (1 + beta * velocity) * 1.0
    return {
        "score": round(value, 5),
        "coverage": round(coverage, 4),
        "groups": len(groups),
        "outlets": len({r.publisher_slug for r in c.reports}),
        "E": e,
        "G": 1.0,
        "C": 1.0,
        "age_hours": round(age_hours, 2),
        "recency": round(recency, 4),
        "velocity": velocity,
        "M": 1.0,
        "confirmed": confirmed,
    }


def rank(candidates: list[Candidate], now: datetime, half_life_hours: float = 12.0, beta: float = 0.15, size: int = 30) -> list[dict]:
    """The top ``size`` stories in order, each with its score breakdown and, when a cap
    moved it down, the reason."""
    scored = [(score(c, now, half_life_hours, beta), c) for c in candidates]
    scored.sort(key=lambda sc: (-sc[0]["score"], -sc[1].last_time.timestamp(), sc[1].story_id))
    by_publisher: Counter[str] = Counter()
    by_country: Counter[str] = Counter()
    chosen: list[dict] = []
    remaining = list(scored)
    while remaining and len(chosen) < size:
        pos = len(chosen)
        pick = None
        for k, (parts, c) in enumerate(remaining):
            if by_publisher[c.lead_publisher] >= PUBLISHER_CAP and pos < PUBLISHER_CAP_SPAN:
                continue
            if c.lead_country and pos < COUNTRY_CAP_SPAN and by_country[c.lead_country] >= COUNTRY_CAP:
                continue
            if not parts["confirmed"] and pos < SINGLE_SOURCE_FLOOR:
                continue
            pick = k
            break
        if pick is None:
            break
        parts, c = remaining.pop(pick)
        by_publisher[c.lead_publisher] += 1
        if pos < COUNTRY_CAP_SPAN and c.lead_country:
            by_country[c.lead_country] += 1
        chosen.append({"story_id": c.story_id, "rank": pos + 1, **parts})
    # Record which caps moved a story below a lower-scored one.
    order = {sc[1].story_id: i for i, sc in enumerate(scored)}
    for entry in chosen:
        natural = order[entry["story_id"]] + 1
        if entry["rank"] > natural:
            entry["moved_down_from"] = natural
    return chosen
