"""
AJ NASDAQ belief agent.

Given AJ's distilled belief model and a current market state, recommend a
position action *strictly within his framework* — what would AJ's rules say to
do right now? The agent does not form its own market view; it applies his.

    export ANTHROPIC_API_KEY=sk-ant-...

    # Run his base-case scenario from the memo:
    python trading_agent.py

    # Probe how the rules respond to a different world:
    python trading_agent.py --scenario yield_shock
    python trading_agent.py --scenario mid_recovery

    # JSON only:
    python trading_agent.py --scenario yield_shock --json

Pipeline: belief_model.json + MarketState ──► Claude ──► PositionRecommendation
"""

from __future__ import annotations

import argparse
import sys

import anthropic

from market import SCENARIOS, mock_state
from schemas import BeliefModel, MarketState, PositionRecommendation

MODEL = "claude-opus-4-8"

SYSTEM = """You are a disciplined execution agent for a single researcher's \
strategy. You are given (1) the researcher's BELIEF MODEL — his decision \
framework, distilled — and (2) the current MARKET STATE. Recommend the action \
HIS framework implies for right now.

Hard rules:
- Apply the author's logic, not your own market opinion. Every conclusion must \
trace to a specific rule, trigger, or condition in the belief model.
- Anchor on his base case (a trim that keeps a core), then move OFF it only as \
his triggers fire or his bullish conditions dominate:
    * No bearish triggers firing, bullish conditions intact  -> STAY_INVESTED or hold the base-case TRIM.
    * His base-case setup (advanced recovery, summer strength, on-hold regime)  -> TRIM to the core.
    * One clear bearish trigger firing (esp. the primary signal)  -> TRIM_FURTHER.
    * Several or severe triggers firing  -> REDUCE_AGGRESSIVELY.
    * The failure-mode setup (early/mid recovery, most of the advance ahead)  -> do NOT trim; STAY_INVESTED, because selling mid-recovery is the mistake he warns against.
- The 10Y Treasury yield is his primary signal — weight it most. State explicitly what it is saying.
- For every bearish trigger in the model, report status: 'firing', 'watch', or 'dormant', with the current reading.
- target_exposure_pct must be consistent with the action (STAY_INVESTED ~100; \
TRIM ~65-75; TRIM_FURTHER ~40-55; REDUCE_AGGRESSIVELY <=30).
- Keep the rationale to his reasoning. If the state is ambiguous, say so and \
default to his stated base case rather than improvising."""


def evaluate(belief: BeliefModel, state: MarketState) -> PositionRecommendation:
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
                    "BELIEF MODEL:\n"
                    + belief.model_dump_json(indent=2)
                    + "\n\nCURRENT MARKET STATE:\n"
                    + state.model_dump_json(indent=2)
                    + "\n\nRecommend the action AJ's framework implies right now."
                ),
            }
        ],
        output_format=PositionRecommendation,
    )
    rec = response.parsed_output
    if rec is None:
        raise RuntimeError(
            f"Model returned no parseable recommendation (stop_reason={response.stop_reason})."
        )
    return rec


def render(state: MarketState, rec: PositionRecommendation) -> str:
    bar = "=" * 64
    lines = [
        bar,
        f"  AJ NASDAQ AGENT  —  {state.label}",
        f"  as of {state.as_of_date}",
        bar,
        f"  ACTION            : {rec.action.value}",
        f"  Target exposure   : {rec.target_exposure_pct}%  of a full position",
        f"  Confidence        : {rec.confidence:.2f}",
        "",
        f"  Primary signal (10Y): {rec.primary_signal_reading}",
        "",
        "  Trigger status:",
    ]
    for t in rec.triggers:
        mark = {"firing": "[X]", "watch": "[~]", "dormant": "[ ]"}.get(t.status, "[?]")
        lines.append(f"    {mark} {t.trigger_id}: {t.reading}")
    lines += ["", "  Supportive conditions:"]
    lines += [f"    + {c}" for c in rec.supportive_conditions] or ["    (none)"]
    lines += [
        "",
        "  Rationale:",
        f"    {rec.rationale}",
        "",
        "  Watch:",
    ]
    lines += [f"    - {w}" for w in rec.watch_items] or ["    (none)"]
    lines += [bar, f"  {rec.disclaimer}", bar]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        default="june_2026_base",
        help="Mock market scenario: " + ", ".join(SCENARIOS),
    )
    parser.add_argument(
        "--belief", default="belief_model.json", help="Path to the belief model JSON."
    )
    parser.add_argument("--json", action="store_true", help="Print only the JSON recommendation.")
    args = parser.parse_args()

    try:
        belief = BeliefModel.model_validate_json(open(args.belief, encoding="utf-8").read())
    except FileNotFoundError:
        sys.exit(
            f"Belief model '{args.belief}' not found. Generate it with extract_belief.py "
            "or use the committed belief_model.json."
        )

    state = mock_state(args.scenario)
    rec = evaluate(belief, state)

    if args.json:
        print(rec.model_dump_json(indent=2))
    else:
        print(render(state, rec))
        print("\n--- JSON ---")
        print(rec.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
