"""Finnhub source — Tier 2/3 corporate news (PRD F2).

Queries ``/company-news`` per ticker over a short trailing window. Finnhub
returns ``headline``, ``source``, ``url``, ``datetime`` (epoch seconds) and a
stable ``id`` we use for idempotency. The query is per-ticker, so the company
arrives *with* the data — we just stamp it (PRD §9).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from ..schemas import RawItem, Tier
from .base import SeenFn, Source

COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"
LOOKBACK_DAYS = 3


def _parse(ticker: str, articles: List[Dict[str, Any]]) -> List[RawItem]:
    items: List[RawItem] = []
    for a in articles:
        article_id = a.get("id")
        url = a.get("url", "")
        headline = a.get("headline", "")
        if article_id is None or not url or not headline:
            continue
        items.append(
            RawItem(
                source="finnhub",
                source_item_id=str(article_id),
                ticker=ticker.upper(),
                tier=Tier.WIRE,
                headline=headline.strip(),
                body=str(a.get("summary", "") or "").strip(),
                url=url,
                publisher=str(a.get("source", "")).strip(),
                published_at=float(a.get("datetime", 0) or 0),
                form_type=None,
                payload={
                    "id": article_id,
                    "category": a.get("category"),
                    "source": a.get("source"),
                    "summary": a.get("summary"),
                    "datetime": a.get("datetime"),
                },
            )
        )
    return items


class FinnhubSource(Source):
    name = "finnhub"
    tier = Tier.WIRE

    def __init__(self, api_key: str, timeout: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def _window(self) -> tuple[str, str]:
        today = datetime.now(timezone.utc).date()
        frm = today - timedelta(days=LOOKBACK_DAYS)
        return frm.isoformat(), today.isoformat()

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        frm, to = self._window()
        out: List[RawItem] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for ticker in tickers:
                params = {
                    "symbol": ticker.upper(),
                    "from": frm,
                    "to": to,
                    "token": self._api_key,
                }
                try:
                    resp = await client.get(COMPANY_NEWS_URL, params=params)
                    resp.raise_for_status()
                    articles = resp.json()
                except (httpx.HTTPError, ValueError):
                    continue
                if not isinstance(articles, list):
                    continue
                for item in _parse(ticker, articles):
                    if not is_seen(self.name, item.source_item_id):
                        out.append(item)
        return out
