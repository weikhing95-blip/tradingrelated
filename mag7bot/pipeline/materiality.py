"""Rule-based materiality scoring (PRD F3, §10).

Deterministic and auditable — no LLM. Maps an event to one of three routes:

  CRITICAL  push now, *overrides* quiet hours   (halt, earnings, M&A)
  MATERIAL  push now, suppressed during quiet hours
  LOW       buffered into the daily digest

Form 4 (routine insider) is demoted to the digest even though it's an SEC
filing; the actual financial reports (8-K results, 10-Q/K) and corporate events
are what warrant an instant push.
"""

from __future__ import annotations

from ..schemas import EventType, Materiality, RawItem, Tier

# Default route per event type (before per-item overrides).
_BASE: dict[EventType, Materiality] = {
    EventType.EARNINGS: Materiality.CRITICAL,
    EventType.MA: Materiality.CRITICAL,
    EventType.SEC_FILING: Materiality.MATERIAL,
    EventType.LEGAL_REGULATORY: Materiality.MATERIAL,
    EventType.MANAGEMENT_CHANGE: Materiality.MATERIAL,
    EventType.INDEX_LISTING: Materiality.MATERIAL,
    EventType.ANALYST: Materiality.LOW,
    EventType.PRODUCT_LAUNCH: Materiality.LOW,
    EventType.NEWS: Materiality.LOW,
}

_HALT_HINTS = ("trading halt", "halted", "halts trading", "circuit breaker")


def score(item: RawItem, event_type: EventType) -> Materiality:
    # A trading halt is always critical, however it was classified.
    if any(hint in item.headline.lower() for hint in _HALT_HINTS):
        return Materiality.CRITICAL
    # Routine insider Form 4 → digest, not a push.
    if item.form_type and item.form_type.startswith("4"):
        return Materiality.LOW
    return _BASE.get(event_type, Materiality.LOW)


def is_unconfirmed(tier: Tier, confirmed_count: int) -> bool:
    """Single-source, non-primary items carry the ⚠️ unconfirmed label (§4)."""
    return confirmed_count < 2 and tier != Tier.PRIMARY
