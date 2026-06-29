"""Source-research agent.

Two modes:

  suggest_sources(client, watchlist, current_whitelist)
      Manual mode (triggered by /suggest_sources): asks Claude to propose
      additional publishers. Owner reviews and approves each one. No changes
      are made automatically.

  run_autonomous(anthropic_api_key, cfg)
      Autonomous mode (weekly scheduled job): Claude uses tool-use to fetch and
      test candidate RSS/API endpoints, auto-adds those that pass quality checks,
      and sends a DM report to the owner. Requires ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Tuple

import httpx
from pydantic import BaseModel, Field

from . import db, ingest
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


# --------------------------------------------------------------------------- #
# Autonomous research agent                                                     #
# --------------------------------------------------------------------------- #

_AUTO_SYSTEM = """You are a market-news source researcher for a Telegram alerting bot.

Your job: find additional high-quality financial news sources (RSS feeds, public APIs)
that the bot can monitor for the companies in the watchlist. Be rigorous — only add
sources you can verify are (a) accessible and (b) contain real financial/company news.

Process:
1. Propose a candidate source (RSS feed URL or public API endpoint).
2. Call fetch_url to verify it exists and inspect its content.
3. If the content has quality financial news relevant to the watchlist, call add_source.
4. If it fails or is low-quality, skip it (record as rejected).
5. Repeat for 6-10 candidates, then stop.

Rules:
- NEVER add social media, blogs, forums, or paywalled-only sources.
- Prefer established financial press: wire services, regulatory bodies, exchange news feeds.
- Only add sources not already in the current whitelist.
- tier2 = major wire / established financial press. tier3 = reputable analyst/aggregator feed.
"""

_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "fetch_url",
        "description": (
            "Fetch the content of a URL (RSS feed or API endpoint) to verify it is "
            "accessible and inspect its content. Returns the HTTP status and first 1500 "
            "characters of the response body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The URL to fetch."}},
            "required": ["url"],
        },
    },
    {
        "name": "add_source",
        "description": (
            "Add a verified financial news domain to the whitelist. Only call this after "
            "fetch_url has confirmed the source is accessible and contains quality content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Bare domain, e.g. 'benzinga.com'."},
                "name": {"type": "string", "description": "Human-readable name, e.g. 'Benzinga'."},
                "tier": {
                    "type": "string",
                    "enum": ["tier2", "tier3"],
                    "description": "tier2 = established press, tier3 = aggregator/analyst.",
                },
            },
            "required": ["domain", "name", "tier"],
        },
    },
]


async def _fetch_url(url: str) -> str:
    """Execute the fetch_url tool — returns a short summary of the response."""
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "mag7bot/1.0 research-agent"})
            snippet = resp.text[:1500]
            return f"HTTP {resp.status_code}\n{snippet}"
    except Exception as exc:
        return f"ERROR: {exc}"


async def run_autonomous(anthropic_api_key: str, cfg) -> str:
    """Run one autonomous research cycle.

    Fetches and evaluates candidate news sources using Claude tool-use.
    Auto-adds verified sources to the whitelist. Returns a report string
    suitable for sending as an owner DM.
    """
    import anthropic

    async_client = anthropic.AsyncAnthropic(api_key=anthropic_api_key)

    current_whitelist = ingest.effective_whitelist(cfg)
    watchlist = db.watchlist_tickers(cfg.db_path, cfg.feed_id)

    messages: List[Dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Watchlist tickers: {', '.join(watchlist)}\n\n"
                f"Current whitelist (do NOT add these — already monitored):\n"
                f"{', '.join(sorted(current_whitelist[:40]))}\n\n"
                "Please research and add 4-6 new quality sources. Start now."
            ),
        }
    ]

    added: List[Tuple[str, str]] = []   # (name, domain)
    rejected: List[str] = []

    for _turn in range(12):  # safety cap on turns
        response = await async_client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=_AUTO_SYSTEM,
            tools=_TOOLS,
            messages=messages,
        )

        # Collect text for the conversation history.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            if block.name == "fetch_url":
                result = await _fetch_url(block.input["url"])
                db.log_research(cfg.db_path, "fetch_url", block.input["url"], result[:120])

            elif block.name == "add_source":
                domain = block.input["domain"].strip().lower()
                name = block.input["name"].strip()
                tier = block.input["tier"]
                if domain in {d.lower() for d in current_whitelist}:
                    result = f"SKIPPED — {domain} already whitelisted."
                    rejected.append(domain)
                else:
                    db.add_whitelist_domain(cfg.db_path, domain, name, tier)
                    db.log_research(cfg.db_path, "added_source", domain, f"{name} / {tier}")
                    added.append((name, domain))
                    # Refresh so subsequent checks see the new entry.
                    current_whitelist = ingest.effective_whitelist(cfg)
                    result = f"✅ Added {domain} ({tier}) to whitelist."

            else:
                result = "Unknown tool."

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

    # Build report.
    lines = [f"🤖 Research agent ran at {time.strftime('%d %b %Y %H:%M SGT', time.localtime())}"]
    if added:
        lines.append(f"\n✅ Added {len(added)} new source(s):")
        for name, domain in added:
            lines.append(f"  • {name} ({domain})")
    else:
        lines.append("\nNo new sources added this cycle.")

    if rejected:
        lines.append(f"\n⏭ Skipped (already whitelisted): {', '.join(rejected)}")

    lines.append("\nUse /sources to review. /remove_source <domain> to undo any addition.")
    return "\n".join(lines)
