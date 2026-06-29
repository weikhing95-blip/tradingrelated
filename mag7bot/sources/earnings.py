"""Earnings source — upcoming preview + actuals vs estimates (Finnhub calendar).

Two emissions per fiscal quarter, off Finnhub's ``/calendar/earnings`` (the
existing Finnhub key):

  Preview (a few days before the report, once):

    $AAPL Q3 2026 earnings expected Thu 31 Jul (after close) — consensus
    EPS $1.42, Rev $85.9B est

  Actuals (when the company reports):

    $AAPL Q2 2026 earnings — EPS $1.52 vs $1.50 est ✅ beat · Rev $94.8B vs
    $94.5B est ✅ beat

The preview uses a distinct ``…-preview`` id so you still get the actuals later,
and each is emitted only once (idempotent via ``is_seen``).
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
LOOKAHEAD_DAYS = 3  # also the earnings-preview lead time (days before a report)

# Finnhub `hour` code → human label.
_HOUR_LABEL = {"bmo": "before open", "amc": "after close", "dmh": "during market hours"}


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


def _fmt_date(date_str: Optional[str]) -> str:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%a %d %b")
    except (TypeError, ValueError):
        return date_str or "soon"


def _preview_snippet(row: Dict[str, Any]) -> str:
    q, y = row.get("quarter"), row.get("year")
    period = f"Q{q} {y} earnings" if q and y else "Earnings"
    when = _fmt_date(row.get("date"))
    hour = _HOUR_LABEL.get((row.get("hour") or "").lower(), "")
    when_str = when + (f" ({hour})" if hour else "")
    parts: List[str] = []
    eps_e = row.get("epsEstimate")
    if eps_e is not None:
        parts.append(f"EPS ${eps_e:.2f}")
    rev_e = row.get("revenueEstimate")
    if rev_e is not None:
        parts.append(f"Rev {_money(rev_e)}")
    cons = (" — consensus " + ", ".join(parts) + " est") if parts else ""
    return f"{period} expected {when_str}{cons}"


def _parse(ticker: str, payload: Dict[str, Any]) -> List[RawItem]:
    items: List[RawItem] = []
    sym = ticker.upper()
    for row in payload.get("earningsCalendar", []):
        q, y = row.get("quarter"), row.get("year")
        reported = row.get("epsActual") is not None or row.get("revenueActual") is not None
        if reported:
            item_id, headline, preview = f"{sym}-{y}Q{q}", _snippet(row), False
        elif row.get("date"):
            # Scheduled but not yet reported → a one-time upcoming-earnings heads-up.
            item_id, headline, preview = f"{sym}-{y}Q{q}-preview", _preview_snippet(row), True
        else:
            continue  # no actuals and no date — nothing useful to say yet
        payload_row = dict(row)
        payload_row["preview"] = preview
        items.append(
            RawItem(
                source="earnings",
                source_item_id=item_id,
                ticker=sym,
                tier=Tier.WIRE,
                headline=headline,
                url=f"https://finnhub.io/quote/{sym}",
                publisher="Finnhub Earnings",
                published_at=time.time(),
                payload=payload_row,
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
