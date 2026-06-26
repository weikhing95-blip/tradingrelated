"""One-line summary generation.

Default (``verbatim``): use the source's own headline, truncated to ≤200 chars.
Zero cost, zero hallucination risk — the safest choice for the verified-link
promise (PRD §12 Q2).

Optional (``llm``): ask Claude for a cleaner ≤200-char one-liner, behind a
config flag. A source-faithfulness guard rejects any output that introduces a
number absent from the headline, falling back to verbatim. This reuses the
repo's ``client.messages.parse(..., output_format=...)`` pattern.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from ..config import MODEL

MAX_LEN = 200


def truncate(text: str, limit: int = MAX_LEN) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class _OneLiner(BaseModel):
    summary: str = Field(description="A neutral one-line summary, ≤200 characters.")


_SYSTEM = (
    "You compress a news headline into one neutral ≤200-char line. "
    "Use ONLY facts stated in the headline. Do not add numbers, names, or "
    "claims that are not already there. No opinion, no speculation."
)

_NUM = re.compile(r"\d[\d,.]*")


def _faithful(summary: str, headline: str) -> bool:
    """Reject summaries that introduce a number not present in the headline."""
    head_nums = {n.replace(",", "") for n in _NUM.findall(headline)}
    for n in _NUM.findall(summary):
        if n.replace(",", "") not in head_nums:
            return False
    return True


def _llm_oneliner(headline: str, client) -> str:
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": f"Headline: {headline}"}],
        output_format=_OneLiner,
    )
    parsed = resp.parsed_output
    if parsed is None:
        raise RuntimeError("LLM returned no parseable summary")
    return parsed.summary


def summarize(headline: str, mode: str = "verbatim", client=None) -> str:
    """Return a ≤200-char summary for an item.

    ``mode='llm'`` requires an Anthropic ``client``; on any error or a
    faithfulness-guard failure it falls back to the verbatim headline.
    """
    verbatim = truncate(headline)
    if mode != "llm" or client is None:
        return verbatim
    try:
        candidate = _llm_oneliner(headline, client)
    except Exception:
        return verbatim
    if not candidate.strip() or not _faithful(candidate, headline):
        return verbatim
    return truncate(candidate)
