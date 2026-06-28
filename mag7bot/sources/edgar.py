"""SEC EDGAR source — Tier 1, the source of record (PRD F1).

Polls the per-company submissions API
(``https://data.sec.gov/submissions/CIK##########.json``) and emits a RawItem
for each new filing of an interesting form type. SEC requires a descriptive
``User-Agent`` (a contact email) on every request — that comes from config.

Parsing helpers (`_parse`, `_index_url`) are pure so they can be unit-tested
against fixture payloads without any network access.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx

from .. import companies
from ..schemas import RawItem, Tier
from .base import SeenFn, Source

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# Forms we care about (PRD F1). Amendments (e.g. "8-K/A") match by prefix.
KEEP_FORMS = ("8-K", "10-Q", "10-K", "4", "S-1")


def _keep(form: str) -> bool:
    return any(form == f or form.startswith(f + "/") for f in KEEP_FORMS)


def _index_url(cik: str, accession: str) -> str:
    """Canonical filing-index URL for a given CIK + accession number."""
    cik_int = str(int(cik))  # strip leading zeros
    acc_nodash = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
        f"{acc_nodash}/{accession}-index.htm"
    )


def _to_epoch(acceptance: str, filing_date: str) -> float:
    """Prefer the precise acceptanceDateTime; fall back to the filing date."""
    if acceptance:
        try:
            dt = datetime.fromisoformat(acceptance.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    try:
        return datetime.strptime(filing_date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return 0.0


def _parse(ticker: str, cik: str, payload: Dict[str, Any]) -> List[RawItem]:
    """Turn a submissions payload into RawItems for the kept forms."""
    recent = payload.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    acceptances = recent.get("acceptanceDateTime", [])
    docs = recent.get("primaryDocDescription", [])

    items: List[RawItem] = []
    for i, accession in enumerate(accessions):
        form = forms[i] if i < len(forms) else ""
        if not _keep(form):
            continue
        filing_date = dates[i] if i < len(dates) else ""
        acceptance = acceptances[i] if i < len(acceptances) else ""
        desc = docs[i] if i < len(docs) else ""
        name = companies.name_for(ticker) if ticker.upper() in companies.ALL_COMPANIES else ticker
        headline = f"{name} files {form}" + (f" — {desc}" if desc else "")
        items.append(
            RawItem(
                source="edgar",
                source_item_id=accession,
                ticker=ticker.upper(),
                tier=Tier.PRIMARY,
                headline=headline,
                url=_index_url(cik, accession),
                publisher="sec.gov",
                published_at=_to_epoch(acceptance, filing_date),
                form_type=form,
                payload={
                    "accessionNumber": accession,
                    "form": form,
                    "filingDate": filing_date,
                    "acceptanceDateTime": acceptance,
                    "primaryDocDescription": desc,
                },
            )
        )
    return items


class EdgarSource(Source):
    name = "edgar"
    tier = Tier.PRIMARY

    def __init__(self, user_agent: str, timeout: float = 15.0) -> None:
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._timeout = timeout

    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        out: List[RawItem] = []
        async with httpx.AsyncClient(
            headers=self._headers, timeout=self._timeout
        ) as client:
            for ticker in tickers:
                if ticker.upper() not in companies.ALL_COMPANIES:
                    continue
                cik = companies.cik_for(ticker)
                url = SUBMISSIONS_URL.format(cik=cik)
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError):
                    # Network/parse hiccup — skip this ticker this cycle (§10:
                    # back off + retry next poll, never crash the loop).
                    continue
                for item in _parse(ticker, cik, payload):
                    if not is_seen(self.name, item.source_item_id):
                        out.append(item)
        return out
