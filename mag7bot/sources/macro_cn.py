"""China macro source (GM-B1-02): NBS prints (CPI/PPI/PMI/GDP) + PBoC LPR.

China's statistics office (NBS) and central bank (PBoC) publish on a fixed
monthly schedule; polling continuously is harmless (the ``seen`` gate dedups),
though release windows are predictable. LPR / policy decisions are tagged
``central_bank`` (CRITICAL); CPI/PPI/PMI/GDP prints are data (MATERIAL).
Commercial-safe (government public records). Toggle ``ENABLE_MACRO_CN``.

Note: NBS/PBoC feed availability is less stable than the other central banks —
the source is fully fail-soft, and its live feed URL should be confirmed during
operation. Parsing is fixture-tested offline.
"""

from __future__ import annotations

from .macro_common import MacroRssSource


class MacroChinaSource(MacroRssSource):
    name = "macro_cn"
    economy = "CN"
    publisher = "China NBS / PBoC"
    feeds = (
        "https://www.stats.gov.cn/english/rss/PressRelease.xml",
        "http://www.pbc.gov.cn/en/rss.xml",
    )
    cb_hints = (
        "loan prime rate", "lpr", "policy rate", "reserve requirement",
        "medium-term lending", "interest rate",
    )
    indicator_map = {
        "consumer price": "CPI", "cpi": "CPI",
        "producer price": "PPI", "ppi": "PPI",
        "purchasing managers": "PMI", "pmi": "PMI",
        "gross domestic": "GDP", "gdp": "GDP",
    }
