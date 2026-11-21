"""Pairwise similarity: word overlap (TF-IDF cosine) and meaning (embedding cosine)."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np


def word_similarity(docs: list[list[str]]) -> np.ndarray:
    """Cosine similarity of TF-IDF vectors. Words common across the set weigh little,
    so «اليوم» or «says» do not make two articles look alike.

    Words found in only one article count towards its vector length but cannot be
    shared, so they are left out of the matrix: same result, a fraction of the memory
    (most of a news vocabulary appears once)."""
    n = len(docs)
    df = Counter()
    for d in docs:
        df.update(set(d))
    idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}
    shared = {w: i for i, w in enumerate(w for w, c in df.items() if c > 1)}
    m = np.zeros((n, len(shared)), dtype=np.float32)
    norms = np.zeros(n, dtype=np.float64)
    for row, d in enumerate(docs):
        for w, c in Counter(d).items():
            weight = (1 + math.log(c)) * idf[w]
            norms[row] += weight * weight
            col = shared.get(w)
            if col is not None:
                m[row, col] = weight
    norms = np.sqrt(norms)
    empty = norms == 0
    norms[empty] = 1.0
    m /= norms[:, None].astype(np.float32)
    sim = m @ m.T
    np.fill_diagonal(sim, np.where(empty, 0.0, 1.0))
    return sim


def embedding_similarity(vectors: list[list[float]]) -> np.ndarray:
    return np.clip(_cosine(np.asarray(vectors, dtype=np.float32)), 0.0, 1.0)


def combine(word: np.ndarray, meaning: np.ndarray, word_weight: float) -> np.ndarray:
    return word_weight * word + (1.0 - word_weight) * meaning


def _cosine(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = m / norms
    return unit @ unit.T


def combine_by_language(word: np.ndarray, meaning: np.ndarray, languages: list[str], same_weight: float, cross_weight: float) -> np.ndarray:
    """Like ``combine``, with a separate word weight for pairs in different languages,
    where shared words are rare (names and numbers at most)."""
    lang = np.asarray(languages)
    same = lang[:, None] == lang[None, :]
    weight = np.where(same, same_weight, cross_weight)
    return weight * word + (1.0 - weight) * meaning
