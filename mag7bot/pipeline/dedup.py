"""Deduplication / cross-source collapse (PRD F4, §10).

The same story arriving from multiple sources within a 6h rolling window
(per ticker) collapses into one event tagged ``cross-confirmed (N)``. We
normalise the headline (lowercase, strip the ticker/company name and
punctuation) and compare with a difflib similarity ratio.

This module is pure: `ingest.py` supplies the already-stored recent events and
applies the resulting groupings/updates to the DB.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Optional

from .. import companies
from ..schemas import Event, RawItem

SIMILARITY_THRESHOLD = 0.85

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
# Filler words that shouldn't drive a match either way.
_STOP = {"a", "an", "the", "to", "in", "of", "for", "and", "on", "at", "is", "as"}


def normalize(headline: str, ticker: str) -> str:
    """Canonical form of a headline for comparison."""
    text = headline.lower()
    # Drop the ticker and company name tokens — they're constant per ticker.
    drop = {ticker.lower()}
    if ticker.upper() in companies.ALL_COMPANIES:
        name = companies.name_for(ticker).lower()
        drop.update(re.split(r"[^a-z0-9]+", name))
    text = _PUNCT.sub(" ", text)
    tokens = [
        t for t in _WS.sub(" ", text).split() if t and t not in drop and t not in _STOP
    ]
    return " ".join(tokens)


JACCARD_THRESHOLD = 0.6


def _jaccard(a: str, b: str) -> float:
    """Token-set overlap |A∩B| / |A∪B|. Order-independent, so it catches the
    same story reworded with a different word order across outlets — where
    SequenceMatcher (sequence-sensitive) falls below threshold."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def similar(a: str, b: str) -> bool:
    """Two normalised headlines describe the same story. Either a high
    sequence ratio OR high token-overlap qualifies — the two metrics fail on
    different rewordings, so OR-ing them catches more true duplicates."""
    if not a or not b:
        return False
    if SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD:
        return True
    return _jaccard(a, b) >= JACCARD_THRESHOLD


def collapse(items: List[RawItem]) -> List[List[RawItem]]:
    """Group a batch of same-ticker(ish) items into same-story clusters.

    Returns a list of groups; each group's first element is the *primary*
    (earliest published). Items are compared by normalised headline.
    """
    groups: List[List[RawItem]] = []
    norms: List[str] = []
    for item in sorted(items, key=lambda i: i.published_at):
        n = normalize(item.headline, item.ticker)
        placed = False
        for idx, gnorm in enumerate(norms):
            if groups[idx][0].ticker == item.ticker and similar(n, gnorm):
                groups[idx].append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
            norms.append(n)
    return groups


def semantic_find(client, headline: str, candidates: List[Event], model: str) -> Optional[Event]:
    """LLM fallback for paraphrased duplicates the lexical checks miss: does
    ``headline`` report the SAME underlying event as any recent candidate (same
    companies + same development, even if worded differently or from another
    outlet)? Returns the matching Event or None. One cheap call; the caller only
    invokes this when there's a plausible (same-company) candidate, so it rarely
    fires. Any error → None (fall through to posting)."""
    # Compare against the whole same-ticker cluster in the window (capped for
    # cost; a single ticker rarely has this many distinct live stories).
    cands = [ev for ev in candidates if ev.id is not None][:12]
    if not cands or client is None:
        return None
    from pydantic import BaseModel, Field

    class _Dup(BaseModel):
        duplicate_of: Optional[int] = Field(
            default=None,
            description="id of the prior item reporting the SAME event, or null.",
        )

    listing = "\n".join(f"[{ev.id}] {ev.summary}" for ev in cands)
    system = (
        "You detect duplicate market-news items. Two items are the SAME story when "
        "they report the same underlying event about the same companies — even if "
        "worded differently or from different outlets. A follow-up that adds "
        "materially NEW information is NOT a duplicate. Be conservative: only flag a "
        "clear same-event match."
    )
    content = (
        f"NEW item:\n{headline}\n\nPRIOR items:\n{listing}\n\n"
        "Which prior [id] reports the SAME event as NEW? Set duplicate_of to that "
        "id, or null if none match."
    )
    try:
        resp = client.messages.parse(
            model=model, max_tokens=300, system=system,
            messages=[{"role": "user", "content": content}], output_format=_Dup,
        )
    except Exception:
        return None
    parsed = resp.parsed_output
    match_id = parsed.duplicate_of if parsed else None
    if match_id is None:
        return None
    for ev in cands:
        if ev.id == match_id:
            return ev
    return None


def find_existing(headline: str, ticker: str, recent: List[Event]) -> Optional[Event]:
    """Find an already-stored event (within the window) the headline matches.

    Compares against each event's stored ``dedup_key`` (the normalised original
    headline). The ``summary`` is a paraphrase / article-body compression and no
    longer resembles the headline, so matching against it silently missed
    paraphrased duplicates — hence the dedicated key. Pre-migration rows have an
    empty key; for those we fall back to the (weaker) summary comparison so old
    events still dedup rather than throwing.
    """
    n = normalize(headline, ticker)
    for ev in recent:
        if ev.ticker != ticker:
            continue
        key = ev.dedup_key or normalize(ev.summary, ticker)
        if similar(n, key):
            return ev
    return None
