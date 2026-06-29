"""Entry point for the Mag 7 News Bot.

Two modes:

    python -m mag7bot.app              # live: bot + polling + daily digest
    python -m mag7bot.app --dry-run    # offline: run fixtures through the
                                       # whole pipeline, print to stdout, no creds

Live mode wires the PTB Application: command handlers (owner-DM control plane),
the channel publisher, and the JobQueue polling/digest jobs. Dry-run proves the
verified-link promise with zero credentials (mirrors market.py's mock ethos).
"""

from __future__ import annotations

import argparse
import asyncio
import time

from . import db, ingest, publisher as publisher_mod, seed
from .config import Config, DEFAULT_TELEGRAM_CHANNELS, load_config
from .schemas import SentMode
from .sources import fixtures

# NOTE: telegram-dependent modules (commands, scheduler, sources adapters that
# only matter live) are imported lazily inside run_live so the offline
# --dry-run path needs no Telegram/crypto stack.


def _make_client(cfg: Config):
    if cfg.summary_mode != "llm":
        return None
    try:
        import anthropic  # imported lazily so verbatim mode needs no SDK creds

        client = anthropic.Anthropic()
        print(f"🤖 LLM summaries ON (model={cfg.summary_model}).")
        return client
    except Exception as exc:
        # Missing/invalid ANTHROPIC_API_KEY or SDK import error must NOT take the
        # bot down — fall back to free verbatim summaries and keep publishing.
        print(
            f"⚠️  SUMMARY_MODE=llm but the Anthropic client failed to init ({exc}). "
            f"Falling back to verbatim summaries. Check ANTHROPIC_API_KEY in the env."
        )
        cfg.summary_mode = "verbatim"
        return None


async def _dry_run(cfg: Config) -> None:
    seed.seed(cfg)
    now = time.time()
    client = _make_client(cfg) if cfg.anthropic_api_key else None
    pub = publisher_mod.StdoutPublisher()

    print(f"Dry run — summary_mode={cfg.summary_mode}, db={cfg.db_path}")
    events = ingest.build_events(cfg, fixtures.sample_raw_items(now), now, client)

    for event in events:
        if event.id is not None and ingest.should_push_now(cfg, event, now):
            await pub.push(event)
            db.mark_event_sent(cfg.db_path, event.id, SentMode.PUSH)

    await publisher_mod.run_digest(cfg, pub, now)
    print(f"\nSummary: {len(pub.pushed)} instant push(es), 1 digest compiled.")


async def preflight(bot, cfg: Config) -> bool:
    """Verify the bot can do its job before polling: token valid, channel
    reachable, and the bot can post there. Prints a clear ✅/⚠️/❌ per check
    and returns True only if nothing is blocking."""
    ok = True
    try:
        me = await bot.get_me()
        print(f"✅ Token valid — bot is @{me.username} (id {me.id})")
    except Exception as exc:  # invalid token / network
        print(f"❌ Token check failed: {exc}")
        return False

    try:
        chat = await bot.get_chat(cfg.channel_id)
        label = getattr(chat, "title", None) or chat.id
        print(f"✅ Channel reachable — {label} (type {chat.type})")
    except Exception as exc:
        print(
            f"❌ Channel {cfg.channel_id} not reachable: {exc}\n"
            f"   Check the id is correct and the bot was added to the channel."
        )
        return False

    try:
        member = await bot.get_chat_member(cfg.channel_id, me.id)
        status = getattr(member, "status", "")
        can_post = getattr(member, "can_post_messages", True)
        if status == "administrator" and can_post:
            print("✅ Bot is a channel admin and can post messages.")
        elif status == "administrator":
            print("⚠️ Bot is an admin but 'Post Messages' is OFF — enable it.")
            ok = False
        else:
            print(
                f"❌ Bot is not a channel admin (status={status!r}). "
                f"Add it as an admin with 'Post Messages' permission."
            )
            ok = False
    except Exception as exc:
        print(f"⚠️ Could not verify posting rights: {exc}")
    return ok


