"""UK macro source (GM-B1-04): Bank of England MPC decisions + ONS CPI/GDP.

The BoE publishes MPC decisions and news via RSS; the ONS publishes CPI
(inflation) and GDP. Bank Rate / MPC decisions are tagged ``central_bank``
(CRITICAL); CPI/GDP prints are data (MATERIAL). Commercial-safe (UK institutional
public records). Toggle ``ENABLE_MACRO_UK``. Live feed shape should be
spot-checked during operation; parsing is fixture-tested offline.
"""

from __future__ import annotations

from .macro_common import MacroRssSource


class MacroUKSource(MacroRssSource):
    name = "macro_uk"
    economy = "GB"
    publisher = "Bank of England / ONS"
    feeds = ("https://www.bankofengland.co.uk/rss/news",)
    cb_hints = (
        "bank rate", "monetary policy", "mpc", "interest rate", "rate decision",
    )
    indicator_map = {
        "inflation": "CPI", "cpi": "CPI", "consumer price": "CPI",
        "gross domestic": "GDP", "gdp": "GDP",
    }
