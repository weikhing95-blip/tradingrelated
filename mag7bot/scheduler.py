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

from . import ingest, publisher as publisher_mod, research
from .config import (
    EARNINGS_POLL_SECONDS,
    EDGAR_POLL_SECONDS,
    FED_RSS_POLL_SECONDS,
    FINNHUB_POLL_SECONDS,
    GOOGLE_NEWS_POLL_SECONDS,
    HALTS_POLL_SECONDS,
    INSIDER_POLL_SECONDS,
    MACRO_POLL_SECONDS,
    RATINGS_POLL_SECONDS,
    RESEARCH_AGENT_INTERVAL,
    SGT,
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
    if cfg.anthropic_api_key and cfg.enable_research_agent:
        jq.run_repeating(
            run_research_agent,
            interval=RESEARCH_AGENT_INTERVAL,
            first=300,  # 5 min after start (not on cold boot)
            name="research_agent",
        )
        print("🔬 Research agent ENABLED (weekly autonomous source discovery).")
    schedule_digest(application, cfg.digest_time_sgt)
    application.bot_data["reschedule_digest"] = lambda hhmm: schedule_digest(
        application, hhmm
    )
