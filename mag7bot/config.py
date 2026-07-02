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

MODEL = "claude-opus-4-8"  # research agent / structured reasoning
# Cheap, fast model for high-volume summarisation + classification (SUMMARY_MODE=llm).
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Singapore time — all user-facing timestamps and digest scheduling are in SGT.
SGT = ZoneInfo("Asia/Singapore")

# Default watchlist (PRD §2): the Magnificent Seven. User-editable at runtime.
DEFAULT_WATCHLIST: List[str] = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]

# Quiet hours (SGT) — no instant push unless CRITICAL (PRD §10).
QUIET_START_HOUR = 0
QUIET_END_HOUR = 7

# Dedup window — collapse same story across sources within this many hours.
DEDUP_WINDOW_HOURS = 6

# Recency guard — never surface items older than this (aggregators resurface
# evergreen listicles/opinion with old publish dates). Tier-1 filings are exempt.
MAX_ITEM_AGE_HOURS = 24  # default freshness window for news (hours); override via env

# Polling intervals in seconds (PRD §8 suggests EDGAR 1–2 min, news 2–5 min).
EDGAR_POLL_SECONDS = 90
FINNHUB_POLL_SECONDS = 180
GOOGLE_NEWS_POLL_SECONDS = 300  # only used when ENABLE_GOOGLE_NEWS=true
YAHOO_NEWS_POLL_SECONDS = 240  # only used when ENABLE_YAHOO_NEWS=true
EARNINGS_POLL_SECONDS = 900  # Finnhub earnings calendar (uses the Finnhub key)
MACRO_POLL_SECONDS = 1800  # FRED macro releases (only when FRED_API_KEY is set)
INSIDER_POLL_SECONDS = 1800  # Finnhub insider transactions (large trades)
FED_RSS_POLL_SECONDS = 300   # Federal Reserve RSS (speeches + FOMC press releases)
HALTS_POLL_SECONDS = 60      # Nasdaq trading-halts RSS (free; time-critical)
RATINGS_POLL_SECONDS = 600   # Finnhub analyst upgrade/downgrade feed
ALPACA_POLL_SECONDS = 120    # Alpaca (Benzinga) real-time news — full article bodies
PRICEMOVE_POLL_SECONDS = 300  # Yahoo chart API — unusual intraday-move check

# Unusual price-move detector defaults. Flag when |move on the day| is at least
# MULTIPLIER × the trailing 2-week average daily move AND clears the MIN_PCT
# floor. LOOKBACK is the number of prior trading sessions in the baseline.
PRICE_MOVE_MULTIPLIER = 2.0
PRICE_MOVE_MIN_PCT = 3.0
PRICE_MOVE_LOOKBACK_DAYS = 10

# Large-insider-trade threshold: trades above this value get an instant push.
INSIDER_THRESHOLD_USD = 1_000_000  # $1M+

# Non-blocking curation learning: after this many distinct 👎 ("not useful")
# on the same (ticker, event_type), auto-promote a suppression rule.
FEEDBACK_SUPPRESS_THRESHOLD = 1

# Source health: only DM the owner once a source has failed this many polls in a
# row (a sustained outage), so a single transient network blip that heals on the
# next poll stays silent. Failures are always logged regardless.
SOURCE_ALERT_THRESHOLD = 3

# Channel post: retry a transient network/timeout failure this many times (with
# exponential backoff) before giving up, so one slow round-trip to Telegram
# doesn't drop an alert or ping the owner.
CHANNEL_SEND_ATTEMPTS = 3

# Autonomous research agent: how often to run source discovery (in seconds).
RESEARCH_AGENT_INTERVAL = 7 * 24 * 3600  # weekly

# Soul review: how often the LLM distils accumulated feedback into the house
# voice (one cheap call per run; only fires when there's new feedback).
SOUL_REVIEW_INTERVAL = 7 * 24 * 3600  # weekly