async def _post_init(application) -> None:
    cfg = application.bot_data["cfg"]
    await preflight(application.bot, cfg)

    # Seed default Telegram channels on first start (idempotent).
    for username, name in DEFAULT_TELEGRAM_CHANNELS:
        db.add_telegram_channel(cfg.db_path, username, name, added_by="default")

    # Prime any source with no history yet (cold start, or a newly enabled
    # source like Google News added to a warm DB): mark its current items as
    # seen WITHOUT alerting, so enabling it doesn't replay days-old news.
    sources = application.bot_data["sources"]
    fresh = [s for name, s in sources.items() if db.seen_count_for_source(cfg.db_path, name) == 0]
    if fresh:
        primed = await ingest.prime(cfg, fresh)
        names = ", ".join(s.name for s in fresh)
        print(
            f"🟢 Primed {primed} pre-existing item(s) as seen across [{names}] "
            f"(no alerts). New events from now on will be pushed/digested."
        )

    # Start Telegram channel monitor if credentials are configured. Any failure
    # here must NOT take the bot down, so it's fully guarded.
    if cfg.telegram_api_id and cfg.telegram_api_hash:
        try:
            from pathlib import Path

            from .telegram_monitor import TelegramChannelMonitor

            # Must match the path telegram_setup.py writes (telegram_user.session
            # next to the DB), so the session created at setup is found here.
            session_path = (
                Path(cfg.telegram_session_path)
                if cfg.telegram_session_path
                else cfg.db_path.with_name("telegram_user.session")
            )
            monitor = TelegramChannelMonitor(
                api_id=cfg.telegram_api_id,
                api_hash=cfg.telegram_api_hash,
                session_path=session_path,
                cfg=cfg,
                publisher=application.bot_data["publisher"],
                llm_client=application.bot_data.get("client"),
            )
            asyncio.create_task(monitor.start())
            print("📡 Telegram channel monitor STARTING (pyrogram user client).")
        except Exception as exc:  # ImportError, bad session, etc. — never fatal
            print(f"⚠️  Telegram channel monitor not started: {exc}")
    else:
        print("📡 Telegram channel monitor OFF (set TELEGRAM_API_ID + TELEGRAM_API_HASH to enable).")


async def _check(cfg: Config) -> None:
    from telegram import Bot

    bot = Bot(cfg.telegram_bot_token)
    try:
        await bot.initialize()  # PTB validates the token here (a get_me call)
    except Exception as exc:
        print(f"❌ Could not connect to Telegram (token or network): {exc}")
        print("\nPreflight: ISSUES FOUND ❌")
        return
    try:
        ok = await preflight(bot, cfg)
    finally:
        await bot.shutdown()
    print("\nPreflight:", "PASS ✅" if ok else "ISSUES FOUND ❌")


def run_live(cfg: Config) -> None:
    from telegram.ext import Application

    from . import commands, scheduler
    from .sources import (
        EarningsSource,
        EdgarSource,
        FedSource,
        FinnhubSource,
        GoogleNewsSource,
        InsiderSource,
        MacroSource,
        YahooNewsSource,
    )

    seed.seed(cfg)
    application = (
        Application.builder().token(cfg.telegram_bot_token).post_init(_post_init).build()
    )

    application.bot_data["cfg"] = cfg
    application.bot_data["client"] = _make_client(cfg)
    application.bot_data["publisher"] = publisher_mod.ChannelPublisher(
        application.bot, cfg.channel_id, cfg.finnhub_api_key
    )
    # Live whitelist = config defaults + owner-approved additions (DB).
    whitelist_provider = lambda: ingest.effective_whitelist(cfg)  # noqa: E731
    sources = {
        "edgar": EdgarSource(cfg.sec_edgar_user_agent),
        "finnhub": FinnhubSource(cfg.finnhub_api_key),
        "earnings": EarningsSource(cfg.finnhub_api_key),  # actuals vs estimates
    }
    if cfg.enable_google_news:
        sources["google_news"] = GoogleNewsSource(whitelist_provider)
        print("📰 Google News source ENABLED (whitelist-filtered aggregator).")
    if cfg.enable_yahoo_news:
        sources["yahoo_news"] = YahooNewsSource(whitelist_provider)
        print("📰 Yahoo Finance source ENABLED (whitelist-filtered).")
    if cfg.fred_api_key:
        sources["macro"] = MacroSource(cfg.fred_api_key)
        print("📊 Macro source ENABLED (FRED: CPI/PCE/PPI/NFP/jobless claims).")
    else:
        print("📊 Macro source OFF (set FRED_API_KEY to enable economic releases).")
    if cfg.enable_insider:
        sources["insider"] = InsiderSource(cfg.finnhub_api_key)
        print("👤 Insider source ENABLED (Finnhub: large open-market trades ≥$1M).")
    if cfg.enable_fed_rss:
        sources["fed"] = FedSource()
        print("🏦 Fed RSS source ENABLED (FOMC decisions + governor speeches).")
    application.bot_data["sources"] = sources

    commands.register(application)
    scheduler.setup_jobs(application)

    print("Mag 7 News Bot is live. Control via the owner DM; alerts to the channel.")
    application.run_polling(allowed_updates=["message"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run fixtures through the pipeline and print output (no credentials).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify token + channel access and exit (no polling).",
    )
    args = parser.parse_args()

    if args.dry_run:
        asyncio.run(_dry_run(load_config(dry_run=True)))
    elif args.check:
        asyncio.run(_check(load_config(dry_run=False)))
    else:
        run_live(load_config(dry_run=False))


if __name__ == "__main__":
    main()
