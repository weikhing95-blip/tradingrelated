"""Render events into the MarketBrief message format.

Per-alert layout (HTML, hyperlinks behind short labels):

    {$TICKER}
    {one-line summary, ≤200 chars}
    🔗 link  ·  [status · ] 🕒 {DD Mon, HH:MM SGT} [· session]

The first line is the ticker (the *who*). The second line is the bite-size
summary (the *what*). The third line condenses provenance, confirmation
status, and the timestamp into one row.

Daily Digest layout: critical events first, then ECONOMY (macro + Fed),
then BY COMPANY for the rest. See ``format_digest`` for details.

Messages are emitted as **Telegram HTML** (publishers send with parse_mode=HTML)
so links are hyperlinked behind a short label (the word "link") instead of
dumping long raw URLs. All dynamic text is HTML-escaped.
"""

from __future__ import annotations

import html
from datetime import datetime, time as dtime
from typing import Iterable, List
from zoneinfo import ZoneInfo

from ..config import SGT
from ..schemas import Event, EventType, Materiality, Tier

# US market hours, used for the optional "pre-mkt"/"after-hrs" tag on alerts.
_ET = ZoneInfo("America/New_York")

# Country/zone → flag for the unified macro line (GM-B2-02).
_COUNTRY_FLAGS = {
    "US": "🇺🇸", "JP": "🇯🇵", "CN": "🇨🇳", "EU": "🇪🇺", "GB": "🇬🇧", "UK": "🇬🇧",
}


def macro_line(ev: Event) -> str:
    """Unified macro render (GM-B2-02):
        {flag} {lead} — actual {a}[ vs est {c}] · prior {p}
    ``lead`` is the event's context sentence when present, else
    ``ECONOMY · indicator (period)``. A missing consensus is omitted — never a
    blank or fabricated estimate (GM-B3-03)."""
    flag = _COUNTRY_FLAGS.get((ev.economy or "").upper(), "")
    if ev.summary:
        lead = _esc(ev.summary)
    else:
        lead = " · ".join(x for x in [(ev.economy or "").upper(), _esc(ev.indicator)] if x)
        if ev.period:
            lead += f" ({_esc(ev.period)})"
    metrics: List[str] = []
    if ev.actual:
        seg = f"actual {_esc(ev.actual)}"
        if ev.consensus:
            seg += f" vs est {_esc(ev.consensus)}"
        metrics.append(seg)
    if ev.prior:
        metrics.append(f"prior {_esc(ev.prior)}")
    body = lead + (" — " + " · ".join(metrics) if metrics else "")
    return f"{flag} {body}".strip() if flag else body


def _esc(text: str) -> str:
    """Escape visible text for Telegram HTML (leave quotes as-is)."""
    return html.escape(text, quote=False)


def link_html(url: str, label: str = "link") -> str:
    """Hyperlink behind a short word (default "link") to declutter the message —
    bite-size summaries rarely need the reader to click through."""
    return f'<a href="{html.escape(url, quote=True)}">{_esc(label)}</a>'


def _links_html(links: list) -> str:
    """One link → "link"; several → "link 1 · link 2 · …"."""
    if len(links) == 1:
        return link_html(links[0])
    return " · ".join(link_html(u, f"link {i + 1}") for i, u in enumerate(links))


def _sgt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=SGT).strftime("%d %b, %H:%M SGT")


def _market_session(ts: float) -> str:
    """Return a short US market-session tag for a UTC timestamp.

    Returns ``"pre-mkt"`` (04:00–09:30 ET), ``"after-hrs"`` (16:00–20:00 ET),
    or an empty string for the regular session and overnight/weekend.
    """
    dt = datetime.fromtimestamp(ts, tz=_ET)
    if dt.weekday() >= 5:  # weekends — markets closed
        return ""
    t = dt.time()
    if dtime(4, 0) <= t < dtime(9, 30):
        return "pre-mkt"
    if dtime(9, 30) <= t < dtime(16, 0):
        return ""  # regular session — no tag needed, it's the default
    if dtime(16, 0) <= t < dtime(20, 0):
        return "after-hrs"
    return ""


