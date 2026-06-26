"""Configuration for the Mag 7 News Bot.

All secrets and tunables come from the environment (load a local ``.env`` if
present — see ``.env.example``). Nothing here is committed. ``load_config()``
returns a frozen ``Config`` and fails loudly when a required value is missing,
*except* in dry-run mode where credentials aren't needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------- #
# Constants                                                                     #
# --------------------------------------------------------------------------- #

MODEL = "claude-opus-4-8"  # only used when SUMMARY_MODE=llm

# Singapore time — all user-facing timestamps and digest scheduling are in SGT.
SGT = ZoneInfo("Asia/Singapore")

# Default watchlist (PRD §2): the Magnificent Seven. User-editable at runtime.
DEFAULT_WATCHLIST: List[str] = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]

# Quiet hours (SGT) — no instant push unless CRITICAL (PRD §10).
QUIET_START_HOUR = 0
QUIET_END_HOUR = 7

# Dedup window — collapse same story across sources within this many hours.
DEDUP_WINDOW_HOURS = 6

# Polling intervals in seconds (PRD §8 suggests EDGAR 1–2 min, news 2–5 min).
EDGAR_POLL_SECONDS = 90
FINNHUB_POLL_SECONDS = 180
GOOGLE_NEWS_POLL_SECONDS = 300  # only used when ENABLE_GOOGLE_NEWS=true
YAHOO_NEWS_POLL_SECONDS = 240  # only used when ENABLE_YAHOO_NEWS=true

# Domain whitelist (PRD §4 hard rule). Anything outside this set is dropped.
# Keys are publisher names/domains as they appear in source `source`/`publisher`
# fields, normalised to lowercase. EDGAR and company IR are implicitly Tier 1.
WHITELIST_DOMAINS: List[str] = [
    # Tier 1 — primary
    "sec.gov",
    "edgar",
    # Tier 2 — reputable wire
    "reuters",
    "reuters.com",
    "bloomberg",
    "bloomberg.com",
    "ap",
    "apnews.com",
    "associated press",
    "cnbc",
    "cnbc.com",
    "wsj",
    "wsj.com",
    "the wall street journal",
    "ft",
    "ft.com",
    "financial times",
    # Reputable finance press / aggregators
    "finance.yahoo.com",
    "yahoo.com",
    "yahoo finance",
    # Tier 3 — aggregated events / analyst feeds
    "marketwatch",
    "marketwatch.com",
    "seekingalpha.com",
    "businesswire",
    "businesswire.com",
    "globenewswire",
    "globenewswire.com",
    "prnewswire",
    "prnewswire.com",
]


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    # Telegram
    telegram_bot_token: str
    owner_user_id: int
    channel_id: str

    # Sources
    finnhub_api_key: str
    sec_edgar_user_agent: str

    # Behaviour
    digest_time_sgt: str  # "HHMM", e.g. "0900"
    summary_mode: str  # "verbatim" | "llm"
    anthropic_api_key: Optional[str]

    # Storage
    db_path: Path

    # Sources (optional)
    enable_google_news: bool = False
    enable_yahoo_news: bool = False

    # Mode
    dry_run: bool = False
    feed_id: int = 1  # MVP has exactly one feed

    quiet_start_hour: int = QUIET_START_HOUR
    quiet_end_hour: int = QUIET_END_HOUR
    whitelist: List[str] = field(default_factory=lambda: list(WHITELIST_DOMAINS))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no third-party dep). Ignores comments/blank lines."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Don't clobber values already exported in the real environment.
        os.environ.setdefault(key, value)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name!r}. "
            f"Set it (or add it to .env). See .env.example."
        )
    return value


def load_config(dry_run: bool = False) -> Config:
    """Build a Config from the environment.

    In ``dry_run`` mode the Telegram/Finnhub/EDGAR credentials are optional —
    the offline pipeline (fixtures → formatted output) needs none of them.
    """
    _load_dotenv(Path.cwd() / ".env")

    db_path = Path(os.environ.get("DB_PATH", "mag7bot.db")).expanduser()
    summary_mode = os.environ.get("SUMMARY_MODE", "verbatim").strip().lower()
    if summary_mode not in ("verbatim", "llm"):
        raise RuntimeError("SUMMARY_MODE must be 'verbatim' or 'llm'.")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
    if summary_mode == "llm" and not anthropic_key and not dry_run:
        raise RuntimeError("SUMMARY_MODE=llm requires ANTHROPIC_API_KEY.")
    _truthy = ("1", "true", "yes", "on")
    enable_google_news = os.environ.get("ENABLE_GOOGLE_NEWS", "").strip().lower() in _truthy
    enable_yahoo_news = os.environ.get("ENABLE_YAHOO_NEWS", "").strip().lower() in _truthy

    if dry_run:
        return Config(
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            owner_user_id=int(os.environ.get("OWNER_USER_ID", "0") or "0"),
            channel_id=os.environ.get("CHANNEL_ID", ""),
            finnhub_api_key=os.environ.get("FINNHUB_API_KEY", ""),
            sec_edgar_user_agent=os.environ.get(
                "SEC_EDGAR_USER_AGENT", "mag7bot dry-run (example@example.com)"
            ),
            digest_time_sgt=os.environ.get("DIGEST_TIME_SGT", "0900").strip(),
            summary_mode=summary_mode,
            anthropic_api_key=anthropic_key,
            db_path=db_path,
            dry_run=True,
            enable_google_news=enable_google_news,
            enable_yahoo_news=enable_yahoo_news,
        )

    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        owner_user_id=int(_require("OWNER_USER_ID")),
        channel_id=_require("CHANNEL_ID"),
        finnhub_api_key=_require("FINNHUB_API_KEY"),
        sec_edgar_user_agent=_require("SEC_EDGAR_USER_AGENT"),
        digest_time_sgt=os.environ.get("DIGEST_TIME_SGT", "0900").strip(),
        summary_mode=summary_mode,
        anthropic_api_key=anthropic_key,
        db_path=db_path,
        dry_run=False,
        enable_google_news=enable_google_news,
        enable_yahoo_news=enable_yahoo_news,
    )
