# Archived prototypes — AJ research → trading logic

> **Status: parked / not the product.** These are early standalone experiments,
> kept for reference. The actively-developed product in this repo is the
> **Mag 7 News Bot** (`mag7bot/`) — see the root `README.md`. Nothing in
> `mag7bot/` imports from this directory.
>
> These scripts import each other as top-level modules (`from schemas import …`,
> `from market import …`), so run them **from inside `prototypes/`**:
> ```bash
> cd prototypes
> python trading_agent.py
> ```

Two prototypes that turn AJ Investment Research write-ups into structured,
machine-readable trading logic with Claude:

1. **Company distiller** (`distill_signal.py`) — for single-company posts.
   Paste a write-up, get a normalized `TradingSignal` (ticker, direction,
   conviction, catalysts, risks).
2. **NASDAQ belief agent** (`trading_agent.py`) — for AJ's macro/regime memos
   like *"When To Trim NASDAQ In 2026."* Distills his **decision framework**
   into an auditable playbook, then an agent recommends an action against *his*
   rules given current market conditions.

The eventual goal was to feed this into automated trading — but read the
**caveats** at the bottom first; that step needs a lot more rigor than these
prototypes provide.

---

## 2. NASDAQ belief agent

AJ's NASDAQ memo isn't a single-stock call — it's a **belief system**: *trim
into summer strength, keep a core, and reduce further only on identifiable
macro triggers (above all the 10Y Treasury yield), never on the calendar
alone.* So instead of one signal, we distill his **decision framework** and
build an agent that applies it.

```
his memo (text)  ──(extract_belief)──►  belief_model.json   (the durable playbook)
market feeds     ──(market.py)───────►  MarketState         (today's conditions)
belief + state   ──(trading_agent)───►  PositionRecommendation
```

### Run it

```bash
cd prototypes
pip install -r ../requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

# His base-case scenario from the memo (should recommend: TRIM, keep a core):
python trading_agent.py

# Probe how his rules respond to a different world:
python trading_agent.py --scenario yield_shock     # his #1 trigger fires -> trim further
python trading_agent.py --scenario mid_recovery    # the 2020/2025 failure mode -> stay invested
```

The agent anchors on his base case and moves off it only as **his** triggers
fire — it does not form its own market view. Output includes the action, a
target exposure %, the firing/watch/dormant status of every trigger in his
playbook, and a rationale that traces back to his rules.

### What got distilled

`belief_model.json` is the committed, human-readable playbook. It captures:

| Part                  | From the memo                                                       |
| --------------------- | ------------------------------------------------------------------- |
| `core_stance`         | Trim into strength, keep a core, return on weakness                 |
| `decision_standard`   | His "risk-efficiency standard" (drawdown OR dead-money avoided)     |
| `primary_signal`      | **The 10Y Treasury yield** — his single best early warning          |
| `bearish_triggers`    | 10Y > 4.5–5%, US-China tariffs, hawkish Fed, AI crowding, seasonality |
| `bullish_conditions`  | Moderate gains, Fed on hold, intact/broadening recovery, no catalyst |
| `failure_modes`       | Selling mid-recovery (the 2020/2025 mistake); liquidating vs trimming |

Each trigger/condition carries a verbatim `source_quote` so the playbook is
auditable against the memo.

### Regenerating the playbook from a memo

`belief_model.json` was distilled from *"When To Trim NASDAQ In 2026."* To
regenerate it (or build one from a different memo):

```bash
# Extract the memo's text from the PDF (kept local — it's your paid content):
python -c "from pypdf import PdfReader; \
  print('\n'.join((p.extract_text() or '') for p in \
  PdfReader('When_To_Trim_NASDAQ_In_2026.pdf').pages))" > article.txt

python extract_belief.py --file article.txt --out belief_model.json
```

> The memo text itself is AJ's paid research — keep it local, don't commit or
> redistribute it. The derived `belief_model.json` is a transformed framework,
> not his prose.

### Going live

`market.py` ships mock scenarios so you can see the rules drive different
actions. `live_state()` is a stub showing exactly where to wire free feeds:
the **10Y** and Fed funds from FRED (`DGS10`, `DFEDTARU`), NASDAQ YTD from
yfinance (`^IXIC`), and a headline classifier for tariff/Fed tone. The
judgement calls (recovery stage, tension level, AI momentum) stay explicit so
the agent's reasoning is auditable.

---

## 1. Company distiller

Distills a qualitative write-up about a **single company** into a structured
`TradingSignal`.

```
research write-up (text)  ──►  Claude (structured extraction)  ──►  TradingSignal
```

The `TradingSignal` captures, per company: `ticker`, `company_name`,
`direction` (`LONG`/`SHORT`/`NEUTRAL`), `conviction` (`0.0`–`1.0`, calibrated to
the author's *language*), `time_horizon`, `thesis_summary`, `catalysts`,
`key_risks`, `valuation_view`, `call_type` (`NEW`/`UPDATE`/`UNCLEAR`), and a
one-sentence auditable `rationale`.

`conviction` deliberately scores *how strongly the author holds the view*, not
how good the idea is — "small starter position" is low, "single largest
position" is high.

### Run

```bash
cd prototypes
pip install -r ../requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

python distill_signal.py --file sample_writeup.txt   # distill the bundled example
python distill_signal.py                             # paste your own, then Ctrl-D
python distill_signal.py --file sample_writeup.txt --json   # JSON only
```

## Before you automate anything

The intended use was to **feed the signal into automated trading**. That's a
meaningful jump from a prototype. The signal here captures *one person's opinion
as structured data* — it is **not** a statistically validated alpha signal.
Before any of it touches live orders:

1. **Backtest the author.** Log every signal with a timestamp and track forward
   returns before sizing anything on them.
2. **Separate signal from execution.** Position sizing, risk limits, slippage,
   and kill-switches are their own problem.
3. **Expect extraction noise.** An LLM occasionally mis-reads conviction or a
   ticker; the `rationale` field exists so every score is auditable.
4. **Mind the legal line.** Trading on a paid newsletter for your own account is
   ordinary; republishing or reselling the signals is not.

## Files

| File                 | Purpose                                                       |
| -------------------- | ------------------------------------------------------------- |
| `trading_agent.py`   | NASDAQ belief agent — recommends an action against AJ's rules |
| `extract_belief.py`  | Distills an AJ memo → `belief_model.json`                     |
| `belief_model.json`  | The committed playbook distilled from the NASDAQ memo         |
| `market.py`          | Mock market scenarios + live-feed stub                        |
| `schemas.py`         | Shared Pydantic schemas for the belief agent                  |
| `distill_signal.py`  | Company distiller (single-stock posts)                        |
| `sample_writeup.txt` | An example fictional company write-up to test against         |
| `scrape_images.py`   | Standalone image-scraping helper (experimental)               |
