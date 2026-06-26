"""Source adapters — each turns an external feed into `RawItem`s.

P0 ships two:
  - edgar.py   : SEC EDGAR submissions (Tier 1, source of record)
  - finnhub.py : Finnhub /company-news (Tier 2/3)

P2 will add aggregator sources (Google News etc.) behind the F11 pre-filter.
"""

from __future__ import annotations

from .base import Source
from .edgar import EdgarSource
from .finnhub import FinnhubSource

__all__ = ["Source", "EdgarSource", "FinnhubSource"]
