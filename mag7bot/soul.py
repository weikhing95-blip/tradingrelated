"""The bot's evolving "soul" — a house-voice STYLE guide for summaries.

This is the durable, human-readable counterpart to the rotating few-shot
examples: distilled style *preferences* (not facts, not safety rules) injected
into every LLM summary prompt. It persists on the data volume (``soul.md`` next
to the DB) so it survives restarts, and it improves two ways:

  - the owner edits it (``/soul`` to view, ``/soul_reset`` to revert), and
  - a periodic LLM **review** distils accumulated feedback (good rewrites +
    muted topics) into an updated guide, auto-applied with the previous version
    kept as ``soul.prev.md`` and the owner notified — easy to revert.

Safety boundary: the soul carries STYLE ONLY. The hard faithfulness rules live
in the summarizer's immutable system prompt (``summarize._SYSTEM``), so a bad
soul edit can never weaken the no-fabrication / no-hype guard.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from .config import Config

MAX_SOUL_CHARS = 2000

DEFAULT_SOUL = """\
# House voice — Mag 7 News Bot

- Wire-style: neutral, factual, bite-size (1–2 sentences, ≤320 characters).
- Lead with the hard number when there is one (EPS beat/miss, %, $ value, date).
- Always name the specific company, people, and country involved — never a vague
  placeholder like "a major company" or "a key ally".
- Active voice. No hype, no opinion, no investment advice, no meta-commentary.
- Don't restate the ticker (it's already the first line) and don't add emojis.
"""


def soul_path(cfg: Config) -> Path:
    """The living soul lives on the data volume, beside the DB, so it persists."""
    return cfg.db_path.parent / "soul.md"


def load(cfg: Config) -> str:
    """Current house voice. Seeds the file with the default on first read."""
    p = soul_path(cfg)
    try:
        if p.exists():
            text = p.read_text(encoding="utf-8").strip()
            if text:
                return text
    except OSError:
        return DEFAULT_SOUL
    try:
        p.write_text(DEFAULT_SOUL, encoding="utf-8")
    except OSError:
        pass
    return DEFAULT_SOUL


def save(cfg: Config, text: str) -> bool:
    """Persist a new soul, keeping the previous version as soul.prev.md. Returns
    False on empty input or write failure."""
    text = (text or "").strip()
    if not text:
        return False
    text = text[:MAX_SOUL_CHARS]
    p = soul_path(cfg)
    try:
        if p.exists():
            (p.parent / "soul.prev.md").write_text(
                p.read_text(encoding="utf-8"), encoding="utf-8"
            )
        p.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


def reset(cfg: Config) -> str:
    """Revert to the built-in default house voice."""
    save(cfg, DEFAULT_SOUL)
    return DEFAULT_SOUL


# --------------------------------------------------------------------------- #
# LLM review — distil accumulated feedback into an updated house voice          #
# --------------------------------------------------------------------------- #


class _SoulUpdate(BaseModel):
    soul: str = Field(
        description="The improved house-voice style guide as short imperative "
        "markdown bullets, ≤1800 characters."
    )


_REVIEW_SYSTEM = (
    "You maintain a concise house-voice STYLE GUIDE for a financial-news bot. "
    "Given the current guide, the owner's recent hand-written summaries (the "
    "style to emulate), and the topics they muted (deprioritise these), produce "
    "an IMPROVED guide that captures the owner's style preferences as short "
    "imperative bullets. Requirements: keep it ≤1800 characters; merge and refine "
    "(do not just append or duplicate); non-contradictory. STYLE ONLY — never add "
    "rules about facts, sourcing, or safety, and never weaken neutrality or the "
    "no-hype / no-fabrication stance. Output the full updated guide."
)


def propose_update(
    client, current_soul: str, corrections: List[str], muted: List[str], model: str
) -> Optional[str]:
    """Ask the LLM for an improved soul. Returns None on empty/failed output."""
    good = "\n".join(f"- {c}" for c in corrections[:20]) or "(none yet)"
    mutes = ", ".join(muted[:30]) or "(none yet)"
    content = (
        f"CURRENT GUIDE:\n{current_soul}\n\n"
        f"OWNER'S RECENT GOOD SUMMARIES (emulate this style):\n{good}\n\n"
        f"TOPICS THE OWNER MUTED (deprioritise):\n{mutes}"
    )
    resp = client.messages.parse(
        model=model,
        max_tokens=2000,
        system=_REVIEW_SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=_SoulUpdate,
    )
    parsed = resp.parsed_output
    if parsed is None or not parsed.soul.strip():
        return None
    return parsed.soul.strip()[:MAX_SOUL_CHARS]
