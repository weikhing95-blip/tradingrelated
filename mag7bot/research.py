"""Source-research agent (PRD robustness aid).

Given the watchlist and the current whitelist, asks Claude to propose additional
*reputable, verifiable* news publishers — each with a canonical domain, a
suggested trust tier, a one-line rationale and any caution. It only proposes;
the owner approves before anything enters the whitelist (the whitelist's hard
rule stays human-controlled). Reuses the repo's ``messages.parse`` pattern.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from .config import MODEL


class SourceSuggestion(BaseModel):
    name: str = Field(description="Publisher name, e.g. 'Barron's'.")
    domain: str = Field(description="Canonical bare domain, e.g. 'barrons.com'.")
    tier: int = Field(
        description="1 = primary/regulatory/exchange; 2 = major wire / established "
        "financial press; 3 = reputable aggregated / analyst feed."
    )
    rationale: str = Field(description="One line on why this source is reliable.")
    caution: str = Field(
        default="", description="Any caveat (paywall, aggregator, etc.); empty if none."
    )


class SourceSuggestions(BaseModel):
    suggestions: List[SourceSuggestion]


_SYSTEM = """You are a financial-news sourcing analyst. You curate a STRICT \
whitelist used to verify market-moving news about large-cap US equities.

Propose additional news publishers that are well-established and verifiable \
enough to trust for factual market news. Rules:
- Only reputable outlets: primary/regulatory/exchange sources, major wires, and \
established financial press. Tier-3 may include reputable analyst/aggregated \
feeds, but NOT general aggregators of unknown provenance.
- NEVER propose social media, personal blogs, forums, or content farms.
- EXCLUDE anything already in the provided current whitelist.
- Give the canonical bare domain (no scheme, no www), an honest tier, a one-line \
rationale, and a caution if there's any caveat (e.g. heavy paywall, aggregator).
- Prefer quality over quantity. It's fine to return fewer if few are warranted."""


def suggest_sources(
    client, watchlist: List[str], current_whitelist: List[str], limit: int = 8
):
    """Return a SourceSuggestions object. Raises on an unparseable response."""
    user = (
        f"Watchlist tickers: {', '.join(watchlist)}.\n\n"
        f"Current whitelist (do NOT repeat these):\n{', '.join(sorted(current_whitelist))}\n\n"
        f"Propose up to {limit} additional reputable publishers not already listed."
    )
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=8000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=SourceSuggestions,
    )
    parsed = resp.parsed_output
    if parsed is None:
        raise RuntimeError(
            f"Agent returned no parseable suggestions (stop_reason={resp.stop_reason})."
        )
    return parsed