def _status_token(event: Event) -> str:
    # Only the positive cross-confirmed badge is shown; the "unconfirmed" tag
    # was dropped at the owner's request to keep alerts clean.
    if event.confirmed_count >= 2:
        return f"✅ cross-confirmed ({event.confirmed_count})"
    return ""


def format_alert(event: Event) -> str:
    """Render a single event into the MarketBrief alert format.

        {$TICKER}
        {summary}
        🔗 link  ·  [status · ] 🕒 {DD Mon, HH:MM SGT} [· session]
    """
    # ── Line 1: ticker(s) — no label, no emoji, no price ───────────────────
    if event.type == EventType.MACRO or event.ticker.upper() == "MACRO":
        head = "MACRO"
    else:
        # All watchlist companies in the story (e.g. "$AAPL · $GOOGL"), capped so
        # an unusually broad piece can't produce a runaway header.
        tickers = [t.upper() for t in (event.tickers or [event.ticker]) if t]
        if not tickers:
            tickers = [event.ticker.upper()]
        head = " · ".join(f"${_esc(t)}" for t in tickers[:4])

    # ── Line 2: bite-size summary (or unified macro line for macro events) ──
    if event.economy or event.indicator:
        summary_line = macro_line(event)
    else:
        summary_line = _esc(event.summary)

    # ── Line 3: link(s) + status + timestamp (one condensed line) ─────────
    parts: List[str] = []
    if event.links:
        parts.append(f"🔗 {_links_html(event.links)}")
    elif event.source_name:
        parts.append(f"🔗 {_esc(event.source_name)}")

    status = _status_token(event)
    if status:
        parts.append(status)

    time_str = f"🕒 {_sgt_time(event.ts)}"
    session = _market_session(event.ts)
    if session:
        time_str += f" · {session}"
    parts.append(time_str)

    meta_line = "  ·  ".join(parts)
    return "\n".join([head, summary_line, meta_line])


def public_footer(handle: str = "") -> str:
    """Forward-friendly footer for public-channel alerts: a channel handle (so a
    forwarded post still points home) + a one-line disclaimer. Part of the message
    text, so it survives Telegram forwards (5A-02)."""
    tag = f"{_esc(handle)} · " if handle else ""
    return f"{tag}ℹ️ Not financial advice"


def with_public_footer(body: str, handle: str = "") -> str:
    """Append the public footer to a rendered alert body."""
    return f"{body}\n\n{public_footer(handle)}"


def format_weekly_roundup(events: Iterable[Event], now: float, handle: str = "") -> str:
    """A shareable weekly "what you missed" post: the week's top events grouped by
    tier, newest first within each tier (5A-03). ``events`` should already be the
    pre-selected top-N for the window."""
    events = list(events)
    week_end = datetime.fromtimestamp(now, tz=SGT)
    header = f"🗞 MarketBrief — This Week ({week_end:%d %b %Y})"
    blocks: List[str] = [header, ""]
    if not events:
        blocks.append("A quiet week — no headline events on the watchlist.")
    else:
        tier_order = [Tier.PRIMARY, Tier.WIRE, Tier.AGGREGATED]
        labels = {Tier.PRIMARY: "🔵 Filings & primary", Tier.WIRE: "📰 Wires",
                  Tier.AGGREGATED: "🟡 Analyst & data"}
        by_tier: dict = {}
        for ev in events:
            by_tier.setdefault(ev.tier, []).append(ev)
        for tier in tier_order:
            items = by_tier.get(tier)
            if not items:
                continue
            blocks.append(labels[tier])
            for ev in sorted(items, key=lambda e: e.ts, reverse=True):
                head = "MACRO" if (ev.type == EventType.MACRO or ev.ticker.upper() == "MACRO") \
                    else f"${_esc(ev.ticker.upper())}"
                link = f"  ·  🔗 {link_html(ev.links[0])}" if ev.links else ""
                blocks.append(f"  {head} {ev.type.emoji} {_esc(ev.summary)}{link}")
            blocks.append("")
    blocks.append("─────────────────")
    tag = f"{_esc(handle)} · " if handle else ""
    blocks.append(f"{tag}ℹ️ Informational only — not financial advice.")
    return "\n".join(blocks).rstrip()


