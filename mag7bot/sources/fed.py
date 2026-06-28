"""Federal Reserve RSS source — FOMC decisions (critical) + speeches (material).

Polls two public Fed RSS feeds with no API key required:

  press_all — all press releases; filtered to monetary-policy-relevant items
              (FOMC statements, rate decisions, minutes, economic testimony).
              Classified as MACRO → CRITICAL (overrides quiet hours).

  speeches  — all speeches by Fed governors and the Chair.
              Classified as EXEC_COMMENTARY → MATERIAL (respects quiet hours).

Items are economy-wide (ticker="MACRO", tier=PRIMARY). The source bypasses the
publisher whitelist and relevance gate (same pattern as macro/earnings). The
federalreserve.gov domain is already in WHITELIST_DOMAINS for cross-source
consistency.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import List

import httpx

from ..schemas import RawItem, Tier
from .base import SeenFn, Source

PRESS_RSS = "https://www.federalreserve.gov/feeds/press_all.xml"
SPEECHES_RSS = "https://www.federalreserve.gov/feeds/speeches.xml"

# Press releases whose title contains any of these terms are market-relevant.
_MONETARY_KEYWORDS = (
    "federal open market committee",
    "fomc",
    "interest rate",
    "federal funds",
    "monetary policy",
    "balance sheet",
    "open market operation",
    "quantitative",
    "economic outlook",
    "inflation",
    "employment situation",
    "testimony",
    "semiannual",
)


def _to_epoch(pub_date: str) -> float:
    try:
        return parsedate_to_datetime(pub_date).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _parse_rss(xml_text: str, feed_type: str) -> List[RawItem]:
    """Parse a Fed RSS feed into RawItems. Pure/testable — no network."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items: List[RawItem] = []
    for el in root.iter("item"):
        title = (el.findtext("title") or "").strip()
        link = (el.findtext("link") or "").strip()
        guid = (el.findtext("guid") or link).strip()
        pub = (el.findtext("pubDate") or "").strip()
        description = (el.findtext("description") or "").strip()
        if not title or not link:
            continue
        if feed_type == "press_release":
            tl = title.lower()
            if not any(kw in tl for kw in _MONETARY_KEYWORDS):
                continue
        items.append(
            RawItem(
                source="fed",
                source_item_id=guid,
                ticker="MACRO",
                tier=Tier.PRIMARY,
                headline=title,
                body=description,
                url=link,
                publisher="Federal Reserve",
                published_at=_to_epoch(pub),
                form_type=None,
                payload={"feed_type": feed_type},
            )
        )
    return items


class FedSource(Source):
    name = "fed"
    tier = Tier.PRIMARY

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout
        self._headers = {"User-Agent": "Mozilla/5.0 (compatible; mag7bot/1.0)"}

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        out: List[RawItem] = []
        feeds = [
            (PRESS_RSS, "press_release"),
            (SPEECHES_RSS, "speech"),
        ]
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            for url, feed_type in feeds:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    items = _parse_rss(resp.text, feed_type)
                except httpx.HTTPError:
                    continue
                for item in items:
                    if not is_seen(self.name, item.source_item_id):
                        out.append(item)
        return out
