"""Separate an author's own words from what the Discord page renders around them.

The Chrome extension reads every message-content node of a message
(tools/discord_copier_extension/content.js ``getMessageContent``), so the stored text
can carry two things the author did not write:

* the browser translator's output, appended after a rule of 31 dashes - on the same
  line, or on the lines after a dash-only line;
* a reply or forward preview, as a line headed "author YYYY-MM-DD HH:MM" (Discord
  renders missing dates as 1970-01-01 08:00).

Both misled intent parsing on 2026-10-07: "Longed met at 0.455 sl; 0.4375" was
translated as "做多在 0.455 已达止损" and the model read a stop-out; a quoted
"BTC: Closed in profits (100%)" turned commentary into a close order. Intent is read
from the own text; previews are kept as context; the translation is dropped.
"""
from __future__ import annotations

import re

TRANSLATION_RULE = re.compile(r"\s*-{20,}")
QUOTE_HEADER = re.compile(r"^\S.{0,60}?\s(?:1970-01-01|\d{4}-\d{2}-\d{2})\s\d{1,2}:\d{2}")


def split_own_text(content: str) -> tuple[str, str]:
    """Return (own text, quoted preview lines)."""
    text = str(content or "")
    rule = TRANSLATION_RULE.search(text)
    head, tail = (text[:rule.start()], text[rule.end():]) if rule else (text, "")
    own, quoted = [], []
    for line in head.split("\n"):
        line = line.strip()
        if line:
            (quoted if QUOTE_HEADER.match(line) else own).append(line)
    for line in tail.split("\n"):
        line = TRANSLATION_RULE.split(line.strip(), maxsplit=1)[0].strip()
        if line and QUOTE_HEADER.match(line):
            quoted.append(line)
    return "\n".join(own), "\n".join(quoted)


def own_text(content: str) -> str:
    return split_own_text(content)[0]
