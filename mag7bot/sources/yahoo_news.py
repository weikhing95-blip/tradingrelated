"""Yahoo Finance source — Tier 2, conditional (per-ticker news RSS).

Uses Yahoo Finance's per-ticker headline RSS
(``feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL``). Unlike Google News the
query is per-ticker and the links are direct (no redirect wrapper), so it's a
cleaner breadth source. Publisher is Yahoo Finance (whitelisted by default);
the pipeline whitelist still applies, and dedup cross-confirms with Finnhub.

Off by default; enable with ``ENABLE_YAHOO_NEWS=true``. The endpoint is
undocumented and has been flaky historically — the source fails soft (yields
nothing) if it's unavailable, never crashing the poll loop.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Callable, List
from urllib.parse import urlparse

_TAGS = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return unescape(_TAGS.sub(" ", text or "")).strip()

import httpx

from ..pipeline import whitelist
from ..schemas import RawItem, Tier
from .base import SeenFn, Source

HEADLINE_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"


def _to_epoch(pub_date: str) -> float:
    try:
        return parsedate_to_datetime(pub_date).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _publisher(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host or "Yahoo Finance"


def parse_rss(xml_text: str, ticker: str) -> List[RawItem]:
    """Parse a Yahoo Finance headline RSS payload into RawItems. Pure/testable."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items: List[RawItem] = []
    for el in root.iter("item"):
        link = el.findtext("link") or ""
        title = (el.findtext("title") or "").strip()
        guid = el.findtext("guid") or link
        pub = el.findtext("pubDate") or ""
        description = _strip_html(el.findtext("description") or "")
        if not link or not title:
            continue
        items.append(
            RawItem(
                source="yahoo_news",
                source_item_id=guid,
                ticker=ticker.upper(),
                tier=Tier.WIRE,
                headline=title,
                body=description,
                url=link,
                publisher=_publisher(link),
                published_at=_to_epoch(pub),
                payload={"description": description},
            )
        )
    return items


class YahooNewsSource(Source):
    name = "yahoo_news"
    tier = Tier.WIRE

    def __init__(
        self, whitelist_provider: Callable[[], List[str]], timeout: float = 12.0
    ) -> None:
        self._whitelist_provider = whitelist_provider
        self._timeout = timeout
        self._headers = {"User-Agent": "Mozilla/5.0 (compatible; mag7bot/1.0)"}

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        out: List[RawItem] = []
        allow = self._whitelist_provider()
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            for ticker in tickers:
                params = {"s": ticker.upper(), "region": "US", "lang": "en-US"}
                try:
                    resp = await client.get(HEADLINE_URL, params=params)
                    resp.raise_for_status()
                    parsed = parse_rss(resp.text, ticker)
                except httpx.HTTPError:
                    continue
                for item in parsed:
                    if is_seen(self.name, item.source_item_id):
                        continue
                    if not whitelist.is_approved(item, allow):
                        continue
                    out.append(item)
        return out
