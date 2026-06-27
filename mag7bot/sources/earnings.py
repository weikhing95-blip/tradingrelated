"""Earnings source — actuals vs estimates (Finnhub earnings calendar).

Turns a reported quarter into a bite-size snippet:

    $AAPL Q2 2026 earnings — EPS $1.52 vs $1.50 est ✅ beat · Rev $94.8B vs
    $94.5B est ✅ beat

Uses Finnhub's ``/calendar/earnings`` (the existing Finnhub key). An item is
emitted once per (symbol, fiscal quarter) when the actuals are populated, so we
post when a company *reports* — not when it's merely scheduled.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..schemas import RawItem, Tier
from .base import SeenFn, Source

EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"
LOOKBACK_DAYS = 4
LOOKAHEAD_DAYS = 1


def _money(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    a = abs(value)
    if a >= 1e9:
        return f"${value / 1e9:.2f}B"
    if a >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:,.0f}"


def _beat(actual: Optional[float], estimate: Optional[float]) -> str:
    if actual is None or estimate is None:
        return ""
    if actual > estimate:
        return "✅ beat"
    if actual < estimate:
        return "❌ miss"
    return "➖ in line"


def _snippet(row: Dict[str, Any]) -> str:
    # The ticker is shown as the $cashtag by the formatter, so don't repeat it.
    q = row.get("quarter")
    y = row.get("year")
    period = f"Q{q} {y} earnings" if q and y else "earnings"
    parts: List[str] = []
    eps_a, eps_e = row.get("epsActual"), row.get("epsEstimate")
    if eps_a is not None:
        tag = _beat(eps_a, eps_e)
        est = f" vs ${eps_e:.2f} est" if eps_e is not None else ""
        parts.append(f"EPS ${eps_a:.2f}{est}{(' ' + tag) if tag else ''}")
    rev_a, rev_e = row.get("revenueActual"), row.get("revenueEstimate")
    if rev_a is not None:
        tag = _beat(rev_a, rev_e)
        est = f" vs {_money(rev_e)} est" if rev_e is not None else ""
        parts.append(f"Rev {_money(rev_a)}{est}{(' ' + tag) if tag else ''}")
    detail = " · ".join(parts) if parts else "results reported"
    return f"{period} — {detail}"


def _parse(ticker: str, payload: Dict[str, Any]) -> List[RawItem]:
    items: List[RawItem] = []
    for row in payload.get("earningsCalendar", []):
        # Only emit once actuals are in (the company has reported).
        if row.get("epsActual") is None and row.get("revenueActual") is None:
            continue
        q, y = row.get("quarter"), row.get("year")
        item_id = f"{ticker.upper()}-{y}Q{q}"
        items.append(
            RawItem(
                source="earnings",
                source_item_id=item_id,
                ticker=ticker.upper(),
                tier=Tier.WIRE,
                headline=_snippet(row),
                url=f"https://finnhub.io/quote/{ticker.upper()}",
                publisher="Finnhub Earnings",
                published_at=time.time(),
                payload=row,
            )
        )
    return items


class EarningsSource(Source):
    name = "earnings"
    tier = Tier.WIRE

    def __init__(self, api_key: str, timeout: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def _window(self) -> tuple[str, str]:
        today = datetime.now(timezone.utc).date()
        return (
            (today - timedelta(days=LOOKBACK_DAYS)).isoformat(),
            (today + timedelta(days=LOOKAHEAD_DAYS)).isoformat(),
        )

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
                    resp = await client.get(EARNINGS_URL, params=params)
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                for item in _parse(ticker, payload):
                    if not is_seen(self.name, item.source_item_id):
                        out.append(item)
        return out
