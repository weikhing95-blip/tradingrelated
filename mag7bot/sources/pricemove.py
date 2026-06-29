"""Unusual price-move source — free, no API key (Yahoo Finance chart API).

Flags a watched name when its move on the day is **unusually large relative to
its own recent behaviour** — specifically when |today's move| is at least
``multiplier`` × the average absolute daily move over the trailing two weeks,
*and* clears a ``min_pct`` floor so a normally-flat stock isn't flagged on a
tiny wobble. This is a *computed* market signal, deliberately distinct from the
auto-generated "X Moves 5%" news articles the relevance filter drops — those are
stale, substance-free filler; this is a live, baseline-relative anomaly.

Source name: "pricemove" — structured/trusted (bypasses whitelist + relevance).
Data: Yahoo's public chart endpoint (``query1.finance.yahoo.com/v8/finance/chart``)
returns the trailing daily closes (for the baseline) and the current price in one
call — no key, and Finnhub's historical candles are now a paid endpoint.

One alert per ticker per trading day: the ``source_item_id`` is keyed on the
date, so a move that keeps growing intraday isn't re-posted every poll.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx

from ..schemas import RawItem, Tier
from .base import SeenFn, Source

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _abs_daily_returns(closes: List[float]) -> List[float]:
    """Absolute day-over-day returns from a series of daily closes (oldest→newest).
    Pairs with a missing/zero close are skipped."""
    out: List[float] = []
    for prev, cur in zip(closes, closes[1:]):
        if prev and cur and prev > 0:
            out.append(abs(cur / prev - 1.0))
    return out


def assess_move(
    prior_closes: List[float],
    current: Optional[float],
    prev_close: Optional[float],
    *,
    multiplier: float,
    min_pct: float,
    lookback: int,
) -> Optional[Tuple[float, float, float]]:
    """Decide whether today's move is an alert-worthy anomaly. Pure.

    ``prior_closes`` are completed daily closes (oldest→newest) NOT including
    today; ``current`` is the latest price; ``prev_close`` is yesterday's close.
    Returns ``(move_fraction, baseline_fraction, ratio)`` when the move clears
    both the ``multiplier``×baseline bar and the ``min_pct`` floor, else None.
    """
    if not current or not prev_close or prev_close <= 0:
        return None
    returns = _abs_daily_returns(prior_closes)[-lookback:]
    if len(returns) < 3:  # not enough history to form a baseline
        return None
    baseline = sum(returns) / len(returns)
    if baseline <= 0:
        return None
    move = current / prev_close - 1.0
    ratio = abs(move) / baseline
    if abs(move) * 100.0 >= min_pct and ratio >= multiplier:
        return move, baseline, ratio
    return None


def parse_chart(data: dict) -> Tuple[List[float], Optional[float], Optional[float], float]:
    """Extract (closes, current_price, prev_close, as_of_epoch) from a Yahoo chart
    JSON payload. Pure; returns ([], None, None, 0.0) on any shape mismatch."""
    try:
        result = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return [], None, None, 0.0
    meta = result.get("meta") or {}
    current = meta.get("regularMarketPrice")
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    as_of = float(meta.get("regularMarketTime") or 0.0)
    closes: List[float] = []
    try:
        raw = result["indicators"]["quote"][0]["close"]
        closes = [c for c in raw if isinstance(c, (int, float))]
    except (KeyError, IndexError, TypeError):
        closes = []
    return closes, current, prev_close, as_of


def _trade_date(as_of_epoch: float) -> str:
    """UTC date string for the once-per-day idempotency key (a single trading
    session never spans two UTC dates for US markets within market hours)."""
    ts = as_of_epoch if as_of_epoch > 0 else datetime.now(tz=timezone.utc).timestamp()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def build_item(
    ticker: str, move: float, ratio: float, as_of_epoch: float
) -> RawItem:
    """Construct the RawItem for an alert-worthy move. The headline is the clean,
    ticker-free summary line (line 1 of the alert already shows ``$TICKER``)."""
    pct = abs(move) * 100.0
    direction = "up" if move > 0 else "down"
    headline = (
        f"{direction} {pct:.1f}% on the day — "
        f"{ratio:.1f}× its 2-week average daily move"
    )
    published = as_of_epoch if as_of_epoch > 0 else datetime.now(tz=timezone.utc).timestamp()
    return RawItem(
        source="pricemove",
        source_item_id=f"{ticker.upper()}-{_trade_date(as_of_epoch)}",
        ticker=ticker.upper(),
        tier=Tier.WIRE,
        headline=headline,
        url=f"https://finance.yahoo.com/quote/{ticker.upper()}",
        publisher="Yahoo Finance",
        published_at=published,
        form_type=None,
        payload={
            "move_pct": round(move * 100.0, 2),
            "ratio": round(ratio, 2),
            "direction": direction,
        },
    )


class PriceMoveSource(Source):
    name = "pricemove"
    tier = Tier.WIRE

    def __init__(
        self,
        *,
        multiplier: float = 2.0,
        min_pct: float = 3.0,
        lookback: int = 10,
        timeout: float = 12.0,
    ) -> None:
        self._multiplier = multiplier
        self._min_pct = min_pct
        self._lookback = lookback
        self._timeout = timeout
        self._headers = {"User-Agent": "Mozilla/5.0 (compatible; mag7bot/1.0)"}
        # 1mo of daily candles is plenty for a 2-week (≈10-session) baseline.
        self._params = {"range": "1mo", "interval": "1d"}

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        out: List[RawItem] = []
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            for ticker in tickers:
                try:
                    resp = await client.get(
                        CHART_URL.format(symbol=ticker.upper()), params=self._params
                    )
                    resp.raise_for_status()
                    closes, current, prev_close, as_of = parse_chart(resp.json())
                except (httpx.HTTPError, ValueError):
                    continue  # best-effort per ticker; a failure never aborts the rest
                # The last close in the series is today's (partial) candle — exclude
                # it so the baseline is built from completed prior sessions only.
                prior = closes[:-1] if closes else []
                verdict = assess_move(
                    prior, current, prev_close,
                    multiplier=self._multiplier,
                    min_pct=self._min_pct,
                    lookback=self._lookback,
                )
                if verdict is None:
                    continue
                move, _baseline, ratio = verdict
                item = build_item(ticker, move, ratio, as_of)
                if not is_seen(self.name, item.source_item_id):
                    out.append(item)
        return out
