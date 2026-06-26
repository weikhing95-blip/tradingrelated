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
from .config import Config, load_config
from .schemas import SentMode
from .sources import fixtures

# NOTE: telegram-dependent modules (commands, scheduler, sources adapters that
# only matter live) are imported lazily inside run_live so the offline
# --dry-run path needs no Telegram/crypto stack.


def _make_client(cfg: Config):
    if cfg.summary_mode != "llm":
        return None
    import anthropic  # imported lazily so verbatim mode needs no SDK creds

    return anthropic.Anthropic()


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


def run_live(cfg: Config) -> None:
    from telegram.ext import Application

    from . import commands, scheduler
    from .sources import EdgarSource, FinnhubSource

    seed.seed(cfg)
    application = Application.builder().token(cfg.telegram_bot_token).build()

    application.bot_data["cfg"] = cfg
    application.bot_data["client"] = _make_client(cfg)
    application.bot_data["publisher"] = publisher_mod.ChannelPublisher(
        application.bot, cfg.channel_id
    )
    application.bot_data["sources"] = {
        "edgar": EdgarSource(cfg.sec_edgar_user_agent),
        "finnhub": FinnhubSource(cfg.finnhub_api_key),
    }

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
    args = parser.parse_args()

    cfg = load_config(dry_run=args.dry_run)
    if args.dry_run:
        asyncio.run(_dry_run(cfg))
    else:
        run_live(cfg)


if __name__ == "__main__":
    main()
