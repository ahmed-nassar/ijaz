"""Text helpers: HTML stripping, excerpt capping, Arabic normalization, SimHash, language."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from html.parser import HTMLParser

_WS = re.compile(r"\s+")
_ARABIC_DIACRITICS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_TOKEN = re.compile(r"[\w؀-ۿ]+", re.UNICODE)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: D401
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return clean_whitespace(html.unescape("".join(parser.parts)))


def clean_whitespace(value: str) -> str:
    return _WS.sub(" ", value).strip()


def cap_excerpt(value: str | None, max_chars: int) -> str | None:
    """Cap an excerpt on a word boundary and add an ellipsis when cut."""
    text = strip_html(value)
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    space = cut.rfind(" ")
    if space > max_chars * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.،؛:;-—") + "…"


def normalize_arabic(value: str) -> str:
    """Matching form only (never displayed): unify letter variants, drop diacritics."""
    value = unicodedata.normalize("NFKC", value)
    value = _ARABIC_DIACRITICS.sub("", value).replace(_TATWEEL, "")
    value = re.sub("[إأآٱ]", "ا", value)
    value = value.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    value = value.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    return value.lower()


def tokens(value: str) -> list[str]:
    return _TOKEN.findall(normalize_arabic(value))


def simhash(value: str) -> int:
    """64-bit SimHash over word shingles, returned as a signed int for BIGINT columns."""
    words = tokens(value)
    if not words:
        return 0
    features = words if len(words) < 3 else [" ".join(words[i : i + 2]) for i in range(len(words) - 1)]
    weights = [0] * 64
    for feature in features:
        h = int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")
        for bit in range(64):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    result = 0
    for bit in range(64):
        if weights[bit] > 0:
            result |= 1 << bit
    return result - (1 << 64) if result >= (1 << 63) else result


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
_TURKISH_LETTERS = set("ğĞışŞİ")
# Short words that are common in English headlines and are not Turkish words.
_ENGLISH_WORDS = frozenset(
    "the of to and for with from after over says is are was were as at by on in into amid "
    "against new its his her their who what how why will has have be not more than".split()
)
# Short Turkish words with no Turkish-specific letters.
_TURKISH_WORDS = frozenset("ve bir ile bu da de gibi olarak sonra daha çok için".split())


def detect_language(text: str, declared: str | None = None) -> str:
    """Check the language a feed or our source list declares against the text.

    Arabic script decides Arabic. Otherwise, among English and Turkish (the Latin-script
    languages we collect), the words decide: English function words such as "the",
    "of" and "to" mean English; Turkish-specific letters (ğ ı ş İ) and words such as
    "ve" and "için" mean Turkish. When English is declared, it takes clearly more
    Turkish than English words to overrule it, since English headlines often name
    Turkish people and places ("Erdoğan says..."). When the text gives no clear sign,
    the declared language stands, defaulting to English.
    """
    declared = (declared or "").split("-")[0].lower() or None
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return declared or "und"
    arabic = sum(1 for c in letters if "\u0600" <= c <= "\u06ff" or "\u0750" <= c <= "\u077f")
    if arabic / len(letters) > 0.5:
        return "ar"
    if declared == "ar":
        # Declared Arabic but written in Latin script: trust the text.
        declared = None
    if declared not in (None, "en", "tr"):
        return declared
    words = [w.lower() for w in _WORD.findall(text)]
    english = sum(1 for w in words if w in _ENGLISH_WORDS)
    turkish = sum(1 for w in words if w in _TURKISH_WORDS or any(c in _TURKISH_LETTERS for c in w))
    if declared == "en":
        # Our source list says English: a name or two with Turkish letters is not enough.
        return "tr" if turkish >= max(2, english + 2) else "en"
    if english >= 2 and english >= turkish:
        return "en"
    if turkish:
        return "tr"
    return declared or "en"
