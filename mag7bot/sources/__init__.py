"""Source adapters — each turns an external feed into `RawItem`s.

P0 ships two:
  - edgar.py   : SEC EDGAR submissions (Tier 1, source of record)
  - finnhub.py : Finnhub /company-news (Tier 2/3)

P2 will add aggregator sources (Google News etc.) behind the F11 pre-filter.
"""

from __future__ import annotations

from .alpaca import AlpacaNewsSource
from .base import Source
from .earnings import EarningsSource
from .edgar import EdgarSource
from .fed import FedSource
from .finnhub import FinnhubSource
from .google_news import GoogleNewsSource
from .halts import TradingHaltsSource
from .insider import InsiderSource
from .macro import MacroSource
from .pricemove import PriceMoveSource
from .ratings import AnalystRatingsSource
from .yahoo_news import YahooNewsSource

__all__ = [
    "Source",
    "AlpacaNewsSource",
    "AnalystRatingsSource",
    "EarningsSource",
    "EdgarSource",
    "FedSource",
    "FinnhubSource",
    "GoogleNewsSource",
    "InsiderSource",
    "MacroSource",
    "PriceMoveSource",
    "TradingHaltsSource",
    "YahooNewsSource",
]
