"""Shared plumbing for the international macro sources (GM-B1).

Japan / China / Eurozone / UK central-bank and statistics-office releases are
published as RSS/Atom press-release feeds. This module provides one parser and a
base ``Source`` so each economy module (``macro_jp`` etc.) is a thin config:
its feed URLs, publisher label, and the keyword maps that decide (a) whether an
item is a macro release worth alerting and (b) whether it's a central-bank rate
decision (CRITICAL, quiet-hours override) or a data print (MATERIAL).

Design notes:
  * Government / central-bank publications → ``commercial_safe=True`` (public
    records); classified in ``config.COMMERCIAL_SAFE_SOURCES``.
  * Fail-soft: any network/parse error yields no items, never an exception that
    could abort the poll cycle (a broken feed is isolated upstream too).
  * Actual/consensus/prior figures rarely appear in a press-release title; when
    absent the alert renders lead + link only (never a fabricated estimate,
    GM-B3-03). Numeric figures found in the title are captured best-effort.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

import httpx

from ..schemas import RawItem, Tier
from .base import SeenFn, Source

# A percentage figure in a headline, e.g. "0.75%", "+3.2%". Restricted to
# percentages so a year in "(May 2026)" isn't mistaken for the reading; non-%
# levels are left empty rather than guessed (GM-B3-03 honesty).
_NUM_IN_TITLE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?%")


def _text(el: Optional[ET.Element]) -> str:
    return (el.text or "").strip() if el is not None else ""


def _first(item: ET.Element, *tags: str) -> str:
    for tag in tags:
        found = item.find(tag)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def classify_release(
    title: str, cb_hints: Sequence[str], indicator_map: Dict[str, str]
) -> Optional[Tuple[str, str]]:
    """Return ``(kind, indicator)`` for a release title, or None to skip it.

    ``kind`` is ``"central_bank"`` (rate decision) or ``"data"`` (a print). An
    item matching neither the central-bank hints nor a known indicator is not a
    macro release we alert on."""
    low = title.lower()
    if any(h in low for h in cb_hints):
        # Try to name the specific indicator (e.g. "Policy Rate") if mapped.
        for kw, label in indicator_map.items():
            if kw in low:
                return "central_bank", label
        return "central_bank", "Policy Rate"
    for kw, label in indicator_map.items():
        if kw in low:
            return "data", label
    return None


def parse_macro_rss(
    xml: str, *, economy: str, source_name: str, publisher: str,
    cb_hints: Sequence[str], indicator_map: Dict[str, str], now: float,
) -> List[RawItem]:
    """Parse an RSS/Atom feed into macro ``RawItem``s (ticker ``MACRO``)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    # RSS <item> or Atom <entry>.
    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry"
    )
    out: List[RawItem] = []
    for it in items:
        title = _first(it, "title", "{http://www.w3.org/2005/Atom}title")
        if not title:
            continue
        link = _first(it, "link", "guid")
        if not link:
            atom_link = it.find("{http://www.w3.org/2005/Atom}link")
            link = atom_link.get("href", "") if atom_link is not None else ""
        classified = classify_release(title, cb_hints, indicator_map)
        if classified is None:
            continue
        kind, indicator = classified
        nums = _NUM_IN_TITLE.findall(title)
        macro = {
            "economy": economy, "indicator": indicator, "period": "",
            "actual": nums[0] if nums else "", "consensus": "", "prior": "",
        }
        out.append(
            RawItem(
                source=source_name,
                source_item_id=f"{source_name}:{link or title}",
                ticker="MACRO",
                tier=Tier.PRIMARY,
                headline=title,
                url=link,
                publisher=publisher,
                published_at=now,  # detection/release time
                payload={"macro": macro, "macro_kind": kind},
            )
        )
    return out


class MacroRssSource(Source):
    """Base international-macro source: fetch one or more RSS feeds and parse
    each into macro release items. Subclasses set the class attributes below."""

    name: str = "macro_intl"
    tier = Tier.PRIMARY
    economy: str = ""
    publisher: str = ""
    feeds: Sequence[str] = ()
    cb_hints: Sequence[str] = ()
    indicator_map: Dict[str, str] = {}

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        out: List[RawItem] = []
        now = time.time()
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            for url in self.feeds:
                try:
                    resp = await client.get(url, headers={"User-Agent": "mag7bot/1.0"})
                    resp.raise_for_status()
                    parsed = parse_macro_rss(
                        resp.text, economy=self.economy, source_name=self.name,
                        publisher=self.publisher, cb_hints=self.cb_hints,
                        indicator_map=self.indicator_map, now=now,
                    )
                except (httpx.HTTPError, ValueError):
                    continue
                for item in parsed:
                    if not is_seen(self.name, item.source_item_id):
                        out.append(item)
        return out
