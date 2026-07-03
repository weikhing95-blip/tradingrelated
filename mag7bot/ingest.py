"""Ingestion orchestration — wires sources through the pipeline to events.

One cycle:
    fetch new items  → mark seen → whitelist → persist raw → classify
    → dedup (collapse + cross-confirm against recent) → materiality + summarize
    → create events → route (push now | buffer for digest).

``build_events`` does the pipeline work (and the DB writes) but no Telegram I/O,
so it's testable against a temp DB. ``run_cycle`` adds fetching + publishing.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional, Sequence

from . import article, companies, db, soul
from .config import (
    DEDUP_WINDOW_BY_TYPE,
    SGT,
    SOURCE_ALERT_THRESHOLD,
    Config,
    is_commercial_safe,
)
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


def effective_feed_volume(cfg: Config) -> str:
    """The feed volume actually in force. In PUBLIC_MODE, ``firehose`` is clamped
    to ``moderate`` — a public/commercial channel is a curated headline feed, not
    a personal firehose (FQ-A2-01). ``firehose`` is only honoured for personal use
    (PUBLIC_MODE off)."""
    if cfg.public_mode and cfg.feed_volume == "firehose":
        return "moderate"
    return cfg.feed_volume


def dedup_window_hours_for(event_type: str, cfg: Config) -> int:
    """Applicable dedup window (hours) for an event type — tight for price-
    sensitive types, wide for slow-moving stories (FQ-A1-02). Falls back to the
    configured DEDUP_WINDOW_HOURS for anything unlisted."""
    return DEDUP_WINDOW_BY_TYPE.get(event_type, cfg.dedup_window_hours)


def should_push_now(cfg: Config, event: Event, now: float) -> bool:
    """Decide whether an event pushes instantly vs waits for the digest.

    - firehose volume: push everything on-watchlist (incl. LOW), 24/7.
    - moderate: push material/critical only.
    - low: push critical only.
    Muted tickers never push; quiet hours (if enabled) suppress all but critical.
    In PUBLIC_MODE firehose is clamped to moderate (see effective_feed_volume).
    """
    if db.is_muted(cfg.db_path, cfg.feed_id, event.ticker, now):
        return False

    volume = effective_feed_volume(cfg)
    if volume == "firehose":
        pushable = True
    elif volume == "low":
        pushable = event.materiality == Materiality.CRITICAL
    else:  # moderate
        pushable = event.materiality.is_push
    if not pushable:
        return False

    if event.materiality != Materiality.CRITICAL and in_quiet_hours(cfg, now):
        return False
    return True


def _approved_items(cfg: Config, raw_items: List[RawItem], now: float) -> List[RawItem]:
    """Stage 1 — whitelist + relevance + freshness filtering.

    Free-text news sources go through the whitelist + company-specific relevance
    gate; structured sources (EDGAR filings, earnings actuals, macro releases)
    are trusted data and bypass both. Then the freshness guard keeps only the
    latest news:
      - SEC filings (Tier-1/PRIMARY) are always allowed — inherently current.
      - Free-text NEWS sources MUST carry a known timestamp within the window;
        aggregators resurface old articles (sometimes with no date), so "no
        date" is treated as stale and dropped.
      - Other structured feeds keep the lenient rule: drop only a known-old date.
    """
    # Legal gate (defense-in-depth, 5B-02): in PUBLIC_MODE no item from a source
    # not licensed for redistribution may become a published event — even via a
    # caller that bypasses run_cycle's source gate (e.g. the channel relay, which
    # calls build_events directly). run_cycle already filters at the source level;
    # this ensures build_events is safe on its own.
    if cfg.public_mode:
        raw_items = [it for it in raw_items if is_commercial_safe(it.source)]

    news = [it for it in raw_items if it.source in relevance.NEWS_SOURCES]
    structured = [it for it in raw_items if it.source not in relevance.NEWS_SOURCES]

    wl = effective_whitelist(cfg)
    news = whitelist.filter_approved(news, wl)
    news = [it for it in news if relevance.is_company_specific(it.headline, it.ticker)]

    approved = structured + news
    age_cutoff = now - cfg.max_item_age_hours * 3600

    def _fresh_enough(it: RawItem) -> bool:
        if it.tier == Tier.PRIMARY:
            return True
        if it.source in relevance.NEWS_SOURCES:
            return bool(it.published_at) and it.published_at >= age_cutoff
        return (not it.published_at) or it.published_at >= age_cutoff

    return [it for it in approved if _fresh_enough(it)]


def _classify_and_enable(cfg: Config, approved: List[RawItem]):
    """Stage 2 — persist raw items, classify each, drop categories disabled for
    the ticker. Returns (kept_items, type_of) where type_of is keyed by id()."""
    for item in approved:
        db.insert_raw_item(cfg.db_path, item)
    type_of = {id(item): classify.classify(item) for item in approved}
    kept = [
        it for it in approved if category_enabled(cfg, it.ticker, type_of[id(it)])
    ]
    return kept, type_of


def _merge_links_into(cfg: Config, existing: Event, links: List[str]) -> None:
    """Fold a duplicate story's links into an already-stored event in place."""
    merged = _dedup_links(existing.links + links)
    count = max(existing.confirmed_count, len(merged))
    db.update_event_links(cfg.db_path, existing.id, merged, count)
    existing.links = merged


