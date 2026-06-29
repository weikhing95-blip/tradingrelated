"""Ingestion orchestration — wires sources through the pipeline to events.

One cycle:
    fetch new items  → mark seen → whitelist → persist raw → classify
    → dedup (collapse + cross-confirm against recent) → materiality + summarize
    → create events → route (push now | buffer for digest).

``build_events`` does the pipeline work (and the DB writes) but no Telegram I/O,
so it's testable against a temp DB. ``run_cycle`` adds fetching + publishing.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from . import db
from .config import DEDUP_WINDOW_HOURS, SGT, Config
from .pipeline import classify, dedup, materiality, relevance, summarize, whitelist
from .schemas import Event, Materiality, RawItem, SentMode, Tier
from .sources.base import Source


def _dedup_links(urls: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _canon_url(url: str) -> str:
    """Canonical form of a URL for cross-ticker dedup: drop scheme, leading
    www., query string and fragment, and a trailing slash. Empty in → empty out
    (callers must treat empty as 'no match' so blank links never collapse)."""
    if not url:
        return ""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?", 1)[0].split("#", 1)[0]
    return u.rstrip("/")


def _find_url_dup(links: Sequence[str], url_to_event: Dict[str, Event]) -> Optional[Event]:
    """The already-stored event (any ticker) that shares one of these URLs."""
    for u in links:
        c = _canon_url(u)
        if c and c in url_to_event:
            return url_to_event[c]
    return None


def effective_whitelist(cfg: Config) -> List[str]:
    """Config defaults plus any owner-approved publishers added at runtime."""
    return list(cfg.whitelist) + db.get_whitelist_extra(cfg.db_path)


def _source_name(item: RawItem) -> str:
    if item.source == "edgar":
        return "SEC EDGAR"
    return item.publisher or item.source.title()


def category_enabled(cfg: Config, ticker: str, event_type) -> bool:
    """A ticker's categories filter (PRD F-`/categories`). Empty set = all on."""
    enabled = db.categories_for(cfg.db_path, cfg.feed_id, ticker)
    return (not enabled) or event_type.value in enabled


def in_quiet_hours(cfg: Config, now: float) -> bool:
    """True if `now` (SGT) falls inside the configured quiet window. Returns
    False when quiet hours are disabled (24/7 — the default for this feed)."""
    if not cfg.quiet_hours_enabled:
        return False
    hour = datetime.fromtimestamp(now, tz=SGT).hour
    start, end = cfg.quiet_start_hour, cfg.quiet_end_hour
    if start <= end:
        return start <= hour < end
    # Window wraps past midnight (e.g. 22→06).
    return hour >= start or hour < end


def should_push_now(cfg: Config, event: Event, now: float) -> bool:
    """Decide whether an event pushes instantly vs waits for the digest.

    - firehose volume: push everything on-watchlist (incl. LOW), 24/7.
    - moderate: push material/critical only.
    - low: push critical only.
    Muted tickers never push; quiet hours (if enabled) suppress all but critical.
    """
    if db.is_muted(cfg.db_path, cfg.feed_id, event.ticker, now):
        return False

    if cfg.feed_volume == "firehose":
        pushable = True
    elif cfg.feed_volume == "low":
        pushable = event.materiality == Materiality.CRITICAL
    else:  # moderate
        pushable = event.materiality.is_push
    if not pushable:
        return False

    if event.materiality != Materiality.CRITICAL and in_quiet_hours(cfg, now):
        return False
    return True


