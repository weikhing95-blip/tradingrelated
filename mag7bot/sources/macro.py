"""Macro source — US economic releases via FRED (PRD: CPI, PCE, PPI, claims).

Polls a configured set of FRED series and emits a bite-size release line when a
new observation lands:

    📊 US Core CPI (May 2026) — +0.3% MoM, +3.2% YoY  (prior +0.2% / +3.1%)
    📊 US Initial Jobless Claims (21 Jun) — 233,000  (+9,000 WoW)

These are economy-wide (no ticker) — stamped ``ticker="MACRO"``, exempt from the
whitelist/relevance gates, treated as critical (instant push). Tier 1 (the data
originates from BLS/BEA; FRED is the access layer). Requires ``FRED_API_KEY`` —
the source is only wired in when that key is present.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from ..config import MACRO_SERIES
from ..schemas import EventType, RawItem, Tier
from .base import SeenFn, Source

OBS_URL = "https://api.stlouisfed.org/fred/series/observations"


def _fmt_month(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%b %Y")
    except ValueError:
        return date_str


def _fmt_day(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d %b")
    except ValueError:
        return date_str


def _pct(cur: float, prev: float) -> Optional[float]:
    if prev == 0:
        return None
    return (cur / prev - 1.0) * 100.0


def _summary(series: Dict[str, str], obs: List[Dict[str, Any]]) -> Optional[str]:
    """Build a release line from observations (newest first)."""
    vals: List[tuple[str, float]] = []
    for o in obs:
        v = o.get("value")
        if v in (None, ".", ""):
            continue
        try:
            vals.append((o["date"], float(v)))
        except (ValueError, KeyError):
            continue
    if not vals:
        return None

    label = series["label"]
    date, latest = vals[0]
    if series["kind"] == "level":
        prior = vals[1][1] if len(vals) > 1 else None
        if series.get("freq") == "monthly":
            date_label = _fmt_month(date)
            change = f"  ({latest - prior:+,.0f} MoM)" if prior is not None else ""
        else:
            date_label = _fmt_day(date)
            change = f"  ({latest - prior:+,.0f} WoW)" if prior is not None else ""
        return f"{label} ({date_label}) — {latest:,.0f}{change}"

    # index → MoM and YoY percent changes
    mom = _pct(latest, vals[1][1]) if len(vals) > 1 else None
    yoy = _pct(latest, vals[12][1]) if len(vals) > 12 else None
    pieces = []
    if mom is not None:
        pieces.append(f"{mom:+.1f}% MoM")
    if yoy is not None:
        pieces.append(f"{yoy:+.1f}% YoY")
    change = ", ".join(pieces) if pieces else f"{latest:.1f}"
    return f"{label} ({_fmt_month(date)}) — {change}"


def latest_date(obs: List[Dict[str, Any]]) -> Optional[str]:
    for o in obs:
        if o.get("value") not in (None, ".", ""):
            return o.get("date")
    return None


class MacroSource(Source):
    name = "macro"
    tier = Tier.PRIMARY

    def __init__(self, api_key: str, timeout: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        # Macro is economy-wide; the watchlist tickers are irrelevant here.
        out: List[RawItem] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for series in MACRO_SERIES:
                params = {
                    "series_id": series["id"],
                    "api_key": self._api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 14,
                }
                try:
                    resp = await client.get(OBS_URL, params=params)
                    resp.raise_for_status()
                    obs = resp.json().get("observations", [])
                except (httpx.HTTPError, ValueError):
                    continue
                date = latest_date(obs)
                if not date:
                    continue
                item_id = f"{series['id']}-{date}"
                if is_seen(self.name, item_id):
                    continue
                summary = _summary(series, obs)
                if not summary:
                    continue
                out.append(
                    RawItem(
                        source="macro",
                        source_item_id=item_id,
                        ticker="MACRO",
                        tier=Tier.PRIMARY,
                        headline=summary,
                        url=f"https://fred.stlouisfed.org/series/{series['id']}",
                        publisher="FRED",
                        published_at=time.time(),  # detection time (release), not the period
                        payload={"series": series["id"], "observation_date": date},
                    )
                )
        return out
