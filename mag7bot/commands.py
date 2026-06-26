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
from typing import Callable, List

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes

from . import companies, db
from .config import Config
from .schemas import EventType

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
        "Mag 7 News Bot — commands:\n"
        "/watchlist — show tracked tickers\n"
        "/add TSLA — add a ticker\n"
        "/remove AMZN — remove a ticker\n"
        "/digest 0900 — set daily digest time (SGT)\n"
        "/mute NVDA 24h — mute a ticker temporarily\n"
        "/categories [TICKER [type]] — view/toggle event types\n"
        "/sources — show the source whitelist\n"
        "/show — expand items from the last digest"
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
    cik = companies.cik_for(ticker) if ticker in companies.MAG7 else None
    db.add_ticker(cfg.db_path, cfg.feed_id, ticker, cik)
    note = "" if cik else " (no SEC CIK on file — news only, no EDGAR filings)"
    await update.message.reply_text(f"Added {ticker}{note}.")


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
async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    await update.message.reply_text(
        "✅ Active source whitelist (PRD §4):\n  " + "\n  ".join(cfg.whitelist)
    )


@owner_only
async def cmd_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    since = time.time() - 24 * 3600
    events = db.last_digest_events(cfg.db_path, cfg.feed_id, since)
    if not events:
        await update.message.reply_text("No digest items in the last 24h.")
        return
    lines = ["🗂 Last digest items:"]
    for ev in events:
        link = ev.links[0] if ev.links else "(no link)"
        lines.append(f"  {ev.type.emoji} {ev.ticker} {ev.type.display}: {ev.summary}")
        lines.append(f"     🔗 {link}")
    await update.message.reply_text("\n".join(lines), disable_web_page_preview=True)


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
    }
    for name, fn in handlers.items():
        application.add_handler(CommandHandler(name, fn))
