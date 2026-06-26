"""Reference data for the watched companies (PRD §9 `companies`).

This is a *lookup* table, not a per-article store. Which company an item is
about arrives with the data (Finnhub per-ticker, EDGAR per-CIK); which *groups*
a company belongs to is a static join handled here. Populated once, rarely
changed.

CIKs are the SEC's central index keys, 10-digit zero-padded as the EDGAR
submissions API expects them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str
    cik: str  # 10-digit zero-padded
    sector: str
    groups: List[str]


# The Magnificent Seven. CIKs verified against SEC EDGAR.
MAG7: Dict[str, Company] = {
    "AAPL": Company("AAPL", "Apple Inc.", "0000320193", "Technology", ["mag7", "sp500", "tech"]),
    "MSFT": Company("MSFT", "Microsoft Corporation", "0000789019", "Technology", ["mag7", "sp500", "tech"]),
    "GOOGL": Company("GOOGL", "Alphabet Inc.", "0001652044", "Communication Services", ["mag7", "sp500", "tech"]),
    "AMZN": Company("AMZN", "Amazon.com, Inc.", "0001018724", "Consumer Discretionary", ["mag7", "sp500", "tech"]),
    "NVDA": Company("NVDA", "NVIDIA Corporation", "0001045810", "Technology", ["mag7", "sp500", "tech"]),
    "META": Company("META", "Meta Platforms, Inc.", "0001326801", "Communication Services", ["mag7", "sp500", "tech"]),
    "TSLA": Company("TSLA", "Tesla, Inc.", "0001318605", "Consumer Discretionary", ["mag7", "sp500", "auto"]),
}


def get(ticker: str) -> Company:
    """Look up a company by ticker, case-insensitively."""
    return MAG7[ticker.upper()]


def cik_for(ticker: str) -> str:
    return get(ticker).cik


def name_for(ticker: str) -> str:
    return get(ticker).name


def all_tickers() -> List[str]:
    return list(MAG7.keys())
