"""Telegram command handlers — the control plane (PRD §7, F9, F10).

All commands are accepted **only in the owner's 1:1 DM**, and only when the
sender's ``user_id`` matches the stored owner. Any other chat or sender gets a
polite refusal and the action is never applied. Alerts/digests never appear
here — they go to the channel.

Config is read from ``application.bot_data['cfg']``; the digest reschedule
callback (set by the scheduler) lives in ``bot_data['reschedule_digest']``.
"""

from __future__ import annotations

import functools
import re
import time
from datetime import datetime
from typing import Callable, List

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes

from . import companies, db, ingest, research
from .config import SGT, Config
from .schemas import Event, EventType, Materiality, SentMode, Tier

_DURATION = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def _cfg(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.application.bot_data["cfg"]


def owner_only(func: Callable):
    """Gate a handler to the owner's DM (F9/F10)."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        cfg = _cfg(context)
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or chat.type != ChatType.PRIVATE:
            return  # ignore commands outside a 1:1 DM
        if user is None or user.id != cfg.owner_user_id:
            await update.message.reply_text(
                "Sorry — this bot is privately operated and only responds to its owner."
            )
            return
        return await func(update, context)

    return wrapper


# --------------------------------------------------------------------------- #
# Handlers                                                                      #
# --------------------------------------------------------------------------- #


@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "MarketBrief — commands:\n"
        "/watchlist — show tracked tickers\n"
        "/add TSLA — add a ticker (auto-looks up the SEC CIK on EDGAR)\n"
        "/remove AMZN — remove a ticker\n"
        "/digest 0900 — set daily digest time (SGT)\n"
        "/mute NVDA 24h — mute a ticker temporarily\n"
        "/categories [TICKER [type]] — view/toggle event types\n"
        "/sources — show the source whitelist\n"
        "/show — expand items from the last digest\n"
        "/status — operational health (last 24h)\n"
        "/test — post a sample alert to the channel (publish health-check)\n"
        "/diag — live-probe each news source and report counts\n"
        "/suggest_sources — agent proposes reputable sources to add\n"
        "/add_source <domain> [name] [tier] · /remove_source <domain>"
    )


@owner_only
async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    rows = db.get_watchlist(cfg.db_path, cfg.feed_id)
    if not rows:
        await update.message.reply_text("Watchlist is empty. Add one with /add TSLA")
        return
    now = time.time()
    lines = ["📋 Watchlist:"]
    for r in rows:
        muted = r["mute_until"] and r["mute_until"] > now
        tag = " (muted)" if muted else ""
        lines.append(f"  • {r['ticker']}{tag}")
    await update.message.reply_text("\n".join(lines))


@owner_only
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not context.args:
        await update.message.reply_text("Usage: /add TSLA")
        return
    ticker = context.args[0].upper()
    if ticker in companies.ALL_COMPANIES:
        cik = companies.cik_for(ticker)
    else:
        # Unknown ticker — best-effort dynamic lookup on EDGAR.
        await update.message.reply_text(f"Looking up {ticker} on SEC EDGAR…")
        cik = await companies.lookup_cik(ticker, cfg.sec_edgar_user_agent)
    db.add_ticker(cfg.db_path, cfg.feed_id, ticker, cik)
    if cik:
        await update.message.reply_text(
            f"Added {ticker} with EDGAR CIK {cik} — full coverage (news + SEC filings)."
        )
    else:
        await update.message.reply_text(
            f"Added {ticker} (news coverage only — no SEC CIK found on EDGAR)."
        )


@owner_only
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not context.args:
        await update.message.reply_text("Usage: /remove AMZN")
        return
    ticker = context.args[0].upper()
    removed = db.remove_ticker(cfg.db_path, cfg.feed_id, ticker)
    await update.message.reply_text(
        f"Removed {ticker}." if removed else f"{ticker} was not on the watchlist."
    )


@owner_only
async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not context.args or not re.fullmatch(r"\d{4}", context.args[0]):
        await update.message.reply_text("Usage: /digest 0900  (24h SGT, HHMM)")
        return
    hhmm = context.args[0]
    hh, mm = int(hhmm[:2]), int(hhmm[2:])
    if hh > 23 or mm > 59:
        await update.message.reply_text("That's not a valid time. Use HHMM, e.g. 0900.")
        return
    db.set_digest_time(cfg.db_path, cfg.feed_id, hhmm)
    reschedule = context.application.bot_data.get("reschedule_digest")
    if reschedule:
        reschedule(hhmm)
    await update.message.reply_text(f"Daily digest set to {hh:02d}:{mm:02d} SGT.")


@owner_only
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /mute NVDA 24h  (units: m/h/d)")
        return
    ticker = context.args[0].upper()
    m = _DURATION.match(context.args[1])
    if not m:
        await update.message.reply_text("Bad duration. Examples: 30m, 24h, 3d")
        return
    seconds = int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
    until = time.time() + seconds
    if not db.set_mute(cfg.db_path, cfg.feed_id, ticker, until):
        await update.message.reply_text(f"{ticker} is not on the watchlist.")
        return
    await update.message.reply_text(f"Muted {ticker} for {context.args[1]}.")


@owner_only
async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    all_types = [t.value for t in EventType]
    # /categories — list the event-type vocabulary.
    if not context.args:
        await update.message.reply_text(
            "Event types:\n  " + "\n  ".join(all_types) +
            "\n\n/categories TICKER — show a ticker's enabled types\n"
            "/categories TICKER <type> — toggle one on/off"
        )
        return
    ticker = context.args[0].upper()
    if ticker not in set(db.watchlist_tickers(cfg.db_path, cfg.feed_id)):
        await update.message.reply_text(f"{ticker} is not on the watchlist.")
        return
    enabled = db.categories_for(cfg.db_path, cfg.feed_id, ticker)
    current = set(enabled) if enabled else set(all_types)
    # /categories TICKER — show current.
    if len(context.args) == 1:
        await update.message.reply_text(
            f"{ticker} enabled types:\n  " + "\n  ".join(sorted(current))
        )
        return
    # /categories TICKER <type> — toggle.
    etype = context.args[1].lower()
    if etype not in all_types:
        await update.message.reply_text(f"Unknown type '{etype}'. See /categories.")
        return
    if etype in current:
        current.discard(etype)
    else:
        current.add(etype)
    to_store: List[str] = [] if current == set(all_types) else sorted(current)
    db.set_categories(cfg.db_path, cfg.feed_id, ticker, to_store)
    state = "on" if etype in current else "off"
    await update.message.reply_text(f"{ticker}: {etype} is now {state}.")


@owner_only
async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post a sample alert to the channel to verify the publish path end-to-end."""
    publisher = context.application.bot_data.get("publisher")
    if publisher is None:
        await update.message.reply_text("No publisher configured (dry-run mode?).")
        return
    sample = Event(
        ticker="TEST",
        type=EventType.NEWS,
        summary="Channel test — if you can see this in the channel, publishing works.",
        links=["https://www.sec.gov/cgi-bin/browse-edgar"],
        tier=Tier.PRIMARY,
        source_name="MarketBrief",
        materiality=Materiality.MATERIAL,
        confirmed_count=1,
        unconfirmed=False,
        sent_mode=SentMode.PUSH,
        ts=time.time(),
    )
    try:
        await publisher.push(sample)
    except Exception as exc:
        await update.message.reply_text(
            f"❌ Channel post FAILED: {exc}\n"
            f"Check the bot is an admin of the channel with 'Post Messages'."
        )
        return
    await update.message.reply_text(
        "✅ Sent a test alert to the channel — go check it appeared there."
    )


@owner_only
async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    extra = db.list_whitelist_extra(cfg.db_path)
    lines = ["✅ Active source whitelist (PRD §4):", "  " + "\n  ".join(cfg.whitelist)]
    if extra:
        lines.append("\n➕ Owner-approved additions:")
        for r in extra:
            lines.append(f"  {r['domain']} ({r['tier']})")
    await update.message.reply_text("\n".join(lines))


@owner_only
async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Live-fetch from each configured source and report counts — answers
    'are the sources returning anything?' without waiting for an alert."""
    cfg = _cfg(context)
    sources = context.application.bot_data.get("sources", {})
    if not sources:
        await update.message.reply_text("No sources configured (dry-run mode?).")
        return
    tickers = db.watchlist_tickers(cfg.db_path, cfg.feed_id)
    await update.message.reply_text(
        f"🔎 Probing {len(sources)} source(s) across {len(tickers)} tickers "
        f"(live fetch, ignoring 'seen')…"
    )
    lines = ["🔎 Source check:"]
    for name, src in sources.items():
        try:
            items = await src.fetch_new(tickers, lambda *_: False)
            sample = items[0].headline[:80] if items else "—"
            lines.append(f"  • {name}: {len(items)} available · e.g. “{sample}”")
        except Exception as exc:  # surface the error rather than hiding it
            lines.append(f"  • {name}: ❌ {type(exc).__name__}: {exc}")
    lines.append(
        "\nNote: 'available' counts everything currently in each feed; only NEW, "
        "material items push to the channel."
    )
    await update.message.reply_text("\n".join(lines), disable_web_page_preview=True)


@owner_only
async def cmd_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    since = time.time() - 24 * 3600
    events = db.last_digest_events(cfg.db_path, cfg.feed_id, since)
    if not events:
        await update.message.reply_text("No digest items in the last 24h.")
        return
    import html as _html

    from .pipeline.formatter import link_html

    lines = ["🗂 Last digest items:"]
    for ev in events:
        link = link_html(ev.links[0]) if ev.links else "(no link)"
        lines.append(
            f"  {ev.type.emoji} {_html.escape(ev.ticker)} "
            f"{_html.escape(ev.type.display)}: {_html.escape(ev.summary, quote=False)}"
        )
        lines.append(f"     🔗 {link}")
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True
    )


def _get_client(context: ContextTypes.DEFAULT_TYPE):
    """Reuse the summariser's client, or create one if an API key is present."""
    client = context.application.bot_data.get("client")
    if client is not None:
        return client
    cfg = _cfg(context)
    if not cfg.anthropic_api_key:
        return None
    import anthropic

    return anthropic.Anthropic()


@owner_only
async def cmd_suggest_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask the research agent to propose reputable publishers for your review."""
    cfg = _cfg(context)
    client = _get_client(context)
    if client is None:
        await update.message.reply_text(
            "The source-research agent needs an Anthropic API key. "
            "Set ANTHROPIC_API_KEY (and redeploy) to use /suggest_sources."
        )
        return
    await update.message.reply_text("🧭 Researching reputable sources… (a few seconds)")
    watchlist = db.watchlist_tickers(cfg.db_path, cfg.feed_id)
    current = ingest.effective_whitelist(cfg)
    try:
        result = research.suggest_sources(client, watchlist, current)
    except Exception as exc:
        await update.message.reply_text(f"Agent error: {exc}")
        return
    # Filter out anything already whitelisted (defensive).
    have = {d.lower() for d in current}
    fresh = [s for s in result.suggestions if s.domain.lower() not in have]
    if not fresh:
        await update.message.reply_text("No new sources to suggest — whitelist looks complete.")
        return
    lines = ["🧭 Suggested sources (review, then /add_source <domain>):", ""]
    for s in fresh:
        lines.append(f"• {s.name} — {s.domain}  (Tier {s.tier})")
        lines.append(f"   {s.rationale}")
        if s.caution:
            lines.append(f"   ⚠️ {s.caution}")
        lines.append(f"   → /add_source {s.domain} {s.name} {s.tier}")
        lines.append("")
    await update.message.reply_text("\n".join(lines), disable_web_page_preview=True)


@owner_only
async def cmd_add_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not context.args:
        await update.message.reply_text("Usage: /add_source <domain> [name] [tier 1-3]")
        return
    domain = context.args[0].strip().lower()
    rest = context.args[1:]
    tier = "tier2"
    if rest and rest[-1] in ("1", "2", "3"):
        tier = f"tier{rest[-1]}"
        rest = rest[:-1]
    name = " ".join(rest)
    db.add_whitelist_domain(cfg.db_path, domain, name, tier)
    await update.message.reply_text(
        f"✅ Added {domain} ({tier}) to the whitelist. It takes effect on the next poll."
    )


@owner_only
async def cmd_remove_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not context.args:
        await update.message.reply_text("Usage: /remove_source <domain>")
        return
    domain = context.args[0].strip().lower()
    removed = db.remove_whitelist_domain(cfg.db_path, domain)
    await update.message.reply_text(
        f"Removed {domain} from the approved additions."
        if removed
        else f"{domain} is not an owner-added source (config defaults can't be removed here)."
    )


@owner_only
async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List monitored Telegram channels."""
    cfg = _cfg(context)
    rows = db.list_telegram_channels(cfg.db_path)
    if not rows:
        await update.message.reply_text(
            "No Telegram channels monitored yet.\nUse /add_channel @username to add one."
        )
        return
    lines = ["📡 Monitored Telegram channels:", ""]
    for r in rows:
        lines.append(f"• {r['username']}  ({r['name']})  — added by {r['added_by']}")
    await update.message.reply_text("\n".join(lines))


@owner_only
async def cmd_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a Telegram channel to the monitor list. Usage: /add_channel @WalterBloomberg [Name]"""
    cfg = _cfg(context)
    if not context.args:
        await update.message.reply_text("Usage: /add_channel @username [Display Name]")
        return
    username = context.args[0].strip()
    if not username.startswith("@"):
        username = f"@{username}"
    name = " ".join(context.args[1:]) if len(context.args) > 1 else username.lstrip("@")
    db.add_telegram_channel(cfg.db_path, username, name, added_by="manual")
    await update.message.reply_text(
        f"✅ Added {username} to channel monitor.\n"
        "Messages mentioning tracked tickers will now be forwarded to the channel.\n"
        "⚠️  Requires TELEGRAM_API_ID/HASH + session setup to be active."
    )


@owner_only
async def cmd_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a monitored Telegram channel. Usage: /remove_channel @WalterBloomberg"""
    cfg = _cfg(context)
    if not context.args:
        await update.message.reply_text("Usage: /remove_channel @username")
        return
    username = context.args[0].strip()
    removed = db.remove_telegram_channel(cfg.db_path, username)
    await update.message.reply_text(
        f"Removed {username} from channel monitor."
        if removed
        else f"{username} was not in the monitor list."
    )


@owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report 24h operational health: events, pushes, last push, source count."""
    cfg = _cfg(context)
    since = time.time() - 86400
    s = db.status_summary(cfg.db_path, cfg.feed_id, since)
    sources = context.application.bot_data.get("sources", {})
    tickers = db.watchlist_tickers(cfg.db_path, cfg.feed_id)
    last = (
        datetime.fromtimestamp(s["last_push"], tz=SGT).strftime("%d %b, %H:%M SGT")
        if s["last_push"] else "none"
    )
    await update.message.reply_text(
        f"📊 MarketBrief Status (last 24h)\n"
        f"  Events: {s['total']} total · {s['pushes']} pushed · {s['digest']} digest\n"
        f"  Last push: {last}\n"
        f"  Sources active: {len(sources)}\n"
        f"  Watching: {len(tickers)} ticker(s)"
    )


def register(application: Application) -> None:
    """Attach all command handlers to the application."""
    handlers = {
        "start": cmd_help,
        "help": cmd_help,
        "watchlist": cmd_watchlist,
        "add": cmd_add,
        "remove": cmd_remove,
        "digest": cmd_digest,
        "mute": cmd_mute,
        "categories": cmd_categories,
        "sources": cmd_sources,
        "show": cmd_show,
        "status": cmd_status,
        "test": cmd_test,
        "diag": cmd_diag,
        "suggest_sources": cmd_suggest_sources,
        "add_source": cmd_add_source,
        "remove_source": cmd_remove_source,
        "channels": cmd_channels,
        "add_channel": cmd_add_channel,
        "remove_channel": cmd_remove_channel,
    }
    for name, fn in handlers.items():
        application.add_handler(CommandHandler(name, fn))
