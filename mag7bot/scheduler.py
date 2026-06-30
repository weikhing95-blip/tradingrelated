"""Job scheduling via python-telegram-bot's JobQueue.

Three recurring jobs (PRD §8):
  - poll EDGAR  every ~90s
  - poll Finnhub every ~180s
  - daily digest at the owner-set SGT time (reschedulable via /digest)

Shared state (cfg, publisher, sources, anthropic client) lives in
``application.bot_data`` so jobs and command handlers see the same objects.
"""

from __future__ import annotations

import time
from datetime import time as dtime

from telegram.ext import Application, ContextTypes

from . import db, ingest, publisher as publisher_mod, research, soul
from .config import (
    ALPACA_POLL_SECONDS,
    EARNINGS_POLL_SECONDS,
    EDGAR_POLL_SECONDS,
    FED_RSS_POLL_SECONDS,
    FINNHUB_POLL_SECONDS,
    GOOGLE_NEWS_POLL_SECONDS,
    HALTS_POLL_SECONDS,
    INSIDER_POLL_SECONDS,
    MACRO_POLL_SECONDS,
    PRICEMOVE_POLL_SECONDS,
    RATINGS_POLL_SECONDS,
    RESEARCH_AGENT_INTERVAL,
    SGT,
    SOUL_REVIEW_INTERVAL,
    YAHOO_NEWS_POLL_SECONDS,
)

DIGEST_JOB = "daily_digest"


async def _poll(context: ContextTypes.DEFAULT_TYPE, source_name: str) -> None:
    bot_data = context.application.bot_data
    cfg = bot_data["cfg"]
    source = bot_data["sources"].get(source_name)
    if source is None:
        return

    async def _alert(msg: str) -> None:
        # Best-effort owner DM on source failure/recovery or a failed channel post.
        try:
            await context.application.bot.send_message(chat_id=cfg.owner_user_id, text=msg)
        except Exception:
            pass

    await ingest.run_cycle(
        cfg, [source], bot_data["publisher"], time.time(), bot_data.get("client"),
        alert=_alert,
    )


async def poll_edgar(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "edgar")


async def poll_finnhub(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "finnhub")


async def poll_google_news(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "google_news")


async def poll_yahoo_news(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "yahoo_news")


async def poll_earnings(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "earnings")


async def poll_macro(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "macro")


async def poll_insider(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "insider")


async def poll_fed(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "fed")


async def poll_halts(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "halts")


async def poll_ratings(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "ratings")


async def poll_pricemove(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "pricemove")


async def poll_alpaca(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "alpaca")


async def run_research_agent(context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_data = context.application.bot_data
    cfg = bot_data["cfg"]
    if not cfg.anthropic_api_key or not cfg.enable_research_agent:
        return
    try:
        report = await research.run_autonomous(cfg.anthropic_api_key, cfg)
    except Exception as exc:
        report = f"⚠️ Research agent error: {exc}"
    await context.application.bot.send_message(chat_id=cfg.owner_user_id, text=report)


async def soul_review_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Weekly: distil accumulated feedback (good rewrites + muted topics) into an
    updated house voice. One cheap LLM call; only runs when there's new feedback
    and a client. Auto-applied (previous kept as soul.prev.md), owner notified."""
    bot_data = context.application.bot_data
    cfg = bot_data["cfg"]
    client = bot_data.get("client")
    if client is None or not cfg.enable_feedback_learning or cfg.summary_mode != "llm":
        return
    corrections = db.recent_summary_examples(cfg.db_path, limit=20)
    muted = [
        f"${r['ticker']} {r['event_type']}"
        for r in db.active_suppression_rules(cfg.db_path)
    ]
    if not corrections and not muted:
        return  # nothing learned yet — don't spend a call
    current = soul.load(cfg)
    try:
        updated = soul.propose_update(client, current, corrections, muted, cfg.summary_model)
    except Exception as exc:  # never let a review error disturb the bot
        print(f"⚠️  Soul review failed: {type(exc).__name__}: {exc}")
        return
    if updated and updated.strip() and updated.strip() != current.strip():
        if soul.save(cfg, updated):
            try:
                await context.application.bot.send_message(
                    chat_id=cfg.owner_user_id,
                    text=(
                        "🧠 Refreshed the house voice from your recent feedback.\n"
                        "/soul to review · /soul_reset to revert."
                    ),
                )
            except Exception:
                pass


async def digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_data = context.application.bot_data
    await publisher_mod.run_digest(
        bot_data["cfg"], bot_data["publisher"], time.time()
    )


def schedule_digest(application: Application, hhmm: str) -> None:
    """(Re)schedule the daily digest at HHMM SGT."""
    jq = application.job_queue
    for job in jq.get_jobs_by_name(DIGEST_JOB):
        job.schedule_removal()
    hh, mm = int(hhmm[:2]), int(hhmm[2:])
    jq.run_daily(digest_job, time=dtime(hour=hh, minute=mm, tzinfo=SGT), name=DIGEST_JOB)


def setup_jobs(application: Application) -> None:
    cfg = application.bot_data["cfg"]
    jq = application.job_queue
    jq.run_repeating(poll_edgar, interval=EDGAR_POLL_SECONDS, first=10, name="poll_edgar")
    jq.run_repeating(
        poll_finnhub, interval=FINNHUB_POLL_SECONDS, first=20, name="poll_finnhub"
    )
    if "google_news" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_google_news,
            interval=GOOGLE_NEWS_POLL_SECONDS,
            first=40,
            name="poll_google_news",
        )
    if "yahoo_news" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_yahoo_news,
            interval=YAHOO_NEWS_POLL_SECONDS,
            first=50,
            name="poll_yahoo_news",
        )
    if "earnings" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_earnings, interval=EARNINGS_POLL_SECONDS, first=60, name="poll_earnings"
        )
    if "macro" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_macro, interval=MACRO_POLL_SECONDS, first=70, name="poll_macro"
        )
    if "insider" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_insider, interval=INSIDER_POLL_SECONDS, first=80, name="poll_insider"
        )
    if "fed" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_fed, interval=FED_RSS_POLL_SECONDS, first=90, name="poll_fed"
        )
    if "halts" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_halts, interval=HALTS_POLL_SECONDS, first=100, name="poll_halts"
        )
    if "ratings" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_ratings, interval=RATINGS_POLL_SECONDS, first=110, name="poll_ratings"
        )
    if "pricemove" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_pricemove, interval=PRICEMOVE_POLL_SECONDS, first=120, name="poll_pricemove"
        )
    if "alpaca" in application.bot_data["sources"]:
        jq.run_repeating(
            poll_alpaca, interval=ALPACA_POLL_SECONDS, first=130, name="poll_alpaca"
        )
    if cfg.anthropic_api_key and cfg.enable_research_agent:
        jq.run_repeating(
            run_research_agent,
            interval=RESEARCH_AGENT_INTERVAL,
            first=300,  # 5 min after start (not on cold boot)
            name="research_agent",
        )
        print("🔬 Research agent ENABLED (weekly autonomous source discovery).")
    if cfg.anthropic_api_key and cfg.enable_feedback_learning and cfg.summary_mode == "llm":
        jq.run_repeating(
            soul_review_job,
            interval=SOUL_REVIEW_INTERVAL,
            first=600,  # 10 min after start, then weekly
            name="soul_review",
        )
        print("🧠 Soul review ENABLED (weekly: distil feedback into the house voice).")
    schedule_digest(application, cfg.digest_time_sgt)
    application.bot_data["reschedule_digest"] = lambda hhmm: schedule_digest(
        application, hhmm
    )
