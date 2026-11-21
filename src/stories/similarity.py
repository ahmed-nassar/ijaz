"""Pairwise similarity: word overlap (TF-IDF cosine) and meaning (embedding cosine)."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np


def word_similarity(docs: list[list[str]]) -> np.ndarray:
    """Cosine similarity of TF-IDF vectors. Words common across the set weigh little,
    so «اليوم» or «says» do not make two articles look alike."""
    n = len(docs)
    df = Counter()
    for d in docs:
        df.update(set(d))
    vocab = {w: i for i, w in enumerate(df)}
    idf = np.array([math.log((1 + n) / (1 + df[w])) + 1 for w in vocab], dtype=np.float32)
    m = np.zeros((n, len(vocab)), dtype=np.float32)
    for row, d in enumerate(docs):
        for w, c in Counter(d).items():
            m[row, vocab[w]] = (1 + math.log(c)) * idf[vocab[w]]
    return _cosine(m)


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
