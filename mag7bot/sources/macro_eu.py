"""Eurozone macro source (GM-B1-03): ECB rate decisions + Eurostat CPI/GDP.

The ECB publishes monetary-policy decisions and press releases via RSS; Eurostat
publishes flash HICP (inflation) and GDP. Rate decisions are tagged
``central_bank`` (CRITICAL); HICP/GDP prints are data (MATERIAL). Commercial-safe
(EU institutional public records). Toggle ``ENABLE_MACRO_EU``. Live feed shape
should be spot-checked during operation; parsing is fixture-tested offline.
"""

from __future__ import annotations

from .macro_common import MacroRssSource


class MacroEuroSource(MacroRssSource):
    name = "macro_eu"
    economy = "EU"
    publisher = "ECB / Eurostat"
    feeds = ("https://www.ecb.europa.eu/rss/press.html",)
    cb_hints = (
        "monetary policy", "interest rate", "policy decision", "key ecb interest",
        "rate decision",
    )
    indicator_map = {
        "inflation": "HICP", "hicp": "HICP", "consumer price": "HICP",
        "gross domestic": "GDP", "gdp": "GDP",
        "purchasing managers": "PMI", "pmi": "PMI",
    }
