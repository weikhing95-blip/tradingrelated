"""Japan macro source (GM-B1-01): Bank of Japan policy statements + CPI/GDP.

BoJ publishes decisions and releases via its "What's New" RSS feed. Central-bank
rate decisions are tagged ``central_bank`` (CRITICAL); CPI/GDP prints are data
(MATERIAL). Commercial-safe (government/central-bank public records). Toggle with
``ENABLE_MACRO_JP``. Live feed shape should be spot-checked during operation;
parsing is fixture-tested offline.
"""

from __future__ import annotations

from .macro_common import MacroRssSource


class MacroJapanSource(MacroRssSource):
    name = "macro_jp"
    economy = "JP"
    publisher = "Bank of Japan"
    feeds = ("https://www.boj.or.jp/en/rss/whatsnew.xml",)
    cb_hints = (
        "monetary policy", "policy rate", "statement on monetary policy",
        "policy decision", "interest rate",
    )
    indicator_map = {
        "consumer price": "CPI", "cpi": "CPI", "inflation": "CPI",
        "gross domestic": "GDP", "gdp": "GDP", "tankan": "Tankan",
    }
