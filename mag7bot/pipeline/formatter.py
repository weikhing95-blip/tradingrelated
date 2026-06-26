"""Render events into the PRD §6 message format.

    {emoji} {TICKER} · {EVENT TYPE}
    {one-line summary, ≤200 chars}
    📄 Source: {tier — source name}
    🔗 {canonical link}  [· 🔗 {second link if cross-confirmed}]
    [⚠️ unconfirmed | ✅ cross-confirmed (N)]
    🕒 {DD Mon, HH:MM SGT}

Also compiles the daily digest (PRD F7), which lists watched tickers with no
events as "no material events". Output is plain text (Telegram-safe); links are
sent as-is so Telegram auto-links them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List

from ..config import SGT
from ..schemas import Event, Tier

_TIER_NAME = {
    Tier.PRIMARY: "Tier 1",
    Tier.WIRE: "Tier 2",
    Tier.AGGREGATED: "Tier 3",
}


def _sgt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=SGT).strftime("%d %b, %H:%M SGT")


def _status_line(event: Event) -> str:
    if event.confirmed_count >= 2:
        return f"✅ cross-confirmed ({event.confirmed_count})"
    if event.unconfirmed:
        return "⚠️ unconfirmed"
    return ""


def format_alert(event: Event) -> str:
    """Render a single event as an instant-push / digest-item message."""
    source = event.source_name or _TIER_NAME[event.tier]
    lines = [
        f"{event.type.emoji} {event.ticker} · {event.type.display}",
        event.summary,
        f"📄 Source: {_TIER_NAME[event.tier]} — {source}",
    ]
    links = " · ".join(f"🔗 {url}" for url in event.links) if event.links else ""
    if links:
        lines.append(links)
    status = _status_line(event)
    if status:
        lines.append(status)
    lines.append(f"🕒 {_sgt_time(event.ts)}")
    return "\n".join(lines)


def format_digest(
    events: Iterable[Event], all_tickers: List[str], now: float
) -> str:
    """Compile the daily digest (PRD F7)."""
    events = list(events)
    by_ticker: dict[str, List[Event]] = {t: [] for t in all_tickers}
    for ev in events:
        by_ticker.setdefault(ev.ticker, []).append(ev)

    header = f"📰 Daily Digest — {datetime.fromtimestamp(now, tz=SGT):%d %b %Y}"
    blocks: List[str] = [header, ""]
    for ticker in all_tickers:
        items = by_ticker.get(ticker, [])
        if not items:
            blocks.append(f"• {ticker}: no material events")
            continue
        blocks.append(f"• {ticker}:")
        for ev in sorted(items, key=lambda e: e.ts):
            status = _status_line(ev)
            tag = f" [{status}]" if status else ""
            blocks.append(f"    {ev.type.emoji} {ev.type.display}: {ev.summary}{tag}")
            if ev.links:
                blocks.append(f"      🔗 {ev.links[0]}")
    return "\n".join(blocks)