# Default Telegram channels to seed on first start.
DEFAULT_TELEGRAM_CHANNELS = [
    ("@WalterBloomberg", "Walter Bloomberg"),
    ("@SM_News_24h", "SM News"),  # verified handle (t.me/SM_News_24h)
    ("@thekobeissiletter", "The Kobeissi Letter"),
]

# Macro series polled from FRED (PRD: CPI, Core CPI, PCE, Core PCE, PPI, claims).
# kind="index" → report MoM/YoY %; kind="level" → report value + change.
MACRO_SERIES = [
    {"id": "CPILFESL", "label": "US Core CPI", "kind": "index"},
    {"id": "CPIAUCSL", "label": "US CPI", "kind": "index"},
    {"id": "PCEPILFE", "label": "US Core PCE", "kind": "index"},
    {"id": "PCEPI", "label": "US PCE", "kind": "index"},
    {"id": "PPIFIS", "label": "US PPI (final demand)", "kind": "index"},
    {"id": "ICSA", "label": "US Initial Jobless Claims", "kind": "level", "freq": "weekly"},
    {"id": "PAYEMS", "label": "US Nonfarm Payrolls", "kind": "level", "freq": "monthly"},
]

# Domain whitelist (PRD §4 hard rule). Anything outside this set is dropped.
# Maintained in data/whitelist_domains.txt (one lowercase name/domain per line,
# '#' comments allowed) rather than inline, so curating it isn't a code change.
# Runtime additions live in the DB whitelist_extra table.
def _load_whitelist_domains() -> List[str]:
    path = Path(__file__).with_name("data") / "whitelist_domains.txt"
    entries: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            entries.append(line)
    return entries


