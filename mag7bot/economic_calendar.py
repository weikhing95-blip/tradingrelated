"""Economic-calendar preview — a daily heads-up of high-impact macro events.

Distinct from the ``macro`` source (which posts a number when it's *released*),
this looks *forward*: once a day it posts the day's scheduled high-impact releases
with their consensus forecast and previous reading — the "today's breaking macro"
reminder. Market-wide (no ticker), tagged like the rest of the macro coverage.

Data: the free FairEconomy / ForexFactory weekly calendar JSON (no API key).
Each entry carries ``country`` (currency code), ``title``, ``date`` (ISO‑8601
with offset), ``impact`` (High/Medium/Low/Holiday), ``forecast`` and ``previous``.
Best-effort: any fetch/parse failure just skips the day's digest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

import httpx

from .config import SGT

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Currency → flag, for a clean SM-News-style header. Unknown codes fall back to
# the bare code.
_FLAGS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CNY": "🇨🇳",
    "AUD": "🇦🇺", "CAD": "🇨🇦", "CHF": "🇨🇭", "NZD": "🇳🇿",
}


@dataclass
class CalEvent:
    currency: str
    title: str
    when: float          # Unix epoch (UTC); 0.0 if unknown/all-day
    impact: str          # "High" / "Medium" / "Low" / "Holiday"
    forecast: str
    previous: str


def _to_epoch(iso: str) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def parse_calendar(text: str) -> List[CalEvent]:
    """Parse the FairEconomy weekly JSON into CalEvents. Pure; [] on bad input."""
    try:
        rows = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    out: List[CalEvent] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(
            CalEvent(
                currency=(r.get("country") or "").upper(),
                title=(r.get("title") or "").strip(),
                when=_to_epoch(r.get("date") or ""),
                impact=(r.get("impact") or "").strip().title(),
                forecast=(r.get("forecast") or "").strip(),
                previous=(r.get("previous") or "").strip(),
            )
        )
    return out


def events_for_day(
    events: Sequence[CalEvent],
    day: datetime,
    currencies: Sequence[str],
    impacts: Sequence[str],
) -> List[CalEvent]:
    """Filter to events on ``day`` (its SGT calendar date) matching the allowed
    currencies and impact levels, sorted by time."""
    allow_ccy = {c.upper() for c in currencies}
    allow_imp = {i.title() for i in impacts}
    target = day.astimezone(SGT).date()
    kept = []
    for e in events:
        if e.currency not in allow_ccy or e.impact not in allow_imp:
            continue
        if not e.when:
            continue
        if datetime.fromtimestamp(e.when, tz=SGT).date() != target:
            continue
        kept.append(e)
    return sorted(kept, key=lambda e: e.when)


def _flag(currency: str) -> str:
    return _FLAGS.get(currency, "")


def format_calendar_digest(events: Sequence[CalEvent], day: datetime) -> str:
    """SM-News-style daily macro preview (Telegram HTML). One block per event,
    sorted by time, rendered in SGT. Returns "" when there's nothing to post."""
    if not events:
        return ""
    header = f"📅 <b>{day.astimezone(SGT):%a %d %b} — high-impact macro</b>"
    lines: List[str] = [header, ""]
    for e in events:
        t = datetime.fromtimestamp(e.when, tz=SGT).strftime("%H:%M")
        flag = _flag(e.currency)
        head = f"{flag} {e.currency}".strip()
        lines.append(f"{head} · {e.title} — 🕒 {t} SGT")
        prev = e.previous or "—"
        est = e.forecast or "—"
        lines.append(f"   prev {prev} · est {est}")
    lines.append("")
    lines.append("─────────────────")
    lines.append("Consensus via ForexFactory · times SGT")
    lines.append("ℹ️ Informational only — not financial advice.")
    return "\n".join(lines)


async def fetch_calendar(timeout: float = 12.0) -> List[CalEvent]:
    """Fetch + parse the weekly calendar. [] on any failure (best-effort)."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; mag7bot/1.0)"}
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            resp = await client.get(CALENDAR_URL)
            resp.raise_for_status()
            return parse_calendar(resp.text)
    except (httpx.HTTPError, ValueError):
        return []


async def build_daily_preview(
    day: datetime, currencies: Sequence[str], impacts: Sequence[str]
) -> str:
    """Fetch the calendar and render the preview for ``day``. "" if nothing."""
    events = await fetch_calendar()
    todays = events_for_day(events, day, currencies, impacts)
    return format_calendar_digest(todays, day)
