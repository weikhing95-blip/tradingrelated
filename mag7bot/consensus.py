"""Consensus-estimate provider seam (GM-B3-02).

The unified macro render shows ``actual vs est · prior``. The *estimate*
(consensus) is the one field we cannot take from a government release — it comes
from an economic-calendar aggregator. ForexFactory's consensus is **not** cleared
for commercial redistribution (see docs/SOURCE_LICENSES.md, GM-B3-01), so no
consensus provider is wired by default.

This module defines the swap point: a ``ConsensusProvider`` protocol and a
``NullConsensusProvider`` that returns nothing. When a licensed calendar API
(e.g. TradingEconomics / FMP under a data-display agreement) is contracted, add
an implementation here — the macro sources and formatter do not change, because
they already read/render the ``consensus`` field and fall back to
``actual + prior`` when it is empty (GM-B3-03).
"""

from __future__ import annotations

from typing import Optional, Protocol


class ConsensusProvider(Protocol):
    def consensus_for(self, economy: str, indicator: str, period: str) -> Optional[str]:
        """Return a formatted consensus estimate, or None when unavailable/unlicensed."""
        ...


class NullConsensusProvider:
    """Default: no consensus (renders actual + prior only)."""

    def consensus_for(self, economy: str, indicator: str, period: str) -> Optional[str]:
        return None


def default_provider() -> ConsensusProvider:
    return NullConsensusProvider()
