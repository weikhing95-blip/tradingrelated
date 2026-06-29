"""Best-effort price-reaction lookup (Finnhub /quote) with a short TTL cache.

Returns the day's signed percent change (e.g. "+2.3%"); the formatter renders
it as "$TICKER (+2.3%)". Failures are swallowed — a missing quote must never
block or delay a push. A 60s per-symbol cache keeps the firehose from
hammering the Finnhub rate limit.
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

import httpx

QUOTE_URL = "https://finnhub.io/api/v1/quote"
_TTL = 60.0  # seconds
_CACHE: Dict[str, Tuple[float, str]] = {}


async def price_move(symbol: str, api_key: str, now: Optional[float] = None) -> str:
    """Return the day's signed percent change as a short string (e.g. "+2.3%"),
    or "" if unavailable. Uses Finnhub's ``dp`` (percent change on the day)."""
    if not symbol or not api_key:
        return ""
    now = time.time() if now is None else now
    sym = symbol.upper()
    cached = _CACHE.get(sym)
    if cached and now - cached[0] < _TTL:
        return cached[1]

    result = ""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(QUOTE_URL, params={"symbol": sym, "token": api_key})
            resp.raise_for_status()
            data = resp.json()
        dp = data.get("dp")
        if isinstance(dp, (int, float)) and dp != 0:
            result = f"{dp:+.1f}%"
    except (httpx.HTTPError, ValueError, TypeError):
        result = ""
    _CACHE[sym] = (now, result)
    return result
