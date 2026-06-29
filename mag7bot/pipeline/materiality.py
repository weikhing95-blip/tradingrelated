"""Rule-based materiality scoring (PRD F3, §10).

Deterministic and auditable — no LLM. Maps an event to one of three routes:

  CRITICAL  push now, *overrides* quiet hours   (halt, earnings, M&A)
  MATERIAL  push now, suppressed during quiet hours
  LOW       buffered into the daily digest

Form 4 (routine insider) is demoted to the digest even though it's an SEC
filing; the actual financial reports (8-K results, 10-Q/K) and corporate events
are what warrant an instant push.

Per-type overrides in ``score`` promote/demote specific items:
  * Trading halts → CRITICAL regardless of classification.
  * Form 4 insider filings → LOW (digest).
  * Mega product launches (iPhone, Blackwell, FSD, …) → CRITICAL.
  * Significant analyst actions (initiations, target moves with a value) →
    MATERIAL (base ANALYST stays LOW).
"""

from __future__ import annotations

from ..config import INSIDER_THRESHOLD_USD
from ..schemas import EventType, Materiality, RawItem, Tier

# Default route per event type (before per-item overrides).
_BASE: dict[EventType, Materiality] = {
    EventType.EARNINGS: Materiality.CRITICAL,
    EventType.MA: Materiality.CRITICAL,
    EventType.MACRO: Materiality.CRITICAL,  # CPI/PCE/claims — market-moving
    EventType.TRADING_HALT: Materiality.CRITICAL,  # exchange halt — always critical
    EventType.SEC_FILING: Materiality.MATERIAL,
    EventType.LEGAL_REGULATORY: Materiality.MATERIAL,
    EventType.MANAGEMENT_CHANGE: Materiality.MATERIAL,
    EventType.INDEX_LISTING: Materiality.MATERIAL,
    EventType.EXEC_COMMENTARY: Materiality.MATERIAL,
    EventType.PRODUCT_LAUNCH: Materiality.MATERIAL,  # mega-launches promoted in score()
    EventType.ANALYST: Materiality.LOW,  # significant actions promoted in score()
    EventType.NEWS: Materiality.LOW,
}

_HALT_HINTS = ("trading halt", "halted", "halts trading", "circuit breaker")

# Mega product launches that should override into CRITICAL — flagship hardware
# and AI model releases from the Mag 7 names.
_MEGA_LAUNCH_HINTS = (
    "iphone", "ipad", "macbook", "apple intelligence",  # Apple
    "blackwell", "hopper", "rtx", "gh200", "b200",      # NVIDIA GPU
    "gemini", "pixel",                                   # Google
    "gpt", "o3", "o4",                                   # AI models (MSFT/OpenAI)
    "autopilot", "full self-driving", "fsd", "cybertruck", "model ",  # Tesla
)

# Significant analyst actions — promote ANALYST back to MATERIAL.
_SIGNIFICANT_ANALYST_HINTS = (
    "initiates", "initiating", "initiation",           # new coverage = significant
    "double upgrade", "double downgrade",
    "raises target to $", "cuts target to $",          # target change with a value
    "price target increase", "price target cut",
    "strong buy", "strong sell",
)


def score(item: RawItem, event_type: EventType) -> Materiality:
    headline = item.headline.lower()

    # A trading halt is always critical, however it was classified.
    if any(hint in headline for hint in _HALT_HINTS):
        return Materiality.CRITICAL
    # Routine EDGAR Form 4 → digest (raw filing reference, not an actionable alert).
    if item.source == "edgar" and item.form_type and item.form_type.startswith("4"):
        return Materiality.LOW
    # Finnhub insider transactions: push only trades above the dollar threshold.
    if item.source == "insider":
        value = item.payload.get("value_usd", 0)
        return Materiality.MATERIAL if value >= INSIDER_THRESHOLD_USD else Materiality.LOW
    # Structured analyst feed: the source already pre-filters to genuine rating
    # *changes* (upgrades/downgrades/initiations), so they're material — unlike
    # headline-guessed analyst chatter, which stays LOW unless a hint promotes it.
    if item.source == "ratings":
        return Materiality.MATERIAL
    # Upcoming-earnings heads-up: material (worth a push), but not CRITICAL like
    # the actual print — it shouldn't override quiet hours.
    if item.source == "earnings" and item.payload.get("preview"):
        return Materiality.MATERIAL
    # Macro/Fed relayed from a monitored channel is commentary, not an official
    # release — material (push), but not CRITICAL like a real CPI/PCE print.
    if item.source == "telegram" and event_type == EventType.MACRO:
        return Materiality.MATERIAL

    # Mega product launches override into CRITICAL.
    if event_type == EventType.PRODUCT_LAUNCH:
        if any(hint in headline for hint in _MEGA_LAUNCH_HINTS):
            return Materiality.CRITICAL
        return Materiality.MATERIAL

    # Significant analyst actions get promoted to MATERIAL; otherwise LOW.
    if event_type == EventType.ANALYST:
        if any(hint in headline for hint in _SIGNIFICANT_ANALYST_HINTS):
            return Materiality.MATERIAL
        return Materiality.LOW

    return _BASE.get(event_type, Materiality.LOW)


def is_unconfirmed(tier: Tier, confirmed_count: int) -> bool:
    """Single-source, non-primary items carry the ⚠️ unconfirmed label (§4)."""
    return confirmed_count < 2 and tier != Tier.PRIMARY
