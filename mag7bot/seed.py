"""Initialise the database and seed reference + config rows.

Idempotent: safe to run repeatedly. Seeds the `companies` reference table, the
single `feed` config row (owner/channel/quiet hours), and the default Mag7
watchlist. In dry-run mode owner/channel may be blank — that's fine, the
offline pipeline never publishes.
"""

from __future__ import annotations

from . import companies, db
from .config import Config


def seed(cfg: Config) -> None:
    db.init_db(cfg.db_path)

    # Reference companies (rarely changes).
    for c in companies.MAG7.values():
        db.upsert_company(cfg.db_path, c.ticker, c.name, c.cik, c.sector, c.groups)

    # The single feed config row (PRD §9: MVP has exactly one).
    db.upsert_feed(
        cfg.db_path,
        feed_id=cfg.feed_id,
        owner_user_id=cfg.owner_user_id,
        channel_id=cfg.channel_id,
        digest_time_sgt=cfg.digest_time_sgt,
        quiet_start=cfg.quiet_start_hour,
        quiet_end=cfg.quiet_end_hour,
    )

    # Default watchlist (only adds tickers not already present).
    existing = set(db.watchlist_tickers(cfg.db_path, cfg.feed_id))
    for c in companies.MAG7.values():
        if c.ticker not in existing:
            db.add_ticker(cfg.db_path, cfg.feed_id, c.ticker, c.cik)
