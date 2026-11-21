"""Words for matching: casefolded word characters, nothing else.

Arabic spelling normalisation (alef forms, ya and alef maqsura, ta marbuta) is a
noted later improvement, kept out on purpose so the first measurement shows the
design as agreed.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.casefold()):
        if w.isdigit() or len(w) > 1:
            out.append(w)
    return out


def article_words(title: str, excerpt: str | None) -> list[str]:
    """The headline counts twice: it says what the story is; the excerpt adds context."""
    head = words(title)
    return head + head + words(excerpt or "")


def embedding_text(title: str, excerpt: str | None) -> str:
    """Only the headline and the short excerpt are ever sent out, never article text."""
    return f"{title}\n{excerpt}" if excerpt else title
