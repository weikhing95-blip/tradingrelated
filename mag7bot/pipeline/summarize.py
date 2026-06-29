"""Bite-size summary generation.

Goal: deliver bite-size news — a substantive 1-2 sentence summary of *what
happened*, not just a headline (see the project goal in the README).

Two modes:
  - ``verbatim`` (default, free): use the source's own summary/description blurb
    (Finnhub ``summary`` / Yahoo ``description``) when it carries real content;
    otherwise fall back to the headline. No API, no hallucination risk.
  - ``llm`` (needs ANTHROPIC_API_KEY): Claude compresses the headline + blurb
    into a neutral 1-2 sentence summary, gated to push items for cost. A
    source-faithfulness guard rejects any output that introduces a number not
    present in the source text, falling back to the rich-verbatim text.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from ..article import is_boilerplate
from ..config import HAIKU_MODEL

MAX_LEN = 320  # bite-size: ~1-3 sentences

_WS = re.compile(r"\s+")
_NUM = re.compile(r"\d[\d,.]*")


def _clean(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def truncate(text: str, limit: int = MAX_LEN) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def rich_verbatim(headline: str, body: str = "") -> str:
    """Prefer the source's content blurb over the bare headline when it adds
    substance; otherwise use the headline. Always verbatim — no invention."""
    h = _clean(headline)
    b = _clean(body)
    if b and not is_boilerplate(b) and len(b) >= 40 and b.lower() != h.lower():
        return truncate(b)
    return truncate(h)


class _BiteSize(BaseModel):
    summary: str = Field(description="A neutral 1-2 sentence factual summary, ≤320 chars.")


_SYSTEM = (
    "You compress financial news into one or two neutral, factual sentences "
    "(≤320 characters) — bite-size, wire-style. Use ONLY facts present in the "
    "provided headline and text. Do NOT add numbers, company/person/product "
    "names, or claims that are not there, and do NOT strengthen a hedge into a "
    "certainty (e.g. 'may consider' must not become 'announces'). No opinion, "
    "no investment advice, no hype, no ticker symbols."
)

# Capitalised tokens that are generic (not entities), so their presence in a
# summary is never treated as a fabricated proper noun.
_GENERIC_CAPS = {
    "the", "a", "an", "it", "its", "this", "that", "these", "those", "and",
    "us", "uk", "eu", "ai", "ceo", "cfo", "coo", "cto", "ipo", "etf",
    "q1", "q2", "q3", "q4", "fed", "gdp", "cpi", "pce", "ppi", "fomc",
    "wall", "street", "inc", "corp", "co", "ltd", "plc", "group",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}
_PROPER = re.compile(r"\b[A-Z][A-Za-z][A-Za-z.&'-]+\b")
_WORD = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    """Reduce a word to a coarse stem so common inflections compare equal:
    possessives (Apple's→apple) and plurals (GPUs→gpu, companies→company). This
    is deliberately crude — it only needs to stop the faithfulness guard from
    flagging a benign plural/possessive of a name that IS in the source as a
    fabrication; it is not a linguistic stemmer."""
    w = word.lower().strip(".&'-")
    w = w.replace("'s", "").replace("’s", "")  # straight + curly possessive
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("es"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]
    return w


def _faithful(summary: str, source_text: str) -> bool:
    """Reject a summary that introduces facts absent from the source:
      - a number not present in the source, or
      - a proper noun (company / person / product name) not present in the
        source.
    Verb and wording rephrasings are allowed — only NEW entities and numbers
    fail, since those are the dangerous fabrications. Inflected forms of a name
    that IS in the source (plural/possessive, e.g. "GPUs" for "GPU", "Apple's"
    for "Apple") are accepted via stem comparison, so accurate rephrases aren't
    discarded. On a failure the caller falls back to the safe rich-verbatim text.
    """
    src = source_text.lower()
    src_nums = {n.replace(",", "") for n in _NUM.findall(source_text)}
    if not all(n.replace(",", "") in src_nums for n in _NUM.findall(summary)):
        return False
    src_stems = {_stem(w) for w in _WORD.findall(src)}
    stripped = summary.lstrip()
    for tok in _PROPER.findall(summary):
        # Sentence-initial capitalisation isn't evidence of an entity.
        if stripped.startswith(tok):
            continue
        low = tok.lower().strip(".&'-")
        if len(low) < 3 or low in _GENERIC_CAPS:
            continue
        # Present as a literal substring (conservative) OR as a matching stem
        # (so "GPUs"/"Apple's" match "GPU"/"Apple"). Otherwise it's invented.
        if low in src or _stem(low) in src_stems:
            continue
        return False
    return True


def _llm_bite_size(headline: str, body: str, client, model: str = HAIKU_MODEL) -> Optional[str]:
    content = f"Headline: {headline}\n\nArticle text: {body or '(none provided)'}"
    resp = client.messages.parse(
        model=model,
        max_tokens=2000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=_BiteSize,
    )
    parsed = resp.parsed_output
    if parsed is None or not parsed.summary.strip():
        return None
    candidate = parsed.summary.strip()
    if not _faithful(candidate, f"{headline} {body}"):
        return None
    return truncate(candidate)


def choose_summary(
    headline: str,
    body: str = "",
    mode: str = "verbatim",
    client=None,
    use_llm: bool = False,
    model: str = HAIKU_MODEL,
) -> str:
    """Return the bite-size summary for an item.

    ``use_llm`` lets the caller gate LLM compression to material/push items so
    digest items don't each cost an API call. ``model`` defaults to a cheap
    fast model (Haiku). Falls back to rich-verbatim on any error, missing
    client, or a faithfulness-guard failure.
    """
    # A boilerplate body (aggregator/consent text) carries no facts — drop it so
    # the LLM summarises the real headline instead of parroting the boilerplate.
    if is_boilerplate(body):
        body = ""
    rich = rich_verbatim(headline, body)
    if mode == "llm" and use_llm and client is not None:
        try:
            llm = _llm_bite_size(headline, body, client, model)
        except Exception:
            llm = None
        if llm:
            return llm
    return rich
