"""Canned payloads for offline dry-run and unit tests.

Two flavours:
  - raw source payloads (EDGAR submissions JSON, Finnhub article lists) for
    testing the `_parse` functions without a network;
  - `sample_raw_items()` — a ready-made batch of RawItems that exercises the
    full pipeline in `--dry-run` (cross-confirm, whitelist drop, materiality
    routing, an unconfirmed item).

Timestamps are fixed (not "now") so tests are deterministic; the dry-run path
re-stamps them to recent so quiet-hours logic behaves sensibly.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from ..schemas import RawItem, Tier

# --------------------------------------------------------------------------- #
# Raw source payloads (for testing source._parse)                              #
# --------------------------------------------------------------------------- #

EDGAR_AAPL_SUBMISSIONS: Dict[str, Any] = {
    "cik": "320193",
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-25-000077",
                "0000320193-25-000070",
                "0000320193-25-000065",
                "0000320193-25-000060",
            ],
            "form": ["8-K", "10-Q", "4", "SC 13G"],
            "filingDate": ["2025-05-01", "2025-05-01", "2025-04-28", "2025-04-20"],
            "acceptanceDateTime": [
                "2025-05-01T16:30:00.000Z",
                "2025-05-01T16:31:00.000Z",
                "2025-04-28T18:00:00.000Z",
                "2025-04-20T17:00:00.000Z",
            ],
            "primaryDocDescription": [
                "Results of Operations",
                "Quarterly Report",
                "Statement of changes in beneficial ownership",
                "Schedule 13G",
            ],
        }
    },
}

# Finnhub article list for NVDA. Includes: an M&A story (whitelisted), an
# analyst upgrade, a legal item, and one from a NON-whitelisted publisher that
# the whitelist node must drop.
FINNHUB_NVDA: List[Dict[str, Any]] = [
    {
        "id": 7001,
        "category": "company",
        "datetime": 1746100000,
        "headline": "NVIDIA to acquire AI startup Run:ai in $700M deal",
        "source": "Reuters",
        "summary": "NVIDIA announced it will acquire Run:ai.",
        "url": "https://www.reuters.com/technology/nvidia-acquire-runai",
    },
    {
        "id": 7002,
        "category": "company",
        "datetime": 1746101000,
        "headline": "Morgan Stanley upgrades NVIDIA to Overweight, raises target",
        "source": "CNBC",
        "summary": "Analyst action.",
        "url": "https://www.cnbc.com/nvidia-upgrade",
    },
    {
        "id": 7003,
        "category": "company",
        "datetime": 1746102000,
        "headline": "NVIDIA faces antitrust probe over GPU bundling practices",
        "source": "Bloomberg",
        "summary": "Regulators open a probe.",
        "url": "https://www.bloomberg.com/nvidia-antitrust",
    },
    {
        "id": 7004,
        "category": "rumor",
        "datetime": 1746103000,
        "headline": "Rumor: NVIDIA secret chip leak, says blog",
        "source": "RandomBlog",
        "summary": "Unverified.",
        "url": "https://randomblog.example/nvidia-leak",
    },
]


# --------------------------------------------------------------------------- #
# Ready-made RawItem batch (for --dry-run end-to-end)                          #
# --------------------------------------------------------------------------- #


def sample_raw_items(now: float | None = None) -> List[RawItem]:
    """A batch that exercises dedup, whitelist, materiality and labels.

    Re-stamps publication times relative to ``now`` so routing/quiet-hours
    behave like a live feed.
    """
    now = time.time() if now is None else now
    return [
        # Tier-1 EDGAR 8-K (earnings results) — critical push.
        RawItem(
            source="edgar",
            source_item_id="0000320193-25-000077",
            ticker="AAPL",
            tier=Tier.PRIMARY,
            headline="Apple Inc. files 8-K — Results of Operations",
            url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000077/0000320193-25-000077-index.htm",
            publisher="sec.gov",
            published_at=now - 120,
            form_type="8-K",
            payload={"form": "8-K", "primaryDocDescription": "Results of Operations"},
        ),
        # Same M&A story from two wires → should cross-confirm (N=2).
        RawItem(
            source="finnhub",
            source_item_id="7001",
            ticker="NVDA",
            tier=Tier.WIRE,
            headline="NVIDIA to acquire AI startup Run:ai in $700M deal",
            url="https://www.reuters.com/technology/nvidia-acquire-runai",
            publisher="Reuters",
            published_at=now - 300,
            payload={},
        ),
        RawItem(
            source="finnhub",
            source_item_id="7099",
            ticker="NVDA",
            tier=Tier.WIRE,
            headline="Nvidia to acquire AI startup Run:ai in a $700 million deal",
            url="https://www.bloomberg.com/nvidia-runai",
            publisher="Bloomberg",
            published_at=now - 280,
            payload={},
        ),
        # Analyst upgrade → digest (low materiality).
        RawItem(
            source="finnhub",
            source_item_id="7002",
            ticker="NVDA",
            tier=Tier.WIRE,
            headline="Morgan Stanley upgrades NVIDIA to Overweight, raises target",
            url="https://www.cnbc.com/nvidia-upgrade",
            publisher="CNBC",
            published_at=now - 600,
            payload={},
        ),
        # Single-source legal item from one wire → material push, unconfirmed label.
        RawItem(
            source="finnhub",
            source_item_id="7003",
            ticker="NVDA",
            tier=Tier.WIRE,
            headline="NVIDIA faces antitrust probe over GPU bundling practices",
            url="https://www.bloomberg.com/nvidia-antitrust",
            publisher="Bloomberg",
            published_at=now - 700,
            payload={},
        ),
        # Non-whitelisted publisher → dropped by the whitelist node.
        RawItem(
            source="finnhub",
            source_item_id="7004",
            ticker="NVDA",
            tier=Tier.WIRE,
            headline="Rumor: NVIDIA secret chip leak, says blog",
            url="https://randomblog.example/nvidia-leak",
            publisher="RandomBlog",
            published_at=now - 800,
            payload={},
        ),
    ]
