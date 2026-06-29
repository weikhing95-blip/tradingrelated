"""Alpaca (Benzinga) news source — real-time professional wire.

Alpaca's free News API streams Benzinga's real-time financial news, which —
unlike a headline-only RSS feed — carries the **full article body** (``content``)
plus a concise ``summary`` and an authoritative list of tagged ``symbols``. That
makes it the richest text source the bot has: the summarizer gets real substance
without any article-fetch/extraction guesswork.

Source name: "alpaca" — treated as structured/trusted (bypasses the publisher
whitelist and the headline-relevance gate), because Benzinga is a curated wire
and tags each story's tickers itself (more reliable than our alias matching). We
still apply the low-quality/filler filter here so Benzinga's "Why X Stock Is
Moving" pieces don't leak in.

Auth: ``APCA-API-KEY-ID`` + ``APCA-API-SECRET-KEY`` headers (free market-data
keys; no funded account needed). Endpoint: ``GET data.alpaca.markets/v1beta1/news``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import httpx

from .. import article
from ..pipeline import relevance
from ..schemas import RawItem, Tier
from .base import SeenFn, Source

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"


def _to_epoch(iso: str) -> float:
    """Parse an ISO-8601 timestamp (e.g. '2026-06-29T11:30:00Z') to epoch secs."""
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _body_text(item: dict) -> str:
    """Best text for the summarizer: cleaned full ``content`` HTML, falling back
    to Benzinga's ``summary`` when the content is empty or too thin."""
    cleaned = article.extract_text(item.get("content") or "")
    if cleaned:
        return cleaned
    return (item.get("summary") or "").strip()


def _parse(payload: dict, tickers: List[str]) -> List[RawItem]:
    """Turn an Alpaca news payload into RawItems — one per (story, watchlist
    symbol) pair. Pure and testable; no network. Filler/opinion headlines are
    dropped (Benzinga carries some), and the story's symbols are trusted as-is."""
    watch = {t.upper() for t in tickers}
    items: List[RawItem] = []
    for story in payload.get("news", []) or []:
        headline = (story.get("headline") or "").strip()
        if not headline or relevance.is_low_quality(headline):
            continue
        story_id = story.get("id")
        if story_id is None:
            continue
        url = (story.get("url") or "").strip()
        publisher = (story.get("source") or "Benzinga").strip() or "Benzinga"
        published = _to_epoch(story.get("created_at") or story.get("updated_at") or "")
        body = _body_text(story)
        for sym in story.get("symbols", []) or []:
            sym = (sym or "").upper()
            if sym not in watch:
                continue
            items.append(
                RawItem(
                    source="alpaca",
                    # Per-(story, ticker) id: a story tagged with two watched
                    # names must not be deduped away by the seen table before the
                    # pipeline's URL dedup can collapse it into one alert.
                    source_item_id=f"{story_id}-{sym}",
                    ticker=sym,
                    tier=Tier.WIRE,
                    headline=headline,
                    body=body,
                    url=url,
                    publisher=publisher,
                    published_at=published,
                    form_type=None,
                    payload={"benzinga_id": story_id, "summary": story.get("summary", "")},
                )
            )
    return items


class AlpacaNewsSource(Source):
    name = "alpaca"
    tier = Tier.WIRE

    def __init__(self, api_key: str, secret_key: str, timeout: float = 12.0, limit: int = 50) -> None:
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }
        self._timeout = timeout
        self._limit = limit

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        if not tickers:
            return []
        params = {
            "symbols": ",".join(t.upper() for t in tickers),
            "limit": self._limit,
            "sort": "desc",
        }
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            resp = await client.get(NEWS_URL, params=params)
            resp.raise_for_status()
            items = _parse(resp.json(), tickers)
        return [it for it in items if not is_seen(self.name, it.source_item_id)]
