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

import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from .. import companies
from ..pipeline import whitelist
from ..schemas import RawItem, Tier
from .base import SeenFn, Source

RSS_URL = "https://news.google.com/rss/search"
# Google's internal RPC that turns an opaque /articles/<id> token into the real
# publisher URL (modern feeds no longer embed the link).
BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


def _gnews_article_id(url: str) -> str:
    """Extract the opaque article id from a Google News link, '' if not one."""
    m = re.search(r"/(?:rss/)?articles/([^?/]+)", url)
    return m.group(1) if m else ""


def _parse_decoding_params(html: str) -> Tuple[str, str]:
    """Pull (signature, timestamp) from a Google News article page — the two
    values the batchexecute call needs to authorise a decode. '' if absent."""
    sig = re.search(r'data-n-a-sig="([^"]+)"', html)
    ts = re.search(r'data-n-a-ts="([^"]+)"', html)
    return (sig.group(1) if sig else "", ts.group(1) if ts else "")


def _build_batch_payload(article_id: str, signature: str, timestamp: str) -> dict:
    """Form-encode the batchexecute body for the URL-decode RPC ('Fbv4je')."""
    inner = [
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
          None, None, None, None, None, 0, 1],
         "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        article_id,
        int(timestamp) if str(timestamp).isdigit() else timestamp,
        signature,
    ]
    req = ["Fbv4je", json.dumps(inner), None, "generic"]
    return {"f.req": json.dumps([[req]])}


def _parse_batch_response(text: str) -> str:
    """Extract the decoded publisher URL from a batchexecute response. The body
    is anti-JSON-prefixed and chunked; the URL sits in the 'Fbv4je' row's nested
    JSON string. Returns '' on any shape mismatch."""
    for chunk in text.split("\n"):
        chunk = chunk.strip()
        if not chunk.startswith("[") or "Fbv4je" not in chunk:
            continue
        try:
            data = json.loads(chunk)
        except ValueError:
            continue
        for row in data:
            if (
                isinstance(row, list)
                and len(row) > 2
                and row[1] == "Fbv4je"
                and isinstance(row[2], str)
            ):
                try:
                    inner = json.loads(row[2])
                except ValueError:
                    continue
                if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], str):
                    if inner[1].startswith("http"):
                        return inner[1]
    return ""


async def _decode_gnews_url(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Best-effort: resolve an opaque Google News link to the real publisher URL
    via Google's batchexecute RPC. Returns None on any failure (caller falls back
    to the redirect/original link), so a change to Google's scheme degrades to
    'skip', never an error."""
    article_id = _gnews_article_id(url)
    if not article_id:
        return None
    try:
        page = await client.get(f"https://news.google.com/rss/articles/{article_id}")
        signature, timestamp = _parse_decoding_params(page.text)
        if not signature or not timestamp:
            return None
        resp = await client.post(
            BATCHEXECUTE_URL,
            data=_build_batch_payload(article_id, signature, timestamp),
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        decoded = _parse_batch_response(resp.text)
        return decoded or None
    except httpx.HTTPError:
        return None

# Per-ticker query strings (tuned for relevance; whitelist does the rest).
QUERIES = {
    "AAPL": "Apple Inc stock",
    "MSFT": "Microsoft stock",
    "GOOGL": "Alphabet Google stock",
    "AMZN": "Amazon.com stock",
    "NVDA": "NVIDIA stock",
    "META": "Meta Platforms stock",
    "TSLA": "Tesla stock",
    "MU": "Micron Technology stock",
    "PLTR": "Palantir Technologies stock",
}


def _query(ticker: str) -> str:
    if ticker.upper() in QUERIES:
        return QUERIES[ticker.upper()]
    name = companies.name_for(ticker) if ticker.upper() in companies.ALL_COMPANIES else ticker
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
        """Best-effort: resolve a Google News link to the canonical publisher URL.

        Two strategies, in order: (1) follow the redirect (works for legacy
        links); (2) if that stays on Google (modern opaque-token links), decode
        via the batchexecute RPC. Falls back to the original link if both fail —
        the article fetcher then skips the un-resolved Google host."""
        try:
            resp = await client.get(url, follow_redirects=True)
            final = str(resp.url)
            host = (urlparse(final).hostname or "").lower()
            if host and "google." not in host:
                return final
        except httpx.HTTPError:
            pass
        decoded = await _decode_gnews_url(client, url)
        if decoded:
            host = (urlparse(decoded).hostname or "").lower()
            if host and "google." not in host:
                return decoded
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
