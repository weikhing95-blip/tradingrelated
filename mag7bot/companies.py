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

# Additional tracked companies beyond the Mag7.
# CIKs verified against SEC EDGAR submissions API.
EXTENDED: Dict[str, Company] = {
    "MU": Company("MU", "Micron Technology, Inc.", "0000723125", "Technology", ["semiconductor", "sp500", "tech"], ["micron", "micron technology", "mu"]),
    "PLTR": Company("PLTR", "Palantir Technologies Inc.", "0001321655", "Technology", ["ai", "software", "sp500", "tech"], ["palantir", "palantir technologies", "pltr"]),
}

# All companies tracked by the bot. Extend EXTENDED (not MAG7) to add more.
ALL_COMPANIES: Dict[str, Company] = {**MAG7, **EXTENDED}


def get(ticker: str) -> Company:
    """Look up a company by ticker, case-insensitively."""
    return ALL_COMPANIES[ticker.upper()]


def cik_for(ticker: str) -> str:
    return get(ticker).cik


def name_for(ticker: str) -> str:
    return get(ticker).name


def all_tickers() -> List[str]:
    return list(ALL_COMPANIES.keys())


def aliases_for(ticker: str) -> List[str]:
    """Lowercase names a company is referred to by. Falls back to the ticker
    for tickers not in the tracked table."""
    t = ticker.upper()
    if t in ALL_COMPANIES:
        return ALL_COMPANIES[t].aliases
    return [t.lower()]


def tickers_in(text: str, watchlist: List[str]) -> List[str]:
    """Every watchlist ticker the text mentions — by ``$CASHTAG`` or a whole-word
    company alias — in watchlist order. Used so a story about several watched
    names (e.g. "Apple and Google") tags all of them, not just the one it was
    fetched under."""
    import re

    if not text:
        return []
    low = text.lower()
    up = text.upper()
    found: List[str] = []
    for ticker in watchlist:
        t = ticker.upper()
        if re.search(r"\$" + re.escape(t) + r"\b", up):
            found.append(t)
            continue
        for alias in aliases_for(t):
            if re.search(r"\b" + re.escape(alias.lower()) + r"\b", low):
                found.append(t)
                break
    return found


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
