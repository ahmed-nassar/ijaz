"""Words for matching: casefolded word characters, with Arabic spelling normalised.

Arabic headlines spell the same word in several ways and glue short words to it, so
«الذهب», «ذهب» and «بالذهب», or «أسعار» and «اسعار», would not match as written.
Each word is normalised (letter forms, diacritics, digits) and a leading
conjunction, preposition or article is removed when at least three letters are left.
Endings are kept: removing them too joined unrelated articles and lowered recall at
95% precision on the answer key (words alone, same-language pairs: 19.7% as written,
26.8% normalised, 30.7% with prefixes removed, 23.2% with endings removed as well).
Nothing here changes the stored or shown text.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"\w+", re.UNICODE)

# Diacritics (tashkeel), superscript alef, and the tatweel stretch.
_MARKS = re.compile("[ً-ْٰـ]")
_LETTERS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",  # alef forms
    "ى": "ي",  # alef maqsura
    "ة": "ه",  # ta marbuta
    "ؤ": "و", "ئ": "ي",  # hamza carriers
    # Arabic-Indic and Persian digits, so «٦٫٨» and «6.8» match
    **{chr(0x0660 + d): str(d) for d in range(10)},
    **{chr(0x06F0 + d): str(d) for d in range(10)},
})

# Longest first. «ال» alone also covers words starting with it after normalisation.
_PREFIXES = ("وبال", "وكال", "وال", "بال", "كال", "فال", "لل", "ال", "و", "ب", "ل", "ف")
_MIN_STEM = 3


def _is_arabic(word: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in word)


def normalise_arabic(word: str) -> str:
    return _MARKS.sub("", word).translate(_LETTERS)


def stem_arabic(word: str) -> str:
    """Remove at most one leading particle or article, keeping at least three letters."""
    for p in _PREFIXES:
        if word.startswith(p) and len(word) - len(p) >= _MIN_STEM:
            return word[len(p):]
    return word


def words(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.casefold()):
        if _is_arabic(w):
            w = stem_arabic(normalise_arabic(w))
        else:
            w = w.translate(_LETTERS)  # digits written in Arabic script inside a Latin run
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
