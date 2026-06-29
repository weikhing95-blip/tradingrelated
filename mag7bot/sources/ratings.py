"""Structured analyst rating-changes source (Finnhub upgrade/downgrade).

Polls Finnhub's /stock/upgrade-downgrade per ticker and emits a RawItem for each
genuine rating change — upgrades, downgrades, and new-coverage initiations.
Plain "maintains"/reiterations are skipped to keep noise down.

This is more reliable than guessing rating actions from news headlines: it
carries the firm name, the from→to grades, and a structured action, so the
summary is precise and materiality is calibrated (real up/downgrades promote to
MATERIAL via the analyst hints in materiality.py).

Source name: "ratings" — structured/trusted (bypasses whitelist + relevance).
Tier 3 (aggregated analyst feed).
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx

from ..schemas import RawItem, Tier
from .base import SeenFn, Source

RATINGS_URL = "https://finnhub.io/api/v1/stock/upgrade-downgrade"

# Finnhub `action` → headline verb. "main" (maintain/reiterate) is dropped.
_VERB = {
    "up": "upgrades",
    "down": "downgrades",
    "init": "initiates coverage on",
}


def _parse(ticker: str, rows: List[Dict[str, Any]]) -> List[RawItem]:
    items: List[RawItem] = []
    for row in rows:
        action = (row.get("action") or "").strip().lower()
        verb = _VERB.get(action)
        if not verb:  # skip "main"/unknown — reiterations aren't news
            continue
        firm = (row.get("company") or "").strip()
        to_grade = (row.get("toGrade") or "").strip()
        from_grade = (row.get("fromGrade") or "").strip()
        grade_time = row.get("gradeTime") or 0
        if not firm or not to_grade:
            continue

        sym = ticker.upper()
        if action == "init":
            headline = f"{firm} initiates coverage on {sym} at {to_grade}"
        else:
            headline = f"{firm} {verb} {sym} to {to_grade}"
            if from_grade:
                headline += f" (from {from_grade})"

        try:
            ts = float(grade_time)
        except (TypeError, ValueError):
            ts = 0.0
        item_id = f"{sym}-{int(ts)}-{firm}-{to_grade}".replace(" ", "_")
        items.append(
            RawItem(
                source="ratings",
                source_item_id=item_id,
                ticker=sym,
                tier=Tier.AGGREGATED,
                headline=headline,
                url=f"https://finnhub.io/quote/{sym}",
                publisher=firm or "Analyst",
                published_at=ts,
                form_type=None,
                payload={"action": action, "from": from_grade, "to": to_grade, "firm": firm},
            )
        )
    return items


class AnalystRatingsSource(Source):
    name = "ratings"
    tier = Tier.AGGREGATED

    def __init__(self, api_key: str, timeout: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        out: List[RawItem] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for ticker in tickers:
                params = {"symbol": ticker.upper(), "token": self._api_key}
                try:
                    resp = await client.get(RATINGS_URL, params=params)
                    resp.raise_for_status()
                    rows = resp.json()
                except (httpx.HTTPError, ValueError):
                    continue
                if not isinstance(rows, list):
                    continue
                for item in _parse(ticker, rows):
                    if not is_seen(self.name, item.source_item_id):
                        out.append(item)
        return out
