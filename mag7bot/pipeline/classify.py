"""Rule-based event classification (PRD F2).

EDGAR items classify from their form type; news items from headline keywords.
This is intentionally a deterministic, auditable rule set for the MVP — an
LLM classifier is a later-phase option. When nothing matches, an item is
``EventType.NEWS`` (which routes to the digest).

Keyword lists are ordered by priority: the first matching category wins, so
more specific/severe categories (M&A, legal) are checked before generic ones.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from ..schemas import EventType, RawItem

# 8-K item 2.02 / "results of operations" signals an earnings release.
_EARNINGS_FORM_HINTS = ("results of operations", "earnings", "financial results")

# (EventType, keywords) — checked in order; first hit wins.
_KEYWORD_RULES: List[Tuple[EventType, Tuple[str, ...]]] = [
    (
        EventType.MA,
        ("acquire", "acquisition", "merger", "merges", "buyout", "takeover",
         "to buy", "stake in", "divest", "spin off", "spinoff"),
    ),
    (
        EventType.LEGAL_REGULATORY,
        ("lawsuit", "sues", "sued", "antitrust", "probe", "investigation",
         "sec charges", "fine", "settlement", "regulator", "subpoena", "fraud"),
    ),
    (
        EventType.MANAGEMENT_CHANGE,
        ("ceo", "cfo", "resign", "steps down", "appoint", "names new",
         "chief executive", "chief financial", "departs", "succession"),
    ),
    (
        EventType.EARNINGS,
        ("earnings", "quarterly results", "beats estimates", "misses estimates",
         "revenue", "guidance", "profit", "eps"),
    ),
    (
        EventType.ANALYST,
        ("upgrade", "downgrade", "price target", "initiates coverage",
         "raises target", "cuts target", "overweight", "underweight",
         "buy rating", "sell rating", "outperform"),
    ),
    (
        EventType.INDEX_LISTING,
        ("added to", "removed from", "index", "s&p 500", "nasdaq 100",
         "delisting", "stock split"),
    ),
    (
        EventType.PRODUCT_LAUNCH,
        ("launches", "unveils", "announces", "introduces", "releases",
         "debuts", "new product", "rolls out"),
    ),
]


def _from_form(form: str, headline: str) -> EventType:
    h = headline.lower()
    if form.startswith("8-K") and any(hint in h for hint in _EARNINGS_FORM_HINTS):
        return EventType.EARNINGS
    if form.startswith(("10-Q", "10-K")):
        return EventType.EARNINGS  # periodic financial reports
    return EventType.SEC_FILING


def classify(item: RawItem) -> EventType:
    """Assign an EventType to a raw item."""
    if item.source == "edgar" and item.form_type:
        return _from_form(item.form_type, item.headline)

    text = item.headline.lower()
    for event_type, keywords in _KEYWORD_RULES:
        for kw in keywords:
            # Word-ish boundary so "sues" doesn't match inside "issues".
            if re.search(r"\b" + re.escape(kw), text):
                return event_type
    return EventType.NEWS
