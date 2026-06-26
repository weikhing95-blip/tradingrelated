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

from . import ingest, publisher as publisher_mod
from .config import (
    EDGAR_POLL_SECONDS,
    FINNHUB_POLL_SECONDS,
    GOOGLE_NEWS_POLL_SECONDS,
    SGT,
)

DIGEST_JOB = "daily_digest"


async def _poll(context: ContextTypes.DEFAULT_TYPE, source_name: str) -> None:
    bot_data = context.application.bot_data
    cfg = bot_data["cfg"]
    source = bot_data["sources"].get(source_name)
    if source is None:
        return
    await ingest.run_cycle(
        cfg, [source], bot_data["publisher"], time.time(), bot_data.get("client")
    )


async def poll_edgar(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "edgar")


async def poll_finnhub(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "finnhub")


async def poll_google_news(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _poll(context, "google_news")


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
    schedule_digest(application, cfg.digest_time_sgt)
    application.bot_data["reschedule_digest"] = lambda hhmm: schedule_digest(
        application, hhmm
    )
