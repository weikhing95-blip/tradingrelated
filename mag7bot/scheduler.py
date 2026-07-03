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

from . import (
    backup as backup_mod,
    db,
    economic_calendar,
    ingest,
    publisher as publisher_mod,
    research,
    soul,
)
from .pipeline import formatter
from .config import (
    is_commercial_safe,
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
    # External liveness: ping the healthcheck URL at the end of a successful
    # poll cycle so an outside monitor alerts if the worker dies. Fail-soft.
    await _healthcheck_ping(cfg)


async def _healthcheck_ping(cfg) -> None:
    """Ping HEALTHCHECK_PING_URL (fail-soft) and record the time for /status.
    A dead/slow monitor endpoint must never disturb the poll loop."""
    if not cfg.healthcheck_ping_url:
        return
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(cfg.healthcheck_ping_url)
        db.set_meta(cfg.db_path, "last_healthcheck", str(int(time.time())), time.time())
    except Exception:
        pass


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


async def backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nightly: snapshot the DB off-volume. With no object store configured, the
    fallback is to DM the gzipped backup to the owner (it's small). Best-effort —
    a backup failure is logged and never disturbs the bot. Records last-backup
    time only on successful delivery (an undelivered on-volume copy is no safer)."""
    bot_data = context.application.bot_data
    cfg = bot_data["cfg"]
    if not cfg.enable_backup or not cfg.owner_user_id:
        return
    try:
        gz = backup_mod.create_backup(cfg.db_path)
    except Exception as exc:
        print(f"⚠️  DB backup failed: {type(exc).__name__}: {exc}")
        return
    try:
        with open(gz, "rb") as fh:
            await context.application.bot.send_document(
                chat_id=cfg.owner_user_id, document=fh, filename=gz.name,
                caption="🗄 Nightly DB backup — keep this to restore after a volume loss.",
            )
        db.set_meta(cfg.db_path, "last_backup", str(int(time.time())), time.time())
    except Exception as exc:
        print(f"⚠️  DB backup DM failed: {type(exc).__name__}: {exc}")
    finally:
        try:
            gz.unlink()
        except OSError:
            pass


async def digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_data = context.application.bot_data
    await publisher_mod.run_digest(
        bot_data["cfg"], bot_data["publisher"], time.time()
    )


async def weekly_roundup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Weekly "what you missed" post: the week's top events by tier, shareable
    (5A-03). Registered daily; only fires on the configured weekday, so the
    weekday check is unambiguous regardless of the JobQueue day convention."""
    from datetime import datetime

    bot_data = context.application.bot_data
    cfg = bot_data["cfg"]
    now = time.time()
    if datetime.fromtimestamp(now, tz=SGT).weekday() != cfg.weekly_roundup_day:
        return
    events = db.top_events_since(cfg.db_path, cfg.feed_id, now - 7 * 86400, limit=12)
    text = formatter.format_weekly_roundup(events, now, cfg.public_channel_handle)
    try:
        await bot_data["publisher"].send_digest(text)
    except Exception as exc:
        print(f"⚠️  Weekly roundup post failed: {type(exc).__name__}: {exc}")


async def econ_calendar_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily forward preview of the day's high-impact macro releases (free
    ForexFactory feed). Posts a single digest to the channel; skips quietly if
    the day has no qualifying events."""
    bot_data = context.application.bot_data
    cfg = bot_data["cfg"]
    from datetime import datetime
    day = datetime.fromtimestamp(time.time(), tz=SGT)
    try:
        text = await economic_calendar.build_daily_preview(
            day, cfg.econ_calendar_currencies, cfg.econ_calendar_impacts
        )
    except Exception as exc:
        print(f"⚠️  Economic-calendar preview failed: {type(exc).__name__}: {exc}")
        return
    if text:
        await bot_data["publisher"].send_digest(text)


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
    # Econ calendar (ForexFactory) is a VERIFY source — not in the commercially-
    # safe set — so it is hard-disabled in PUBLIC_MODE until its ToS is cleared
    # (see docs/SOURCE_LICENSES.md).
    if cfg.enable_econ_calendar and (not cfg.public_mode or is_commercial_safe("econ_calendar")):
        hh, mm = int(cfg.econ_calendar_time_sgt[:2]), int(cfg.econ_calendar_time_sgt[2:])
        jq.run_daily(
            econ_calendar_job, time=dtime(hour=hh, minute=mm, tzinfo=SGT),
            name="econ_calendar",
        )
        print(f"📅 Economic-calendar preview ENABLED (daily {cfg.econ_calendar_time_sgt} SGT).")
    elif cfg.enable_econ_calendar and cfg.public_mode:
        print("📅 Economic-calendar preview OFF — disabled in PUBLIC_MODE (source ToS not yet cleared).")
    if cfg.enable_backup and cfg.owner_user_id:
        bh, bm = int(cfg.backup_time_sgt[:2]), int(cfg.backup_time_sgt[2:])
        jq.run_daily(
            backup_job, time=dtime(hour=bh, minute=bm, tzinfo=SGT), name="db_backup",
        )
        print(f"🗄 Nightly DB backup ENABLED (daily {cfg.backup_time_sgt} SGT → owner DM).")
    if cfg.healthcheck_ping_url:
        print("💓 External healthcheck ping ENABLED (each successful poll cycle).")
    if cfg.enable_weekly_roundup:
        wh, wm = int(cfg.weekly_roundup_time_sgt[:2]), int(cfg.weekly_roundup_time_sgt[2:])
        jq.run_daily(
            weekly_roundup_job, time=dtime(hour=wh, minute=wm, tzinfo=SGT),
            name="weekly_roundup",
        )
        _dayname = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][cfg.weekly_roundup_day % 7]
        print(f"🗞 Weekly roundup ENABLED ({_dayname} {cfg.weekly_roundup_time_sgt} SGT).")
    schedule_digest(application, cfg.digest_time_sgt)
    application.bot_data["reschedule_digest"] = lambda hhmm: schedule_digest(
        application, hhmm
    )
