"""
Market-state providers for the AJ NASDAQ agent.

The agent decides against a `MarketState` — today's readings on the variables
AJ's framework cares about. This module supplies that state. For the prototype
the default is named mock scenarios (so you can see the belief rules drive
different actions); `live_state()` is a stub showing where to wire real feeds.
"""

from __future__ import annotations

from schemas import FedStance, MarketState, Momentum, Tension

# --------------------------------------------------------------------------- #
# Mock scenarios — each is a self-contained reading of the world.             #
# Tweak these, or add your own, to probe how the belief model responds.        #
# --------------------------------------------------------------------------- #

SCENARIOS = {
    # The conditions described in the 17 Jun 2026 memo. Should reproduce his
    # base case: a disciplined trim that keeps a core.
    "june_2026_base": MarketState(
        as_of_date="2026-06-17",
        label="Mid-June 2026 (the memo's setup)",
        ndx_ytd_pct=14.0,
        recovery_stage="well advanced — spring drawdown cleanly retraced, modest upward momentum",
        ust10y_pct=4.3,
        fed_funds_upper_pct=4.0,
        fed_stance=FedStance.ON_HOLD,
        rate_cut_priced=True,
        us_china_tension=Tension.ELEVATED,
        ai_leadership=Momentum.INTACT,
        participation="broadening",
        notes="Policy-on-hold regime; gains moderate and less extended than 2019/2023/2024 pre-peak.",
    ),
    # His #1 trigger fires: the 10Y pushes into the 4.5-5.0% danger zone.
    "yield_shock": MarketState(
        as_of_date="2026-08-05",
        label="Yield shock — 10Y breaks toward 5%",
        ndx_ytd_pct=18.0,
        recovery_stage="well advanced, now in the late-July–September peak window",
        ust10y_pct=4.8,
        fed_funds_upper_pct=4.0,
        fed_stance=FedStance.ON_HOLD,
        rate_cut_priced=True,
        us_china_tension=Tension.ELEVATED,
        ai_leadership=Momentum.CROWDED,
        participation="narrowing",
        notes="The proximate trigger that ended the 2018 and 2023 advances, per the memo.",
    ),
    # The failure-mode setup: early/mid recovery with most of the advance still
    # ahead. His framework says do NOT sell into this (the 2020/2025 mistake).
    "mid_recovery": MarketState(
        as_of_date="2026-07-10",
        label="Early/mid recovery (2020/2025 analog)",
        ndx_ytd_pct=8.0,
        recovery_stage="early/mid recovery — most of the advance still ahead",
        ust10y_pct=4.2,
        fed_funds_upper_pct=4.0,
        fed_stance=FedStance.ON_HOLD,
        rate_cut_priced=True,
        us_china_tension=Tension.CALM,
        ai_leadership=Momentum.INTACT,
        participation="broadening",
        notes="Shape resembles the two counterexamples where a July exit was premature.",
    ),
}


def mock_state(scenario: str = "june_2026_base") -> MarketState:
    if scenario not in SCENARIOS:
        raise KeyError(
            f"Unknown scenario '{scenario}'. Options: {', '.join(SCENARIOS)}"
        )
    return SCENARIOS[scenario]


def live_state() -> MarketState:
    """Wire real feeds here. Suggested, all free:

        - ust10y_pct        : FRED series DGS10        (https://fred.stlouisfed.org)
        - fed_funds_upper_pct: FRED series DFEDTARU
        - ndx_ytd_pct       : yfinance ticker ^IXIC (close / first-trading-day close - 1)
        - rate_cut_priced   : CME FedWatch, or infer from fed funds futures
        - us_china_tension  : a news/LLM classifier over recent headlines
        - ai_leadership     : breadth/momentum of MAG7 vs equal-weight NASDAQ
        - participation     : advance/decline or %-above-200dma breadth

    Keep the *interpretation* (recovery_stage, tension level, momentum) explicit
    and auditable — those judgement calls are what the agent reasons over.
    """
    raise NotImplementedError(
        "live_state() is a stub. Wire FRED (DGS10/DFEDTARU) and yfinance (^IXIC) "
        "per the docstring, or pass --scenario to use a mock state."
    )
