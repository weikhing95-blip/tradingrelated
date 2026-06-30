"""Shared schemas for the Mag 7 News Bot.

Two objects flow through the pipeline:

    source feed ──(sources/*)──► RawItem    (the firehose, exactly as fetched)
    RawItem(s)  ──(pipeline/*)─► Event      (deduped, classified, send-ready)

`RawItem` mirrors the `raw_items` table; `Event` mirrors the `events` table
(see db.py and PRD §9). Keeping them as Pydantic models gives us validation at
the boundary and a single source of truth for field names.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enumerations                                                                  #
# --------------------------------------------------------------------------- #


class Tier(str, Enum):
    """Source trust tier (PRD §4). Lower number = more authoritative."""

    PRIMARY = "tier1"  # SEC EDGAR, company IR — source of record
    WIRE = "tier2"  # Reuters/Bloomberg/AP/CNBC/WSJ/FT — reputable wire
    AGGREGATED = "tier3"  # analyst feeds, earnings calendars

    @property
    def label(self) -> str:
        return {
            Tier.PRIMARY: "Tier 1 — Primary",
            Tier.WIRE: "Tier 2 — Wire",
            Tier.AGGREGATED: "Tier 3 — Aggregated",
        }[self]


class EventType(str, Enum):
    """The event taxonomy from PRD §2, plus a catch-all."""

    EARNINGS = "earnings"
    SEC_FILING = "sec_filing"
    MA = "m&a"  # mergers & acquisitions / major corporate
    ANALYST = "analyst"  # rating changes, price targets
    LEGAL_REGULATORY = "legal_regulatory"
    MANAGEMENT_CHANGE = "management_change"
    PRODUCT_LAUNCH = "product_launch"
    INDEX_LISTING = "index_listing"
    EXEC_COMMENTARY = "exec_commentary"  # CEO/CFO interviews, earnings-call remarks
    MACRO = "macro"  # economic data releases (CPI, PCE, jobless claims, ...)
    TRADING_HALT = "trading_halt"  # exchange trading halt / resumption
    PRICE_MOVE = "price_move"  # unusual intraday move vs the stock's own baseline
    NEWS = "news"  # unclassified / general

    @property
    def emoji(self) -> str:
        """Emoji legend per PRD §6."""
        return {
            EventType.EARNINGS: "🟢",
            EventType.SEC_FILING: "🔵",
            EventType.ANALYST: "🟡",
            EventType.MA: "🔴",
            EventType.LEGAL_REGULATORY: "⚖️",
            EventType.MANAGEMENT_CHANGE: "🔴",
            EventType.PRODUCT_LAUNCH: "🟢",
            EventType.INDEX_LISTING: "🔵",
            EventType.EXEC_COMMENTARY: "🎙",
            EventType.MACRO: "📊",
            EventType.TRADING_HALT: "🛑",
            EventType.PRICE_MOVE: "📈",
            EventType.NEWS: "📰",
        }[self]

    @property
    def display(self) -> str:
        return {
            EventType.EARNINGS: "Earnings",
            EventType.SEC_FILING: "SEC Filing",
            EventType.MA: "Corporate / M&A",
            EventType.ANALYST: "Analyst Action",
            EventType.LEGAL_REGULATORY: "Legal / Regulatory",
            EventType.MANAGEMENT_CHANGE: "Management Change",
            EventType.PRODUCT_LAUNCH: "Product / Launch",
            EventType.INDEX_LISTING: "Index / Listing",
            EventType.EXEC_COMMENTARY: "Exec Commentary",
            EventType.MACRO: "Economic Data",
            EventType.TRADING_HALT: "Trading Halt",
            EventType.PRICE_MOVE: "Price Move",
            EventType.NEWS: "News",
        }[self]


class Materiality(str, Enum):
    """How an event should be routed (PRD F3, F6)."""

    CRITICAL = "critical"  # push now; overrides quiet hours (halt, earnings, M&A)
    MATERIAL = "material"  # push now; suppressed during quiet hours
    LOW = "low"  # buffered into the daily digest

    @property
    def is_push(self) -> bool:
        return self in (Materiality.CRITICAL, Materiality.MATERIAL)


class SentMode(str, Enum):
    """Delivery state of an event row."""

    PENDING = "pending"  # not yet delivered (buffered for digest)
    PUSH = "push"  # delivered as an instant push
    DIGEST = "digest"  # delivered in a daily digest


# --------------------------------------------------------------------------- #
# Core models                                                                   #
# --------------------------------------------------------------------------- #


class RawItem(BaseModel):
    """The firehose, exactly as it arrived from a source (PRD §9 `raw_items`).

    `source_item_id` is the source's own stable id for idempotency (EDGAR
    accession number, Finnhub article id). `payload` keeps the original JSON so
    we can reprocess from raw if classification/summary logic changes later.
    """

    source: str = Field(description="Source name, e.g. 'edgar' or 'finnhub'.")
    source_item_id: str = Field(description="Source's stable id for this item.")
    ticker: str = Field(description="Watchlist ticker this item is about.")
    tier: Tier
    headline: str = Field(description="The item's own headline/title, verbatim.")
    body: str = Field(
        default="",
        description="Source's own summary/description blurb (article lede), if any. "
        "Used to produce a content summary instead of just the headline.",
    )
    url: str = Field(description="Canonical link to the primary/wire source.")
    publisher: str = Field(
        default="", description="Resolved publisher domain/name (for whitelist)."
    )
    published_at: float = Field(
        description="Source publication time, Unix epoch seconds (UTC)."
    )
    form_type: Optional[str] = Field(
        default=None, description="EDGAR form type (8-K, 10-Q, 4, ...) if applicable."
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict, description="Original source JSON, untouched."
    )


class Event(BaseModel):
    """A cleaned-up, send-ready item (PRD §9 `events`). The summary lives here."""

    ticker: str
    tickers: List[str] = Field(
        default_factory=list,
        description="All watchlist tickers the story is about (primary first); "
        "the alert header shows each. Falls back to [ticker] when empty.",
    )
    type: EventType
    summary: str = Field(description="One-line summary, ≤200 chars.")
    links: List[str] = Field(
        default_factory=list, description="Canonical link(s); 2+ when cross-confirmed."
    )
    tier: Tier
    source_name: str = Field(
        default="", description="Human source label for the §6 'Source:' line."
    )
    materiality: Materiality
    confirmed_count: int = Field(
        default=1, description="Number of sources confirming this story (dedup)."
    )
    unconfirmed: bool = Field(
        default=False, description="True for single-source, lower-trust items."
    )
    sent_mode: SentMode = SentMode.PENDING
    ts: float = Field(description="Event time, Unix epoch seconds (UTC).")
    dedup_key: str = Field(
        default="",
        description="Normalised original headline, for cross-cycle dedup. The "
        "summary is a paraphrase, so dedup must match against this, not summary.",
    )
    id: Optional[int] = Field(default=None, description="DB row id once persisted.")
