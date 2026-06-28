"""Reference data for the watched companies (PRD §9 `companies`).

This is a *lookup* table, not a per-article store. Which company an item is
about arrives with the data (Finnhub per-ticker, EDGAR per-CIK); which *groups*
a company belongs to is a static join handled here. Populated once, rarely
changed.

CIKs are the SEC's central index keys, 10-digit zero-padded as the EDGAR
submissions API expects them.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str
    cik: str  # 10-digit zero-padded
    sector: str
    groups: List[str]
    aliases: List[str]  # names the company is referred to by, for relevance


# The Magnificent Seven. CIKs verified against SEC EDGAR.
MAG7: Dict[str, Company] = {
    "AAPL": Company("AAPL", "Apple Inc.", "0000320193", "Technology", ["mag7", "sp500", "tech"], ["apple", "aapl"]),
    "MSFT": Company("MSFT", "Microsoft Corporation", "0000789019", "Technology", ["mag7", "sp500", "tech"], ["microsoft", "msft"]),
    "GOOGL": Company("GOOGL", "Alphabet Inc.", "0001652044", "Communication Services", ["mag7", "sp500", "tech"], ["alphabet", "google", "googl", "goog"]),
    "AMZN": Company("AMZN", "Amazon.com, Inc.", "0001018724", "Consumer Discretionary", ["mag7", "sp500", "tech"], ["amazon", "amzn", "aws"]),
    "NVDA": Company("NVDA", "NVIDIA Corporation", "0001045810", "Technology", ["mag7", "sp500", "tech"], ["nvidia", "nvda"]),
    "META": Company("META", "Meta Platforms, Inc.", "0001326801", "Communication Services", ["mag7", "sp500", "tech"], ["meta", "facebook", "instagram", "whatsapp"]),
    "TSLA": Company("TSLA", "Tesla, Inc.", "0001318605", "Consumer Discretionary", ["mag7", "sp500", "auto"], ["tesla", "tsla"]),
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


def aliases_for(ticker: str) -> List[str]:
    """Lowercase names a company is referred to by. Falls back to the ticker
    (and the leading word of the name) for tickers not in the Mag7 table."""
    t = ticker.upper()
    if t in MAG7:
        return MAG7[t].aliases
    return [t.lower()]


# Alias for code that prefers a more general name (the table currently *is*
# the Mag 7, but other tickers can be added at runtime via the watchlist).
ALL_COMPANIES: Dict[str, Company] = MAG7


async def lookup_cik(
    ticker: str,
    user_agent: str = "MarketBrief contact@marketbrief.app",
    timeout: float = 10.0,
) -> Optional[str]:
    """Resolve a ticker to its SEC CIK via EDGAR's company-search endpoint.

    Returns a 10-digit zero-padded CIK string, or ``None`` when EDGAR doesn't
    recognise the ticker (or the lookup fails for any reason — best effort).
    """
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={ticker.upper()}&type=10-K"
        "&dateb=&owner=include&count=1&search_text=&output=atom"
    )
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=timeout
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        root = ET.fromstring(resp.text)
        # EDGAR's atom feed nests CIK inside <company-info><cik>…</cik>.
        for el in root.iter():
            tag = el.tag.split("}", 1)[-1]  # strip XML namespace
            if tag == "cik":
                raw = (el.text or "").strip()
                if raw.isdigit():
                    return raw.zfill(10)
    except Exception:
        return None
    return None
