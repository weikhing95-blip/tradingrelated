"""Finnhub insider-transactions source — large Form 4 open-market trades.

Polls Finnhub's /stock/insider-transactions per ticker and emits a RawItem for
each open-market purchase (P) or sale (S) whose dollar value exceeds
INSIDER_THRESHOLD_USD (default $1M). Smaller trades are still emitted but route
to the daily digest via materiality.py. Grants, awards, and plan-driven sales
(transactionCode not in P/S) are ignored.

Source name: "insider" — treated as structured/trusted (bypasses whitelist and
relevance gates in ingest.py). Tier 1 (these are SEC Form 4 filings via
Finnhub's aggregation layer).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from ..schemas import RawItem, Tier
from .base import SeenFn, Source

INSIDER_URL = "https://finnhub.io/api/v1/stock/insider-transactions"
LOOKBACK_DAYS = 7

# Only open-market purchases and sales are market-meaningful.
_OPEN_MARKET_CODES = {"P", "S"}


def _money(val: float) -> str:
    if val >= 1e9:
        return f"${val / 1e9:.2f}B"
    if val >= 1e6:
        return f"${val / 1e6:.1f}M"
    return f"${val:,.0f}"


def _to_epoch(date_str: str) -> float:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _parse(ticker: str, payload: Dict[str, Any]) -> List[RawItem]:
    items: List[RawItem] = []
    for row in payload.get("data", []):
        code = (row.get("transactionCode") or "").strip().upper()
        if code not in _OPEN_MARKET_CODES:
            continue
        name = (row.get("name") or "").strip()
        change = row.get("change") or 0
        price = row.get("transactionPrice") or 0.0
        filing_date = row.get("filingDate") or row.get("transactionDate") or ""
        if not name or not filing_date or change == 0:
            continue
        shares = abs(int(change))
        value_usd = shares * float(price)
        action = "bought" if change > 0 else "sold"
        headline = (
            f"{name} {action} {shares:,} {ticker.upper()} shares"
            + (f" (~{_money(value_usd)} at ${price:.2f})" if price else "")
        )
        item_id = f"{ticker.upper()}-{filing_date}-{name}-{change}"
        items.append(
            RawItem(
                source="insider",
                source_item_id=item_id,
                ticker=ticker.upper(),
                tier=Tier.PRIMARY,
                headline=headline,
                url=(
                    f"https://www.sec.gov/cgi-bin/browse-edgar"
                    f"?action=getcompany&CIK={ticker.upper()}&type=4&dateb=&owner=include&count=10"
                ),
                publisher="SEC EDGAR / Finnhub",
                published_at=_to_epoch(filing_date),
                form_type=None,
                payload={
                    "value_usd": value_usd,
                    "code": code,
                    "name": name,
                    "change": change,
                    "price": price,
                    "filing_date": filing_date,
                },
            )
        )
    return items


class InsiderSource(Source):
    name = "insider"
    tier = Tier.PRIMARY

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
                    resp = await client.get(INSIDER_URL, params=params)
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