def _make_event(
    cfg: Config, group: List[RawItem], event_type, client, examples=None,
    style_guide="", watchlist=None,
) -> Event:
    """Stage 4 — build a send-ready Event from a same-story group."""
    primary = group[0]
    mat = materiality.score(primary, event_type)
    # LLM compression is gated to push items so the digest doesn't cost an API
    # call per line; in firehose mode we summarise minor items too (all pushed).
    use_llm = mat.is_push or effective_feed_volume(cfg) == "firehose"
    # Anchor the summary on the known company (from the ticker) so the model
    # names it instead of writing "a major data-analytics company".
    subject = (
        companies.name_for(primary.ticker)
        if primary.ticker.upper() in companies.ALL_COMPANIES
        else ""
    )
    summary = summarize.choose_summary(
        primary.headline, primary.body, cfg.summary_mode, client,
        use_llm=use_llm, model=cfg.summary_model, subject=subject, examples=examples,
        style_guide=style_guide,
    )
    # Tag every watchlist company the story is about (headline + summary), primary
    # first — so "Apple and Google …" shows "$AAPL · $GOOGL", not just one.
    tickers = [primary.ticker]
    if watchlist and primary.ticker.upper() != "MACRO":
        for t in companies.tickers_in(f"{primary.headline} {summary}", watchlist):
            if t not in tickers:
                tickers.append(t)
    return Event(
        ticker=primary.ticker,
        tickers=tickers,
        type=event_type,
        summary=summary,
        links=_dedup_links([i.url for i in group]),
        tier=primary.tier,
        source_name=_source_name(primary),
        materiality=mat,
        confirmed_count=len(group),
        unconfirmed=materiality.is_unconfirmed(primary.tier, len(group)),
        sent_mode=SentMode.PENDING,
        ts=primary.published_at,
        # Store the normalised headline (not the summary) so a later cycle can
        # match a paraphrased re-report of this same story and skip reposting.
        dedup_key=dedup.normalize(primary.headline, primary.ticker),
        # Structured macro fields (GM-B2-01), if the source supplied them.
        **_macro_fields(primary),
    )


def _macro_fields(item: RawItem) -> dict:
    """Extract the structured macro fields a macro source stashed in its payload,
    for the unified macro render. Empty for non-macro items."""
    m = item.payload.get("macro") if isinstance(item.payload, dict) else None
    if not isinstance(m, dict):
        return {}
    return {
        k: str(m.get(k, "") or "")
        for k in ("economy", "indicator", "period", "actual", "consensus", "prior")
    }