def build_events(
    cfg: Config, raw_items: List[RawItem], now: float, client=None
) -> List[Event]:
    """Run raw items through the pipeline and persist resulting events.

    Cross-confirmations of an already-stored event update that row in place
    (merged links, bumped count) and produce no new event — so a story is
    alerted once, not re-sent each time another wire picks it up (PRD F4).
    """
    # Free-text news sources go through the whitelist + company-specific
    # relevance gate; structured sources (EDGAR filings, earnings actuals, macro
    # releases) are trusted data and bypass both.
    news = [it for it in raw_items if it.source in relevance.NEWS_SOURCES]
    structured = [it for it in raw_items if it.source not in relevance.NEWS_SOURCES]

    wl = effective_whitelist(cfg)
    news = whitelist.filter_approved(news, wl)
    news = [it for it in news if relevance.is_company_specific(it.headline, it.ticker)]

    approved = structured + news

    # Recency guard: only the latest news reaches the channel.
    #   - SEC filings (Tier-1/PRIMARY) are always allowed — inherently current.
    #   - Free-text NEWS sources (Finnhub/Yahoo/Google) MUST carry a known
    #     timestamp within the freshness window. Aggregators resurface old
    #     articles, sometimes with a missing/zero date, so "no date" is treated
    #     as stale and dropped — not kept.
    #   - Other structured feeds (earnings/macro/Fed/insider/relay) keep the
    #     lenient rule: drop only when a known date is clearly too old.
    age_cutoff = now - cfg.max_item_age_hours * 3600

    def _fresh_enough(it: RawItem) -> bool:
        if it.tier == Tier.PRIMARY:
            return True
        if it.source in relevance.NEWS_SOURCES:
            return bool(it.published_at) and it.published_at >= age_cutoff
        return (not it.published_at) or it.published_at >= age_cutoff

    approved = [it for it in approved if _fresh_enough(it)]

    for item in approved:
        db.insert_raw_item(cfg.db_path, item)

    # Classify once, then drop items whose event type is disabled for the ticker.
    type_of = {id(item): classify.classify(item) for item in approved}
    approved = [
        item for item in approved if category_enabled(cfg, item.ticker, type_of[id(item)])
    ]
    groups = dedup.collapse(approved)
    cutoff = now - DEDUP_WINDOW_HOURS * 3600

    # Cross-ticker URL dedup: one article often surfaces under several tickers
    # (e.g. a "Micron vs Nvidia" piece is returned for both MU and NVDA). Map
    # each recently-alerted URL to its event so we post the story once, not once
    # per company. Seeded from the window, kept fresh as we insert below.
    url_to_event: Dict[str, Event] = {}
    for ev in db.recent_events_window(cfg.db_path, cfg.feed_id, cutoff):
        for u in ev.links:
            c = _canon_url(u)
            if c:
                url_to_event.setdefault(c, ev)

    events: List[Event] = []
    for group in groups:
        primary = group[0]
        links = _dedup_links([i.url for i in group])

        # Same article already alerted under any ticker → merge links, bump the
        # cross-confirm count, and don't post a duplicate.
        url_dup = _find_url_dup(links, url_to_event)
        if url_dup and url_dup.id is not None:
            merged = _dedup_links(url_dup.links + links)
            count = max(url_dup.confirmed_count, len(merged))
            db.update_event_links(cfg.db_path, url_dup.id, merged, count)
            url_dup.links = merged
            continue

        recent = db.recent_events(cfg.db_path, cfg.feed_id, primary.ticker, cutoff)
        existing = dedup.find_existing(primary.headline, primary.ticker, recent)
        if existing and existing.id is not None:
            merged = _dedup_links(existing.links + links)
            count = max(existing.confirmed_count, len(merged))
            db.update_event_links(cfg.db_path, existing.id, merged, count)
            continue

        event_type = type_of[id(primary)]
        confirmed = len(group)
        mat = materiality.score(primary, event_type)
        # LLM compression is gated to push items so the digest doesn't cost an
        # API call per line; low-materiality items use the free rich-verbatim.
        # In firehose mode we LLM-summarise minor items too (they get pushed).
        use_llm = mat.is_push or cfg.feed_volume == "firehose"
        summary = summarize.choose_summary(
            primary.headline, primary.body, cfg.summary_mode, client,
            use_llm=use_llm, model=cfg.summary_model,
        )
        event = Event(
            ticker=primary.ticker,
            type=event_type,
            summary=summary,
            links=links,
            tier=primary.tier,
            source_name=_source_name(primary),
            materiality=mat,
            confirmed_count=confirmed,
            unconfirmed=materiality.is_unconfirmed(primary.tier, confirmed),
            sent_mode=SentMode.PENDING,
            ts=primary.published_at,
        )
        event.id = db.insert_event(cfg.db_path, cfg.feed_id, event)
        events.append(event)
        # Register this event's URLs so a later group in the same batch that
        # carries the same article (under another ticker) collapses into it.
        for u in event.links:
            c = _canon_url(u)
            if c:
                url_to_event.setdefault(c, event)
    return events


async def prime(cfg: Config, sources: Sequence[Source]) -> int:
    """Cold-start baseline: mark all currently-available items as seen WITHOUT
    alerting, so the first deploy doesn't flood the channel with days-old news.
    Only genuinely new items (arriving after this) will then trigger alerts."""
    tickers = db.watchlist_tickers(cfg.db_path, cfg.feed_id)
    if not tickers:
        return 0

    def _is_seen(source: str, item_id: str) -> bool:
        return db.is_seen(cfg.db_path, source, item_id)

    count = 0
    for source in sources:
        for item in await source.fetch_new(tickers, _is_seen):
            db.mark_seen(cfg.db_path, item.source, item.source_item_id, item.ticker)
            count += 1
    return count


async def run_cycle(
    cfg: Config,
    sources: Sequence[Source],
    publisher,
    now: float,
    client=None,
) -> List[Event]:
    """Fetch from each source, build events, push the material ones."""
    tickers = db.watchlist_tickers(cfg.db_path, cfg.feed_id)
    if not tickers:
        return []

    def _is_seen(source: str, item_id: str) -> bool:
        return db.is_seen(cfg.db_path, source, item_id)

    raw: List[RawItem] = []
    for source in sources:
        fetched = await source.fetch_new(tickers, _is_seen)
        for item in fetched:
            db.mark_seen(cfg.db_path, item.source, item.source_item_id, item.ticker)
        raw.extend(fetched)

    events = build_events(cfg, raw, now, client)

    for event in events:
        if event.id is None:
            continue
        if should_push_now(cfg, event, now):
            await publisher.push(event)
            db.mark_event_sent(cfg.db_path, event.id, SentMode.PUSH)
    return events
