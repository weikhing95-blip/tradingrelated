"""Nasdaq trading-halts source — free, no API key.

Polls the public Nasdaq Trader trading-halts RSS feed and emits a RawItem for
any halt/resumption whose symbol is on the watchlist. Trading halts are among
the most market-moving events a feed can carry, so these classify as
``TRADING_HALT`` → CRITICAL (override quiet hours).

Source name: "halts" — structured/trusted (bypasses whitelist + relevance).
Tier 1 (exchange-authoritative). The feed carries the whole market, so we
filter to watchlist symbols here.

Feed item fields are namespaced (``ndaq:IssueSymbol`` etc.); we match by local
tag name so a namespace-prefix change doesn't break parsing.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, List

import httpx

from ..schemas import RawItem, Tier
from .base import SeenFn, Source

HALTS_RSS = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"


def _local(tag: str) -> str:
    """Strip any XML namespace from a tag, e.g. '{ns}IssueSymbol' → 'issuesymbol'."""
    return tag.split("}", 1)[-1].strip().lower()


def _fields(item: ET.Element) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for child in item:
        out[_local(child.tag)] = (child.text or "").strip()
    return out


def _parse_halts(xml_text: str, tickers: List[str]) -> List[RawItem]:
    """Parse the trading-halts RSS into RawItems for watchlist symbols. Pure."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    watch = {t.upper() for t in tickers}
    items: List[RawItem] = []
    for el in root.iter("item"):
        f = _fields(el)
        symbol = (f.get("issuesymbol") or "").upper()
        if not symbol or symbol not in watch:
            continue
        reason = f.get("reasoncode") or ""
        halt_date = f.get("haltdate") or ""
        halt_time = f.get("halttime") or ""
        resume_date = f.get("resumptiondate") or ""
        resume_time = f.get("resumptiontradetime") or f.get("resumptionquotetime") or ""

        resumed = bool(resume_date or resume_time)
        verb = "resumes trading" if resumed else "trading halted"
        headline = f"{symbol} {verb}" + (f" ({reason})" if reason else "")
        if resumed and (resume_date or resume_time):
            headline += f" — resumption {resume_date} {resume_time}".rstrip()

        item_id = f"{symbol}-{halt_date}-{halt_time}-{reason}-{int(resumed)}"
        items.append(
            RawItem(
                source="halts",
                source_item_id=item_id,
                ticker=symbol,
                tier=Tier.PRIMARY,
                headline=headline,
                url="https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts",
                publisher="Nasdaq Trader",
                published_at=0.0,  # PRIMARY → exempt from the recency guard
                form_type=None,
                payload=f,
            )
        )
    return items


class TradingHaltsSource(Source):
    name = "halts"
    tier = Tier.PRIMARY

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout
        self._headers = {"User-Agent": "Mozilla/5.0 (compatible; mag7bot/1.0)"}

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            resp = await client.get(HALTS_RSS)
            resp.raise_for_status()
            items = _parse_halts(resp.text, tickers)
        return [it for it in items if not is_seen(self.name, it.source_item_id)]