def build_events(
    cfg: Config, raw_items: List[RawItem], now: float, client=None
) -> List[Event]:
    """Run raw items through the pipeline and persist resulting events.

    Cross-confirmations of an already-stored event update that row in place
    (merged links, bumped count) and produce no new event — so a story is
    alerted once, not re-sent each time another wire picks it up (PRD F4).

    Stages: filter (whitelist/relevance/freshness) → classify+category →
    dedup (same-story collapse, cross-ticker URL, cross-window) → build.
    """
    approved = _approved_items(cfg, raw_items, now)
    approved, type_of = _classify_and_enable(cfg, approved)
    groups = dedup.collapse(approved)
    # Widest configured window seeds the candidate pool; each group then narrows
    # to its own event-type window (FQ-A1-02).
    max_window_h = max([cfg.dedup_window_hours, *DEDUP_WINDOW_BY_TYPE.values()])
    cutoff = now - max_window_h * 3600
    today = time.strftime("%Y%m%d", time.gmtime(now))

    # House voice (durable style guide) + recent ✏️ corrections (rotating
    # few-shot examples), both only relevant in LLM mode.
    learning_on = cfg.summary_mode == "llm" and cfg.enable_feedback_learning
    examples = db.recent_summary_examples(cfg.db_path) if learning_on else []
    style_guide = soul.load(cfg) if learning_on else ""
    # Watchlist drives multi-company tagging on each event's header.
    watchlist = db.watchlist_tickers(cfg.db_path, cfg.feed_id)

    # Cross-ticker URL dedup: one article often surfaces under several tickers
    # (e.g. a "Micron vs Nvidia" piece returned for both MU and NVDA). Map each
    # recently-alerted URL to its event so we post the story once, not once per
    # company. Seeded from the window, kept fresh as we insert below.
    window_events = db.recent_events_window(cfg.db_path, cfg.feed_id, cutoff)
    url_to_event: Dict[str, Event] = {}
    for ev in window_events:
        for u in ev.links:
            c = _canon_url(u)
            if c:
                url_to_event.setdefault(c, ev)

    def _event_tickers(ev: Event) -> set:
        return {(ev.ticker or "").upper(), *(t.upper() for t in (ev.tickers or []))}

    events: List[Event] = []
    for group in groups:
        primary = group[0]
        # Learned curation: skip a (ticker, event_type) the owner has muted via
        # repeated 👎 feedback — dropped before summarising, so it costs nothing.
        etype = type_of[id(primary)]
        if cfg.enable_feedback_learning and db.is_suppressed(
            cfg.db_path, primary.ticker, etype.value
        ):
            continue
        links = _dedup_links([i.url for i in group])
        # Applicable dedup window for this event type (tight for price-sensitive,
        # wide for slow-moving stories). Everything below only considers prior
        # alerts within this window.
        type_cutoff = now - dedup_window_hours_for(etype.value, cfg) * 3600

        # Same article already alerted under any ticker → merge, don't repost.
        url_dup = _find_url_dup(links, url_to_event)
        if url_dup and url_dup.id is not None:
            _merge_links_into(cfg, url_dup, links)
            continue

        # Same story already alerted for this ticker (paraphrased headline).
        recent = db.recent_events(cfg.db_path, cfg.feed_id, primary.ticker, type_cutoff)
        existing = dedup.find_existing(primary.headline, primary.ticker, recent)
        if existing and existing.id is not None:
            _merge_links_into(cfg, existing, links)
            continue

        # Semantic fallback: a paraphrased re-report from a different outlet (no
        # shared words or URL) that the lexical checks miss. Runs whenever a client
        # is available (independent of SUMMARY_MODE — FQ-A1-01); compares the
        # incoming item against ALL same-ticker alerts still inside the window
        # (story-level clustering — FQ-A1-03). One cheap Haiku call, only when
        # there are candidates; the daily call count is logged for cost visibility.
        if cfg.enable_semantic_dedup and client is not None:
            item_tickers = {primary.ticker.upper()} | {
                t.upper() for t in companies.tickers_in(primary.headline, watchlist)
            }
            cands = [
                ev for ev in window_events
                if ev.id is not None and ev.ts >= type_cutoff
                and _event_tickers(ev) & item_tickers
            ]
            if cands:
                db.incr_counter(cfg.db_path, "semantic_calls", today)
                sem_dup = dedup.semantic_find(
                    client, primary.headline, cands, cfg.summary_model
                )
                if sem_dup and sem_dup.id is not None:
                    _merge_links_into(cfg, sem_dup, links)
                    continue

        event = _make_event(
            cfg, group, type_of[id(primary)], client, examples, style_guide, watchlist
        )
        # Nothing worth posting: an empty headline/body yields an empty or
        # refusal-style summary ("No article text provided…"). Drop it rather
        # than publish a value-less alert to the channel.
        if summarize.is_nonsummary(event.summary):
            continue
        event.id = db.insert_event(cfg.db_path, cfg.feed_id, event)
        events.append(event)
        # Make this event a candidate for later groups in the same batch — both
        # by URL (same article under another ticker) and for the semantic check
        # (a differently-worded re-report arriving in the same cycle).
        window_events.append(event)
        for u in event.links:
            c = _canon_url(u)
            if c:
                url_to_event.setdefault(c, event)
    return events


