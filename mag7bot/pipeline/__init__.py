"""The processing pipeline: raw items → send-ready events.

Stages (PRD §8), each a pure, testable function/module:
    whitelist  → drop non-approved publishers (the §4 hard rule)
    classify   → assign an EventType
    dedup      → collapse the same story across sources (cross-confirmed)
    materiality→ rule-based push | digest, with critical override
    summarize  → verbatim headline (default) or LLM one-liner (flag)
    formatter  → render the §6 message format
"""

from __future__ import annotations
