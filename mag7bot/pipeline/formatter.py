"""Render events into the PRD §6 message format.

    {emoji} {TICKER} · {EVENT TYPE}
    {one-line summary, ≤200 chars}
    📄 Source: {tier — source name}
    🔗 {publisher}  [· {publisher} if cross-confirmed]   ← hyperlinked
    [⚠️ unconfirmed | ✅ cross-confirmed (N)]
    🕒 {DD Mon, HH:MM SGT}

Messages are emitted as **Telegram HTML** (publishers send with parse_mode=HTML)
so links are hyperlinked behind a short label (the publisher domain, or the
source name when the URL is an aggregator redirect) instead of dumping long raw
URLs. All dynamic text is HTML-escaped.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Iterable, List
from urllib.parse import urlparse

from ..config import SGT
from ..schemas import Event, Tier

_TIER_NAME = {
    Tier.PRIMARY: "Tier 1",
    Tier.WIRE: "Tier 2",
    Tier.AGGREGATED: "Tier 3",
}


def _esc(text: str) -> str:
    """Escape visible text for Telegram HTML (leave quotes as-is)."""
    return html.escape(text, quote=False)


def _host(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _anchor_label(url: str, source_name: str = "") -> str:
    """A short, human label for a link. Prefer the publisher domain; for
    aggregator redirect URLs (news.google.com) fall back to the source name so
    we never show the ugly wrapper."""
    host = _host(url)
    if host and "google." not in host:
        return host
    return source_name or "link"


def link_html(url: str, source_name: str = "") -> str:
    label = _anchor_label(url, source_name)
    return f'<a href="{html.escape(url, quote=True)}">{_esc(label)}</a>'


def _sgt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=SGT).strftime("%d %b, %H:%M SGT")


def _status_line(event: Event) -> str:
    if event.confirmed_count >= 2:
        return f"✅ cross-confirmed ({event.confirmed_count})"
    if event.unconfirmed:
        return "⚠️ unconfirmed"
    return ""


def format_alert(event: Event) -> str:
    """Render a single event, content-forward (bite-size summary first), HTML.

        {emoji} {summary} ${TICKER}
        📄 {Tier} — {source}  ·  🔗 {publisher link(s)}
        [status · ] 🕒 {DD Mon, HH:MM SGT}
    """
    source = event.source_name or _TIER_NAME[event.tier]
    cashtag = f"${_esc(event.ticker.upper())}"
    lines = [f"{event.type.emoji} {_esc(event.summary)} {cashtag}"]

    meta = f"📄 {_TIER_NAME[event.tier]} — {_esc(source)}"
    if event.links:
        anchors = " · ".join(link_html(u, event.source_name) for u in event.links)
        meta += f"  ·  🔗 {anchors}"
    lines.append(meta)

    status = _status_line(event)
    tail = f"{status} · " if status else ""
    lines.append(f"{tail}🕒 {_sgt_time(event.ts)}")
    return "\n".join(lines)


def format_digest(
    events: Iterable[Event], all_tickers: List[str], now: float
) -> str:
    """Compile the daily digest (PRD F7), HTML-formatted."""
    events = list(events)
    by_ticker: dict[str, List[Event]] = {t: [] for t in all_tickers}
    for ev in events:
        by_ticker.setdefault(ev.ticker, []).append(ev)

    header = f"📰 Daily Digest — {datetime.fromtimestamp(now, tz=SGT):%d %b %Y}"
    blocks: List[str] = [header, ""]
    for ticker in all_tickers:
        items = by_ticker.get(ticker, [])
        if not items:
            blocks.append(f"• ${_esc(ticker.upper())}: no material events")
            continue
        blocks.append(f"• ${_esc(ticker.upper())}")
        for ev in sorted(items, key=lambda e: e.ts):
            status = _status_line(ev)
            tag = f" [{status}]" if status else ""
            link = f" — 🔗 {link_html(ev.links[0], ev.source_name)}" if ev.links else ""
            blocks.append(f"    {ev.type.emoji} {_esc(ev.summary)}{link}{tag}")
    return "\n".join(blocks)
