"""
Distill a researcher's company write-up into a structured trading signal.

Prototype: paste in (or point at a file containing) the text of a research
post — the kind published on a Ghost newsletter — and Claude extracts a
normalized trading signal you could feed into downstream tooling.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...

    # From a text file:
    python distill_signal.py --file sample_writeup.txt

    # From stdin (paste, then Ctrl-D):
    python distill_signal.py

    # Pretty JSON only (for piping into another tool):
    python distill_signal.py --file sample_writeup.txt --json

This is a PROTOTYPE. The output captures *the author's opinion as structured
data* — it is not a validated alpha signal. Do not wire it into live order
flow without a backtest of the author's historical hit-rate and hard risk
controls. See README.md.
"""

from __future__ import annotations

import argparse
import sys
from enum import Enum
from typing import List

import anthropic
from pydantic import BaseModel, Field

MODEL = "claude-opus-4-8"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class CallType(str, Enum):
    NEW = "NEW"  # a fresh recommendation
    UPDATE = "UPDATE"  # an update to a thesis the author has covered before
    UNCLEAR = "UNCLEAR"


class TradingSignal(BaseModel):
    """A normalized, machine-readable summary of one company write-up."""

    ticker: str = Field(
        description="Stock ticker symbol. Use 'UNKNOWN' if the post never states one."
    )
    company_name: str = Field(description="Full company name as written.")
    direction: Direction = Field(
        description=(
            "The author's directional stance. LONG = bullish / buy, "
            "SHORT = bearish / sell-or-avoid, NEUTRAL = watching / no clear stance."
        )
    )
    conviction: float = Field(
        description=(
            "How strongly the author holds the view, from 0.0 (idle musing) to "
            "1.0 (table-pounding, highest-conviction position). Infer from the "
            "language: 'worth a look' is low; 'my single largest position' is high."
        )
    )
    time_horizon: str = Field(
        description="The author's stated or implied holding horizon, e.g. '3-6 months', '2-3 years', or 'unspecified'."
    )
    thesis_summary: str = Field(
        description="2-4 sentence plain-language summary of the core thesis."
    )
    catalysts: List[str] = Field(
        description="Specific events the author expects to move the stock (earnings, product launches, M&A, etc.). Empty list if none stated."
    )
    key_risks: List[str] = Field(
        description="Specific risks or bear-case points the author acknowledges. Empty list if none stated."
    )
    valuation_view: str = Field(
        description="The author's valuation claim in a phrase, e.g. 'trading at 8x FCF vs peers at 15x', or 'no valuation discussed'."
    )
    call_type: CallType = Field(
        description="Whether this reads as a brand-new call or an update to existing coverage."
    )
    rationale: str = Field(
        description=(
            "One sentence explaining how you mapped the prose to the direction and "
            "conviction above — quote the decisive phrase(s) so the score is auditable."
        )
    )


SYSTEM = """You are a buy-side analyst's assistant. You read one piece of \
investment research about a single company and distill it into a structured \
signal that captures the AUTHOR'S view — not your own.

Rules:
- Represent the author faithfully. Do not inject your own opinion, price \
targets, or market knowledge.
- Calibrate `conviction` to the author's language, not to how good the idea \
sounds. Hedged language ("might", "watching", "small position") is low; \
emphatic language ("table-pounding", "largest holding", "no-brainer") is high.
- If the post is an update with no fresh directional change, still fill the \
fields from the most recent stated view.
- When a field genuinely isn't in the text, say so ('unspecified', \
'UNKNOWN', or an empty list) rather than guessing."""


def distill(text: str) -> TradingSignal:
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    "Distill the following research write-up into a trading signal.\n\n"
                    "<writeup>\n" + text.strip() + "\n</writeup>"
                ),
            }
        ],
        output_format=TradingSignal,
    )
    signal = response.parsed_output
    if signal is None:
        raise RuntimeError(
            f"Model did not return a parseable signal (stop_reason={response.stop_reason})."
        )
    return signal


def _bullets(items: List[str]) -> List[str]:
    if not items:
        return ["    (none stated)"]
    return [f"    - {item}" for item in items]


def render_human(s: TradingSignal) -> str:
    bar = "=" * 60
    lines = [
        bar,
        f"  {s.company_name}  ({s.ticker})",
        bar,
        f"  Direction   : {s.direction.value}",
        f"  Conviction  : {s.conviction:.2f}  ({_conviction_label(s.conviction)})",
        f"  Horizon     : {s.time_horizon}",
        f"  Call type   : {s.call_type.value}",
        f"  Valuation   : {s.valuation_view}",
        "",
        "  Thesis:",
        f"    {s.thesis_summary}",
        "",
        "  Catalysts:",
        *_bullets(s.catalysts),
        "",
        "  Key risks:",
        *_bullets(s.key_risks),
        "",
        f"  Why this score: {s.rationale}",
        bar,
    ]
    return "\n".join(lines)


def _conviction_label(c: float) -> str:
    if c >= 0.75:
        return "high"
    if c >= 0.45:
        return "moderate"
    return "low"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file", "-f", help="Path to a text file containing the write-up. Omit to read stdin."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print only the JSON signal (for piping)."
    )
    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()
    else:
        if sys.stdin.isatty():
            print("Paste the write-up, then press Ctrl-D:\n", file=sys.stderr)
        text = sys.stdin.read()

    if not text.strip():
        sys.exit("No input text provided.")

    signal = distill(text)

    if args.json:
        print(signal.model_dump_json(indent=2))
    else:
        print(render_human(signal))
        print("\n--- JSON ---")
        print(signal.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