# --------------------------------------------------------------------------- #
# Daily digest                                                                  #
# --------------------------------------------------------------------------- #


def _digest_event_line(
    ev: Event, *, indent: str = "  ", show_cashtag: bool = True
) -> str:
    """Render one event line inside a digest section.

    ``show_cashtag=False`` is used inside a per-company block where the
    cashtag is already the block header (avoids "$NVDA … $NVDA …").
    """
    status = _status_token(ev)
    tag = f"  [{status}]" if status else ""
    link = f"  ·  🔗 {link_html(ev.links[0])}" if ev.links else ""
    if ev.type == EventType.MACRO or ev.ticker.upper() == "MACRO":
        # Economy items: emoji + summary (no cashtag).
        return f"{indent}{ev.type.emoji} {_esc(ev.summary)}{link}{tag}"
    if not show_cashtag:
        return f"{indent}{ev.type.emoji} {_esc(ev.summary)}{link}{tag}"
    cashtag = f"${_esc(ev.ticker.upper())}"
    return f"{indent}{cashtag} {ev.type.emoji} {_esc(ev.summary)}{link}{tag}"


def format_digest(
    events: Iterable[Event], all_tickers: List[str], now: float
) -> str:
    """Compile the daily digest (PRD F7), HTML-formatted.

    Sections, in order:
      🔴 CRITICAL EVENTS       — anything materially CRITICAL (incl. MACRO)
      📊 ECONOMY               — macro releases + Fed commentary
      📈 BY COMPANY            — per-ticker (watchlist order), one block each

    Watchlist tickers with no events show as "— no new events".
    """
    events = list(events)

    macro_tickers = {"MACRO"}
    # Buckets
    critical: List[Event] = []
    economy: List[Event] = []
    by_company: dict[str, List[Event]] = {t: [] for t in all_tickers if t.upper() not in macro_tickers}

    # Partition. An event lands in at most one section: CRITICAL first;
    # otherwise ECONOMY for macro/Fed; otherwise per-ticker.
    for ev in events:
        is_macro = (
            ev.type == EventType.MACRO
            or ev.ticker.upper() == "MACRO"
            or (ev.ticker.upper() == "MACRO" and ev.type == EventType.EXEC_COMMENTARY)
        )
        if ev.materiality == Materiality.CRITICAL:
            critical.append(ev)
        elif is_macro:
            economy.append(ev)
        else:
            by_company.setdefault(ev.ticker.upper(), []).append(ev)

    header = f"📰 MarketBrief Daily — {datetime.fromtimestamp(now, tz=SGT):%d %b %Y}"
    blocks: List[str] = [header, ""]

    if critical:
        blocks.append("🔴 CRITICAL EVENTS")
        for ev in sorted(critical, key=lambda e: e.ts):
            blocks.append(_digest_event_line(ev))
        blocks.append("")

    if economy:
        blocks.append("📊 ECONOMY")
        for ev in sorted(economy, key=lambda e: e.ts):
            blocks.append(_digest_event_line(ev))
        blocks.append("")

    blocks.append("📈 BY COMPANY")
    for ticker in all_tickers:
        if ticker.upper() in macro_tickers:
            continue
        items = by_company.get(ticker.upper(), [])
        if not items:
            blocks.append(f"  ${_esc(ticker.upper())}  — no new events")
            continue
        blocks.append(f"  ${_esc(ticker.upper())}")
        for ev in sorted(items, key=lambda e: e.ts):
            blocks.append(_digest_event_line(ev, indent="    ", show_cashtag=False))

    blocks.append("")
    blocks.append("─────────────────")
    blocks.append(f"MarketBrief · {len(events)} updates today")
    blocks.append("ℹ️ Informational only — not financial advice.")
    return "\n".join(blocks)
