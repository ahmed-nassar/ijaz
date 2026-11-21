"""The language model step for one story: an Arabic headline, an importance rating,
Arabic translations of foreign-language headlines and the event date, all in one call;
then a separate quality check of that headline against the reports.

The model sees only what the database holds: headlines, short excerpts, publisher
names and times. It is told to use nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from .openrouter import OpenRouter

WRITE_SYSTEM = """\
You write for إيجاز, an Arabic news site that groups reports about the same event.
You get the reports about one story: each has an id, publisher, language, time, headline and a short excerpt.
Use only facts stated in these reports. Do not add background, numbers or names that are not in them.

Return JSON with:
- headline_ar: one neutral Modern Standard Arabic headline for the story, at most 90 characters, in the style of a wire service. Attribute claims ("قال ...", "بحسب ...") when the reports do.
- importance: an integer from 1 to 5 for a Middle East reader.
  5: major event with regional or world impact (war escalation, many deaths, a head of state's decisive act).
  4: important national or regional news (government decisions, deadly incidents, significant diplomacy).
  3: notable news of wide interest.
  2: routine or local news (prices, weather, ceremonies, minor court cases).
  1: service pieces, listings, features with no news event.
- importance_reason: one short English sentence.
- event_date: the date the event happened, as YYYY-MM-DD, only if a report states or clearly implies it; otherwise null.
- translations: for every report whose language is not Arabic, its id and an Arabic translation of its headline. Empty list if all are Arabic.
"""

WRITE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline_ar", "importance", "importance_reason", "event_date", "translations"],
    "properties": {
        "headline_ar": {"type": "string"},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5},
        "importance_reason": {"type": "string"},
        "event_date": {"type": ["string", "null"]},
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title_ar"],
                "properties": {"id": {"type": "integer"}, "title_ar": {"type": "string"}},
            },
        },
    },
}

CHECK_SYSTEM = """\
You check headlines for إيجاز, an Arabic news site.
You get a proposed Arabic headline and the reports it summarises.
Decide whether every fact in the headline (names, numbers, places, who said what) is supported by the reports, and whether it is neutral and grammatical Arabic.
Return JSON with ok (true only if there is no problem) and issues (short English descriptions; empty if ok).
"""

CHECK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok", "issues"],
    "properties": {"ok": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}},
}


@dataclass
class Report:
    id: int
    publisher: str
    language: str
    time: str
    title: str
    excerpt: str | None


def reports_text(reports: list[Report], limit: int = 12) -> str:
    lines = []
    for r in reports[:limit]:
        lines.append(f"[id {r.id}] {r.publisher} ({r.language}, {r.time})\n{r.title}\n{(r.excerpt or '').strip()}")
    return "\n\n".join(lines)


def enrich(client: OpenRouter, reports: list[Report]) -> dict:
    text = reports_text(reports)
    result = client.chat_json(WRITE_SYSTEM, text, "story", WRITE_SCHEMA)
    check = client.chat_json(CHECK_SYSTEM, f"Headline: {result['headline_ar']}\n\nReports:\n{text}", "check", CHECK_SCHEMA)
    result["quality"] = check
    return result
