"""Company-specific relevance + quality filter for news items.

Aggregator and per-ticker news feeds often surface articles that merely
*mention* a watched company (e.g. "Mesh came out of stealth … backed by
Google") or are opinion/listicle pieces ("I Correctly Predicted Alphabet …",
"3 AI stocks to buy"). Neither is company-specific news.

Two checks, applied to text news sources only (EDGAR / earnings / macro are
structured and exempt):

  - ``is_about`` — a company alias must appear in the **headline** (not just the
    body), so "mentioned but not about" items are dropped.
  - ``is_low_quality`` — drop opinion/listicle/promo headline patterns.
"""

from __future__ import annotations

import re

from .. import companies

# Sources whose items are free-text news and need the relevance/quality gate.
NEWS_SOURCES = {"finnhub", "yahoo_news", "google_news"}

_LOW_QUALITY = [
    r"^\s*i\b",                              # first-person opinion ("I correctly predicted…")
    r"\bwhy\s+i\b",
    r"\bmy\s+(top|favou?rite|best)\b",
    r"here'?s\s+(what|why|how)\b",
    r"\bshould\s+you\s+(buy|sell)\b",
    r"\bis\s+(it|now)\s+(the\s+)?time\s+to\b",
    r"\bstocks?\s+to\s+(buy|watch|consider|own|avoid)\b",
    r"\bbest\s+[\w\s]*\bstocks?\b",
    r"\b\d+\s+[\w\s]*\bstocks?\b",           # "3 AI stocks", "5 growth stocks"
    r"\bmillionaire\b",
    r"\bmotley\s+fool\b",
    r"\bprediction\b",
    r"\b(could|will)\s+make\s+you\b",
    # Auto-generated price-move filler (e.g. "MSFT Moves -5.7%: What You Should
    # Know", "Why NVDA Stock Is Up 3%", "Tesla Stock Falls 4%"). These reflect a
    # past move, not new information; the real substance (earnings, deals) comes
    # through the structured sources.
    r"\bmoves?\s+[+\-−]?\d",            # "Moves -5.7%", "Moves 5%"
    r"\b(stock|shares?|is|are)\s+(up|down)\s+[+\-−]?\d",
    r"\b(up|down|gains?|loses?|adds?|sheds?)\s+\d+(\.\d+)?\s*%",
    r"\b(falls?|rises?|drops?|jumps?|slips?|sinks?|tumbles?|surges?|climbs?|plunges?|soars?)\s+\d+(\.\d+)?\s*%",
    r"\bwhat\s+(you\s+should|to)\s+know\b",
]
_LOW_QUALITY_RE = [re.compile(p, re.IGNORECASE) for p in _LOW_QUALITY]


def is_about(headline: str, ticker: str) -> bool:
    """True if a company alias appears as a whole word in the headline."""
    h = headline.lower()
    for alias in companies.aliases_for(ticker):
        if re.search(r"\b" + re.escape(alias.lower()) + r"\b", h):
            return True
    return False


def is_low_quality(headline: str) -> bool:
    return any(rx.search(headline) for rx in _LOW_QUALITY_RE)


def is_company_specific(headline: str, ticker: str) -> bool:
    """Keep only headlines that are actually about the company and not opinion."""
    return is_about(headline, ticker) and not is_low_quality(headline)
