"""
Shared schemas for the AJ belief-driven NASDAQ agent.

Three objects flow through the system:

    article text ──(extract_belief)──► BeliefModel        (the durable playbook)
    market feeds ──(market.py)───────► MarketState        (today's conditions)
    BeliefModel + MarketState ──(agent)──► PositionRecommendation
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 1. The belief model — a structured, auditable distillation of the article.   #
# --------------------------------------------------------------------------- #


class Trigger(BaseModel):
    """A condition that, per the author, argues for REDUCING risk."""

    id: str = Field(description="Short stable identifier, e.g. 'yield_shock'.")
    name: str = Field(description="Human-readable name.")
    indicator: str = Field(
        description="The observable thing to watch, e.g. '10Y Treasury yield'."
    )
    threshold: str = Field(
        description="The level/condition that fires this trigger, e.g. 'above 4.5-5.0%'. "
        "Use 'qualitative' if there is no numeric line."
    )
    rationale: str = Field(description="Why the author treats this as a risk-off signal.")
    source_quote: str = Field(description="A short verbatim phrase from the article.")


class Condition(BaseModel):
    """A condition that, per the author, argues for STAYING invested."""

    id: str
    name: str
    indicator: str
    condition: str = Field(description="The state that supports staying invested.")
    rationale: str
    source_quote: str


class BeliefModel(BaseModel):
    """The author's decision framework, distilled into machine-readable form."""

    author: str
    asset: str = Field(description="The instrument the thesis is about.")
    as_of: str = Field(description="Date the thesis was written, as stated in the article.")

    core_stance: str = Field(
        description="One-sentence summary of the recommended posture."
    )
    base_case_action: str = Field(
        description="The single action the data supports today, in the author's words."
    )
    base_case_target_exposure_pct: int = Field(
        description="Implied target exposure as a percent of a full position (0-100). "
        "A 'trim that keeps a core' is roughly 60-75."
    )

    decision_standard: str = Field(
        description="The test the author uses to judge whether reducing risk was correct "
        "(here: the 'risk efficiency standard' — drawdown avoided OR dead money avoided)."
    )
    primary_signal: str = Field(
        description="The single most informative early-warning indicator, per the author."
    )

    bearish_triggers: List[Trigger] = Field(
        description="Conditions that argue for trimming further."
    )
    bullish_conditions: List[Condition] = Field(
        description="Conditions that argue for staying invested."
    )

    seasonality_view: str = Field(
        description="How the author treats the calendar/seasonality."
    )
    position_philosophy: List[str] = Field(
        description="Core rules about HOW to act (e.g. trim-and-keep-core, never a permanent exit)."
    )
    failure_modes: List[str] = Field(
        description="Mistakes the author explicitly warns against."
    )
    caveats: str = Field(description="The author's stated limitations on the analysis.")


# --------------------------------------------------------------------------- #
# 2. The market state — today's readings on the indicators the author cares    #
#    about. Populated by market.py (mock scenarios or live feeds).             #
# --------------------------------------------------------------------------- #


class FedStance(str, Enum):
    EASING = "easing"
    ON_HOLD = "on_hold"
    HIKING = "hiking"


class Tension(str, Enum):
    CALM = "calm"
    ELEVATED = "elevated"
    ESCALATING = "escalating"


class Momentum(str, Enum):
    INTACT = "intact"
    CROWDED = "crowded"
    EXHAUSTED = "exhausted"


class MarketState(BaseModel):
    """Current readings on the variables in the belief model."""

    as_of_date: str
    label: str = Field(description="Human label for this state/scenario.")

    ndx_ytd_pct: float = Field(description="NASDAQ Composite year-to-date return, percent.")
    recovery_stage: str = Field(
        description="Where the index sits in its recovery, e.g. 'well advanced' vs 'early/mid recovery'. "
        "This is the author's single most important shape question."
    )

    ust10y_pct: float = Field(description="10-Year US Treasury yield, percent.")
    fed_funds_upper_pct: float = Field(description="Federal Funds upper-bound target, percent.")
    fed_stance: FedStance
    rate_cut_priced: bool = Field(description="Is a near-term cut still partially priced in?")

    us_china_tension: Tension = Field(description="Tariff / rare-earth tension level.")
    ai_leadership: Momentum = Field(description="State of mega-cap AI leadership.")
    participation: str = Field(description="'broadening' or 'narrowing' breadth.")

    notes: str = Field(default="", description="Anything else relevant.")


# --------------------------------------------------------------------------- #
# 3. The agent's output — an action recommendation grounded in the belief.     #
# --------------------------------------------------------------------------- #


class Action(str, Enum):
    STAY_INVESTED = "STAY_INVESTED"  # keep full intended exposure
    TRIM = "TRIM"  # the base case: reduce to a core
    TRIM_FURTHER = "TRIM_FURTHER"  # triggers firing — reduce more
    REDUCE_AGGRESSIVELY = "REDUCE_AGGRESSIVELY"  # multiple/severe triggers
    RE_ENTER = "RE_ENTER"  # add back toward full after weakness


class FiringTrigger(BaseModel):
    trigger_id: str = Field(description="Matches a Trigger.id from the belief model.")
    status: str = Field(description="'firing', 'watch', or 'dormant'.")
    reading: str = Field(description="The current reading that justifies the status.")


class PositionRecommendation(BaseModel):
    """What the agent recommends, strictly per the author's framework."""

    action: Action
    target_exposure_pct: int = Field(
        description="Recommended NASDAQ exposure as percent of a full position (0-100)."
    )
    confidence: float = Field(description="0.0-1.0 confidence in the read of his framework.")

    primary_signal_reading: str = Field(
        description="What the author's primary signal (the 10Y yield) is saying right now."
    )
    triggers: List[FiringTrigger] = Field(
        description="Status of each bearish trigger from the belief model."
    )
    supportive_conditions: List[str] = Field(
        description="Bullish conditions currently satisfied."
    )

    rationale: str = Field(
        description="2-4 sentences justifying the action by explicit reference to the "
        "author's rules — not the agent's own market view."
    )
    watch_items: List[str] = Field(
        description="What to monitor that would change the recommendation."
    )
    disclaimer: str = Field(
        default="Decision support based on one researcher's framework. Not investment advice.",
        description="Standing disclaimer.",
    )