async def enrich_article_bodies(
    cfg: Config, items: Sequence[RawItem], concurrency: int = 5
) -> None:
    """Fetch the full article for each free-text news item and replace its short
    blurb with the extracted body, so the LLM summarizer has real substance to
    compress (the headline/blurb often omit the key numbers — e.g. a price-target
    or earnings figure). Best-effort and concurrency-capped; structured sources
    (filings/earnings/macro/relay) are skipped — they're already self-contained.
    """
    targets = [it for it in items if it.source in relevance.NEWS_SOURCES and it.url]
    if not targets:
        return
    sem = asyncio.Semaphore(concurrency)

    async def _one(it: RawItem) -> None:
        async with sem:
            text = await article.fetch_article_text(it.url)
        if text and len(text) > len(it.body or ""):
            it.body = text

    await asyncio.gather(*(_one(it) for it in targets), return_exceptions=True)


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


def public_safe_sources(sources: Sequence[Source], cfg: Config) -> List[Source]:
    """Legal gate: in PUBLIC_MODE, drop every source not licensed for commercial
    redistribution — a hard exclusion in code, independent of the per-source
    ENABLE_* env toggles, so an unlicensed feed can never leak to a public
    channel. Outside public mode this is a no-op (all configured sources run)."""
    if not cfg.public_mode:
        return list(sources)
    return [s for s in sources if is_commercial_safe(s.name)]


async def run_cycle(
    cfg: Config,
    sources: Sequence[Source],
    publisher,
    now: float,
    client=None,
    alert: Optional[Callable[[str], Awaitable[None]]] = None,
) -> List[Event]:
    """Fetch from each source, build events, push the material ones.

    Each source is fetched in isolation: one source raising never aborts the
    cycle or the other sources. Per-source health is recorded, and ``alert`` (if
    given) is called once on an ok→error transition and once on recovery, so the
    owner is told when a feed breaks or comes back — not on every cycle.

    In PUBLIC_MODE, commercially-unsafe sources are excluded here before any
    fetch (defense-in-depth — they are also not wired in app.py public mode).
    """
    tickers = db.watchlist_tickers(cfg.db_path, cfg.feed_id)
    if not tickers:
        return []

    def _is_seen(source: str, item_id: str) -> bool:
        return db.is_seen(cfg.db_path, source, item_id)

    raw: List[RawItem] = []
    for source in public_safe_sources(sources, cfg):
        try:
            fetched = await source.fetch_new(tickers, _is_seen)
        except Exception as exc:  # isolate: a broken source never kills the cycle
            detail = f"{type(exc).__name__}: {exc}"
            n = db.record_source_error(cfg.db_path, source.name, detail, now)
            print(f"⚠️  Source '{source.name}' fetch failed (#{n}): {detail}")
            # Alert once, only when the outage becomes *sustained* — a single
            # transient blip that heals next poll never pings the owner.
            if alert and n == SOURCE_ALERT_THRESHOLD:
                await alert(
                    f"⚠️ Source '{source.name}' is failing "
                    f"({n} polls in a row) — {detail}"
                )
            continue
        prior_errors = db.record_source_ok(cfg.db_path, source.name, len(fetched), now)
        # Only announce recovery if we announced the outage (avoids an orphan ✅).
        if alert and prior_errors >= SOURCE_ALERT_THRESHOLD:
            await alert(f"✅ Source '{source.name}' recovered — {len(fetched)} item(s).")
        for item in fetched:
            db.mark_seen(cfg.db_path, item.source, item.source_item_id, item.ticker)
        raw.extend(fetched)

    # Enrich news items with the full article text so LLM summaries carry the
    # article's substance, not just the headline (LLM mode only — verbatim mode
    # never fetches). Best-effort; failures leave the original blurb.
    if cfg.summary_mode == "llm" and cfg.enable_article_fetch:
        await enrich_article_bodies(cfg, raw)

    events = build_events(cfg, raw, now, client)

    for event in events:
        if event.id is None:
            continue
        if should_push_now(cfg, event, now):
            try:
                await publisher.push(event)
                db.mark_event_sent(cfg.db_path, event.id, SentMode.PUSH)
                # Metrics: end-to-end latency = post time − source event time.
                db.record_metric(
                    cfg.db_path, event.id, event.source_name, event.tier.value,
                    event.type.value, event.ts, now,
                )
            except Exception as exc:  # channel post failed (e.g. lost admin)
                detail = f"{type(exc).__name__}: {exc}"
                print(f"⚠️  Push to channel failed: {detail}")
                if alert:
                    await alert(f"⚠️ Failed to post to the channel — {detail}")
    return events