WHITELIST_DOMAINS: List[str] = _load_whitelist_domains()


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
    fred_api_key: str  # enables the macro source when non-empty
    alpaca_api_key: str  # enables the Alpaca (Benzinga) news source when set
    alpaca_secret_key: str

    # Behaviour
    digest_time_sgt: str  # "HHMM", e.g. "0900"
    summary_mode: str  # "verbatim" | "llm"
    anthropic_api_key: Optional[str]
    summary_model: str  # model id for LLM summaries (default Haiku)
    feed_volume: str  # "firehose" | "moderate" | "low"
    quiet_hours_enabled: bool  # False = 24/7 (default for the Walter/SM-style feed)

    # Storage
    db_path: Path

    # Sources (optional)
    enable_google_news: bool = True   # whitelist-filtered aggregator; links decoded to publishers
    enable_yahoo_news: bool = True    # Yahoo Finance per-ticker news (free wire, real URLs)
    enable_insider: bool = True   # Finnhub large-insider-trade alerts (uses Finnhub key)
    enable_fed_rss: bool = True   # Fed speeches + FOMC press releases (free RSS)
    enable_trading_halts: bool = True   # Nasdaq trading-halts RSS (free, critical)
    enable_analyst_ratings: bool = True  # Finnhub upgrade/downgrade feed (uses Finnhub key)
    enable_article_fetch: bool = True   # fetch full article text for richer LLM summaries (llm mode)
    enable_alpaca: bool = True          # Alpaca (Benzinga) news — active only when keys are set
    enable_price_move: bool = True      # unusual intraday-move alerts (Yahoo chart; free)
    enable_feedback_learning: bool = True  # 👎 feedback buttons + learned suppression rules
    enable_semantic_dedup: bool = True  # LLM check for paraphrased dupes (llm mode; gated, cheap)
    public_channel: bool = False  # True → keep curation buttons off public posts (mirror to owner DM)
    enable_econ_calendar: bool = True   # daily forward macro-calendar preview (free)
    econ_calendar_time_sgt: str = "0700"  # when to post the daily macro preview (SGT)
    econ_calendar_currencies: tuple = ("USD", "EUR", "GBP", "JPY", "CNY")
    econ_calendar_impacts: tuple = ("High",)

    # Price-move detector thresholds (see constants above).
    price_move_multiplier: float = PRICE_MOVE_MULTIPLIER
    price_move_min_pct: float = PRICE_MOVE_MIN_PCT

    # Freshness: news items older than this (hours) are dropped, and news with
    # no usable timestamp is dropped too. Lower = fresher feed. SEC filings are
    # always allowed (inherently current).
    max_item_age_hours: int = MAX_ITEM_AGE_HOURS
    # Cross-source/cross-ticker dedup lookback window (hours).
    dedup_window_hours: int = DEDUP_WINDOW_HOURS

    # Telegram user client (for channel monitoring)
    telegram_api_id: int = 0          # from my.telegram.org
    telegram_api_hash: str = ""       # from my.telegram.org
    telegram_session_path: str = ""   # path to pyrogram .session file (auto-derived if empty)

    # Research agent
    enable_research_agent: bool = False  # weekly Opus source-discovery agent — OFF by default (costly)

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
        value = value.strip()
        # Strip inline or standalone comments:
        #   FOO=bar   # note  →  "bar"
        #   FOO=      # note  →  ""  (value IS the comment after stripping)
        if value.startswith("#"):
            value = ""
        else:
            comment_pos = value.find(" #")
            if comment_pos != -1:
                value = value[:comment_pos]
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
    _falsy = ("0", "false", "no", "off")
    enable_google_news = os.environ.get("ENABLE_GOOGLE_NEWS", "true").strip().lower() not in _falsy
    enable_yahoo_news = os.environ.get("ENABLE_YAHOO_NEWS", "true").strip().lower() not in _falsy
    enable_insider = os.environ.get("ENABLE_INSIDER", "true").strip().lower() not in _falsy
    enable_fed_rss = os.environ.get("ENABLE_FED_RSS", "true").strip().lower() not in _falsy
    enable_trading_halts = os.environ.get("ENABLE_TRADING_HALTS", "true").strip().lower() not in _falsy
    enable_analyst_ratings = os.environ.get("ENABLE_ANALYST_RATINGS", "true").strip().lower() not in _falsy
    enable_article_fetch = os.environ.get("ENABLE_ARTICLE_FETCH", "true").strip().lower() not in _falsy
    enable_alpaca = os.environ.get("ENABLE_ALPACA", "true").strip().lower() not in _falsy
    enable_price_move = os.environ.get("ENABLE_PRICE_MOVE", "true").strip().lower() not in _falsy
    enable_feedback_learning = os.environ.get("ENABLE_FEEDBACK_LEARNING", "true").strip().lower() not in _falsy
    enable_semantic_dedup = os.environ.get("ENABLE_SEMANTIC_DEDUP", "true").strip().lower() not in _falsy
    public_channel = os.environ.get("PUBLIC_CHANNEL", "").strip().lower() in _truthy
    enable_econ_calendar = os.environ.get("ENABLE_ECON_CALENDAR", "true").strip().lower() not in _falsy
    econ_calendar_time_sgt = (os.environ.get("ECON_CALENDAR_TIME_SGT", "0700").strip() or "0700")
    _ccy_raw = os.environ.get("ECON_CALENDAR_CURRENCIES", "USD,EUR,GBP,JPY,CNY")
    econ_calendar_currencies = tuple(
        c.strip().upper() for c in _ccy_raw.split(",") if c.strip()
    ) or ("USD", "EUR", "GBP", "JPY", "CNY")
    _imp_raw = os.environ.get("ECON_CALENDAR_IMPACT", "High")
    econ_calendar_impacts = tuple(
        i.strip().title() for i in _imp_raw.split(",") if i.strip()
    ) or ("High",)
    enable_research_agent = os.environ.get("ENABLE_RESEARCH_AGENT", "false").strip().lower() in _truthy

    def _float_env(name: str, default: float) -> float:
        try:
            val = float(os.environ.get(name, str(default)))
        except ValueError:
            return default
        return val if val > 0 else default

    price_move_multiplier = _float_env("PRICE_MOVE_MULTIPLIER", PRICE_MOVE_MULTIPLIER)
    price_move_min_pct = _float_env("PRICE_MOVE_MIN_PCT", PRICE_MOVE_MIN_PCT)

    tg_api_id_raw = os.environ.get("TELEGRAM_API_ID", "0").strip() or "0"
    tg_api_id = int(tg_api_id_raw) if tg_api_id_raw.isdigit() else 0
    tg_api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    tg_session = os.environ.get("TELEGRAM_SESSION_PATH", "").strip()

    summary_model = os.environ.get("SUMMARY_MODEL", HAIKU_MODEL).strip() or HAIKU_MODEL
    feed_volume = os.environ.get("FEED_VOLUME", "firehose").strip().lower()
    if feed_volume not in ("firehose", "moderate", "low"):
        feed_volume = "moderate"
    quiet_hours_enabled = os.environ.get("QUIET_HOURS", "").strip().lower() in _truthy
    def _int_env(name: str, default: int) -> int:
        try:
            val = int(os.environ.get(name, str(default)))
        except ValueError:
            return default
        return val if val >= 1 else default

    max_item_age_hours = _int_env("MAX_ITEM_AGE_HOURS", MAX_ITEM_AGE_HOURS)
    dedup_window_hours = _int_env("DEDUP_WINDOW_HOURS", DEDUP_WINDOW_HOURS)

    # Fields that are identical in both modes — defined once so the dry-run and
    # live branches can't drift apart. Only the credentials differ (optional in
    # dry-run, required live).
    common = dict(
        fred_api_key=os.environ.get("FRED_API_KEY", "").strip(),
        alpaca_api_key=os.environ.get("ALPACA_API_KEY", "").strip(),
        alpaca_secret_key=os.environ.get("ALPACA_SECRET_KEY", "").strip(),
        digest_time_sgt=os.environ.get("DIGEST_TIME_SGT", "0900").strip(),
        summary_mode=summary_mode,
        anthropic_api_key=anthropic_key,
        summary_model=summary_model,
        feed_volume=feed_volume,
        quiet_hours_enabled=quiet_hours_enabled,
        max_item_age_hours=max_item_age_hours,
        dedup_window_hours=dedup_window_hours,
        db_path=db_path,
        enable_google_news=enable_google_news,
        enable_yahoo_news=enable_yahoo_news,
        enable_insider=enable_insider,
        enable_fed_rss=enable_fed_rss,
        enable_trading_halts=enable_trading_halts,
        enable_analyst_ratings=enable_analyst_ratings,
        enable_article_fetch=enable_article_fetch,
        enable_alpaca=enable_alpaca,
        enable_price_move=enable_price_move,
        enable_feedback_learning=enable_feedback_learning,
        enable_semantic_dedup=enable_semantic_dedup,
        public_channel=public_channel,
        enable_econ_calendar=enable_econ_calendar,
        econ_calendar_time_sgt=econ_calendar_time_sgt,
        econ_calendar_currencies=econ_calendar_currencies,
        econ_calendar_impacts=econ_calendar_impacts,
        price_move_multiplier=price_move_multiplier,
        price_move_min_pct=price_move_min_pct,
        telegram_api_id=tg_api_id,
        telegram_api_hash=tg_api_hash,
        telegram_session_path=tg_session,
        enable_research_agent=enable_research_agent,
    )

    if dry_run:
        return Config(
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            owner_user_id=int(os.environ.get("OWNER_USER_ID", "0") or "0"),
            channel_id=os.environ.get("CHANNEL_ID", ""),
            finnhub_api_key=os.environ.get("FINNHUB_API_KEY", ""),
            sec_edgar_user_agent=os.environ.get(
                "SEC_EDGAR_USER_AGENT", "mag7bot dry-run (example@example.com)"
            ),
            dry_run=True,
            **common,
        )

    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        owner_user_id=int(_require("OWNER_USER_ID")),
        channel_id=_require("CHANNEL_ID"),
        finnhub_api_key=_require("FINNHUB_API_KEY"),
        sec_edgar_user_agent=_require("SEC_EDGAR_USER_AGENT"),
        dry_run=False,
        **common,
    )
