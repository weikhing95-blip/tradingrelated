"""Google News source — Tier 2, conditional aggregator (PRD §4, F11).

Google News has no official API; this uses the undocumented per-keyword RSS
feed (``news.google.com/rss/search?q=...``). Because it aggregates *everyone*,
it reintroduces the noise/trust problem the bot exists to avoid, so two
safeguards from F11 apply here:

  (a) **whitelist** — each item's *resolved publisher* (the RSS ``<source>``
      element, which Google supplies) is checked against the domain whitelist;
      non-approved publishers are dropped. The pipeline re-applies the whitelist
      too, so this is defence-in-depth.
  (b) **redirect resolution** — feed links are ``news.google.com`` redirects, so
      we best-effort resolve each kept item to its canonical publisher URL.

This source is **off by default** and enabled via ``ENABLE_GOOGLE_NEWS=true``.
Dedup collapses its items against Finnhub's, so overlap becomes cross-confirm,
not duplication.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Callable, List
from urllib.parse import urlparse

import httpx

from .. import companies
from ..pipeline import whitelist
from ..schemas import RawItem, Tier
from .base import SeenFn, Source

RSS_URL = "https://news.google.com/rss/search"

# Per-ticker query strings (tuned for relevance; whitelist does the rest).
QUERIES = {
    "AAPL": "Apple Inc stock",
    "MSFT": "Microsoft stock",
    "GOOGL": "Alphabet Google stock",
    "AMZN": "Amazon.com stock",
    "NVDA": "NVIDIA stock",
    "META": "Meta Platforms stock",
    "TSLA": "Tesla stock",
}


def _query(ticker: str) -> str:
    if ticker.upper() in QUERIES:
        return QUERIES[ticker.upper()]
    name = companies.name_for(ticker) if ticker.upper() in companies.MAG7 else ticker
    return f"{name} stock"


def _to_epoch(pub_date: str) -> float:
    try:
        return parsedate_to_datetime(pub_date).timestamp()
    except (TypeError, ValueError):
        return 0.0


def parse_rss(xml_text: str, ticker: str) -> List[RawItem]:
    """Parse a Google News RSS payload into RawItems (links still un-resolved).

    Pure and testable — no network. The item URL is the Google redirect; the
    publisher comes from the ``<source>`` element. ``fetch_new`` resolves the
    redirect afterwards.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items: List[RawItem] = []
    for el in root.iter("item"):
        guid = el.findtext("guid") or el.findtext("link") or ""
        link = el.findtext("link") or ""
        title = (el.findtext("title") or "").strip()
        pub = el.findtext("pubDate") or ""
        source_el = el.find("source")
        source_name = (source_el.text or "").strip() if source_el is not None else ""
        source_url = source_el.get("url", "") if source_el is not None else ""
        if not guid or not link or not title:
            continue
        # Google appends " - Publisher" to titles; strip it for a clean headline.
        if source_name and title.endswith(f" - {source_name}"):
            title = title[: -(len(source_name) + 3)].strip()
        items.append(
            RawItem(
                source="google_news",
                source_item_id=guid,
                ticker=ticker.upper(),
                tier=Tier.WIRE,
                headline=title,
                url=link,
                publisher=source_name,
                published_at=_to_epoch(pub),
                payload={"source_url": source_url, "google_link": link},
            )
        )
    return items


class GoogleNewsSource(Source):
    name = "google_news"
    tier = Tier.WIRE

    def __init__(
        self, whitelist_provider: Callable[[], List[str]], timeout: float = 12.0
    ) -> None:
        # A provider (not a static list) so owner-approved publishers added at
        # runtime are honoured without a restart.
        self._whitelist_provider = whitelist_provider
        self._timeout = timeout
        self._headers = {"User-Agent": "Mozilla/5.0 (compatible; mag7bot/1.0)"}

    async def _resolve(self, client: httpx.AsyncClient, url: str) -> str:
        """Best-effort: follow the Google redirect to the canonical publisher
        URL. Falls back to the original link if resolution stays on Google."""
        try:
            resp = await client.get(url, follow_redirects=True)
            final = str(resp.url)
            host = (urlparse(final).hostname or "").lower()
            if host and "google." not in host:
                return final
        except httpx.HTTPError:
            pass
        return url

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        out: List[RawItem] = []
        allow = self._whitelist_provider()
        async with httpx.AsyncClient(
            headers=self._headers, timeout=self._timeout, follow_redirects=True
        ) as client:
            for ticker in tickers:
                params = {
                    "q": _query(ticker),
                    "hl": "en-US",
                    "gl": "US",
                    "ceid": "US:en",
                }
                try:
                    resp = await client.get(RSS_URL, params=params)
                    resp.raise_for_status()
                    parsed = parse_rss(resp.text, ticker)
                except httpx.HTTPError:
                    continue
                for item in parsed:
                    if is_seen(self.name, item.source_item_id):
                        continue
                    # F11(a): drop items whose publisher isn't whitelisted —
                    # also bounds how many redirects we resolve.
                    if not whitelist.is_approved(item, allow):
                        continue
                    # F11(b): resolve the redirect to the canonical publisher.
                    item.url = await self._resolve(client, item.url)
                    out.append(item)
        return out
