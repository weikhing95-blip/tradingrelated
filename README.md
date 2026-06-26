# Distilling AJ research into trading logic (prototype)

Two prototypes that turn AJ Investment Research write-ups into structured,
machine-readable trading logic with Claude:

1. **Company distiller** (`distill_signal.py`) — for single-company posts.
   Paste a write-up, get a normalized `TradingSignal` (ticker, direction,
   conviction, catalysts, risks). See [below](#1-company-distiller).
2. **NASDAQ belief agent** (`trading_agent.py`) — for AJ's macro/regime memos
   like *"When To Trim NASDAQ In 2026."* Distills his **decision framework**
   into an auditable playbook, then an agent recommends an action against *his*
   rules given current market conditions. See [below](#2-nasdaq-belief-agent).

The eventual goal is to feed this into automated trading — but read the
[caveats](#before-you-automate-anything) first; that step needs a lot more
rigor than these prototypes provide.

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
pip install -r requirements.txt
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

## What it does

```
research write-up (text)  ──►  Claude (structured extraction)  ──►  TradingSignal
```

The `TradingSignal` captures, per company:

| Field            | Meaning                                                        |
| ---------------- | -------------------------------------------------------------- |
| `ticker`         | Stock symbol (`UNKNOWN` if the post never states one)          |
| `company_name`   | Full company name                                              |
| `direction`      | `LONG` / `SHORT` / `NEUTRAL`                                   |
| `conviction`     | `0.0`–`1.0`, calibrated to the author's *language*             |
| `time_horizon`   | Stated/implied holding period                                  |
| `thesis_summary` | 2–4 sentence core thesis                                       |
| `catalysts`      | Specific events the author expects to move the stock           |
| `key_risks`      | Bear-case points the author acknowledges                       |
| `valuation_view` | The author's valuation claim, in a phrase                      |
| `call_type`      | `NEW` / `UPDATE` / `UNCLEAR`                                   |
| `rationale`      | One auditable sentence: which phrases drove the score          |

`conviction` deliberately scores *how strongly the author holds the view*, not
how good the idea is — "small starter position" is low, "single largest
position" is high.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
# Distill the bundled example
python distill_signal.py --file sample_writeup.txt

# Distill your own post (paste, then Ctrl-D)
python distill_signal.py

# JSON only, for piping into another tool
python distill_signal.py --file sample_writeup.txt --json
```

Expected shape of the JSON for the bundled example (values may vary slightly
between runs):

```json
{
  "ticker": "NGL",
  "company_name": "Northgate Logistics",
  "direction": "LONG",
  "conviction": 0.9,
  "time_horizon": "2 years",
  "call_type": "UPDATE",
  ...
}
```

## Getting the posts in

You said you currently **read his research on the Ghost website** (login
required). This prototype takes the write-up as plain text on purpose, so it
works no matter how you obtain it. To go further:

- **Cleanest authorized route:** if his Ghost posts also arrive as a **paid
  email newsletter**, pull them from your inbox via the Gmail API / IMAP. You're
  a paying subscriber reading your own mail — far less fraught than scraping.
- **Website session:** automating a logged-in browser session is possible but
  brushes up against his terms of service. Keep anything like this to
  **personal use** (distilling what you already pay to read); don't
  redistribute his content.

I left fetching out of the prototype so the core — distillation quality — is
what you evaluate first.

## Before you automate anything

You said the intended use is to **feed the signal into automated trading**.
That's a meaningful jump from this prototype. The signal here captures *one
person's opinion as structured data* — it is **not** a statistically validated
alpha signal. Before any of it touches live orders:

1. **Backtest the author.** Log every signal with a timestamp and track forward
   returns. You need evidence that his calls actually work, and at what horizon,
   before sizing anything on them.
2. **Separate signal from execution.** Even with a good signal, position
   sizing, risk limits, slippage, and kill-switches are their own problem.
3. **Expect extraction noise.** An LLM occasionally mis-reads conviction or a
   ticker. The `rationale` field exists so every score is auditable; in an
   automated path you'd want a confidence threshold and human spot-checks.
4. **Mind the legal line.** Trading on a paid newsletter for your own account is
   ordinary; republishing or reselling the signals is not.

Treat this as the first link in a longer chain, not a finished trading system.

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
| `requirements.txt`   | Dependencies                                                  |

---

# Mag 7 News Bot (Telegram)

A separate product in this repo (`mag7bot/`): a long-running Telegram service
that delivers **source-verified** news on the Magnificent Seven to a private
channel, configured privately via a 1:1 DM with the bot. Every alert carries a
source tier and a canonical link to a primary or reputable source — no social
media, no unverifiable noise. Built to the spec in `mag7newsbotprd.md`.

```
SEC EDGAR + Finnhub ─► whitelist ─► classify ─► dedup ─► materiality
                                                              │
                              material ──► instant push ──┐   │
                              low ────────► daily digest ─┴──►  📢 channel
        owner DM ──► user_id auth gate ──► commands ──► state DB
```

- **Two surfaces (PRD §3):** the owner DM is the *control plane* (commands,
  confirmations); the private channel is the *publish plane* (broadcast-only
  alerts + digests). Commands run **only** in the owner DM and **only** for the
  stored owner `user_id`; everyone else gets a refusal.
- **Verified links (PRD §4):** a domain whitelist drops anything outside the
  approved publisher set. Single-source, non-primary items are labelled
  `⚠️ unconfirmed`; the same story across sources collapses to one
  `✅ cross-confirmed (N)` alert (6h window).
- **Routing (PRD §6, §10):** rule-based materiality sends 8-K / earnings / M&A /
  legal / management / index changes as instant pushes (critical types override
  quiet hours 00:00–07:00 SGT); analyst actions and minor news go to the daily
  digest.

## Try it offline (no credentials)

```bash
pip install -r requirements.txt
python -m mag7bot.app --dry-run
```

This runs canned fixtures through the entire pipeline and prints the formatted
alerts + a sample digest — proving the verified-link promise with zero API keys.

## Run it live

```bash
cp .env.example .env        # then fill in the values (see below)
python -m mag7bot.app
```

Before the first live run, verify connectivity without starting the poll loop:

```bash
python -m mag7bot.app --check    # ✅/❌ for token, channel reachable, can-post
```

`--check` (and every live launch) runs a preflight: it calls `getMe`, resolves
the channel, and confirms the bot is a channel admin with "Post Messages" — so
setup problems surface immediately instead of failing silently mid-loop.

Setup: create a private channel, add the bot (from @BotFather) as an admin with
post permission, and put the channel id + your numeric Telegram user id in
`.env`. Required env vars are documented in `.env.example`:
`TELEGRAM_BOT_TOKEN`, `OWNER_USER_ID`, `CHANNEL_ID`, `FINNHUB_API_KEY`,
`SEC_EDGAR_USER_AGENT` (SEC requires a contact email), plus optional
`DIGEST_TIME_SGT`, `SUMMARY_MODE` (`verbatim` default, or `llm`) and
`ANTHROPIC_API_KEY` (only for `llm` mode).

## Deploy on Railway

The repo ships a **`Dockerfile`** (the build path Railway uses when present) plus
a `railway.json` (Dockerfile builder, restart-on-failure). The bot is a
**worker** — it has no HTTP port; don't add a domain or healthcheck.

> Railway's auto-detect builder (Railpack/Nixpacks) fails on this repo with
> *"No start command detected"* because the entrypoint is a package
> (`mag7bot/app.py`), not a root `main.py` or a web framework. The Dockerfile
> avoids that entirely. If you'd rather use the auto-detect builder, set a
> **Custom Start Command** of `python -m mag7bot.app` in the service settings
> (a root `main.py` shim is also included as a fallback).

1. **New Project → Deploy from GitHub Repo** → pick this repo. Select the branch
   you want (merge the PR to your default branch first, or point Railway at the
   feature branch).
2. **Variables** — add the env vars (same as `.env.example`): `TELEGRAM_BOT_TOKEN`,
   `OWNER_USER_ID`, `CHANNEL_ID`, `FINNHUB_API_KEY`, `SEC_EDGAR_USER_AGENT`, plus
   optional `DIGEST_TIME_SGT` / `SUMMARY_MODE` / `ANTHROPIC_API_KEY`. No `.env`
   file needed — Railway injects these into the environment.
3. **Add a Volume** (critical) — Railway's filesystem is ephemeral, and the
   `seen` table is what prevents duplicate alerts across restarts. Attach a
   volume (e.g. mount path `/data`) and set `DB_PATH=/data/mag7bot.db` so state
   survives redeploys. Without this, every redeploy re-fetches recent news.
4. **Deploy.** On the very first boot the bot runs a one-time *cold-start prime*:
   it marks currently-available items as seen **without alerting**, so you don't
   get a flood of days-old news — only genuinely new events fire after that.

Watch the deploy logs: you should see the preflight ✅ lines, then
`🟢 Cold start — primed N pre-existing item(s)`. After that, DM the bot
`/watchlist` and wait for the first live alert in the channel.

## Commands (owner DM only)

| Command | Action |
|---------|--------|
| `/watchlist` | Show tracked tickers |
| `/add TSLA` · `/remove AMZN` | Edit the watchlist |
| `/digest 0900` | Set the daily digest time (SGT) |
| `/mute NVDA 24h` | Mute a ticker temporarily (units `m`/`h`/`d`) |
| `/categories [TICKER [type]]` | View / toggle event types per ticker |
| `/sources` | Show the active source whitelist |
| `/show` | Expand items from the last digest |

## Layout

| Path | Purpose |
| ---- | ------- |
| `mag7bot/config.py` | Env/secrets, whitelist, SGT tz, tunables |
| `mag7bot/schemas.py` | `RawItem` / `Event` + enums |
| `mag7bot/db.py` | SQLite state (companies, feed, watchlist, raw_items, events, seen) |
| `mag7bot/companies.py` | Mag7 ticker → CIK / name / groups |
| `mag7bot/sources/` | EDGAR + Finnhub adapters, offline fixtures |
| `mag7bot/pipeline/` | whitelist · classify · dedup · materiality · summarize · formatter |
| `mag7bot/ingest.py` | One ingest cycle: fetch → pipeline → events → route |
| `mag7bot/commands.py` | Telegram command handlers + owner auth gate |
| `mag7bot/publisher.py` | Channel publish (instant push + digest) |
| `mag7bot/scheduler.py` | JobQueue polling + daily digest jobs |
| `mag7bot/app.py` | Entry point (`--dry-run` / live) |
| `tests/test_pipeline.py` | Unit + end-to-end pipeline tests |

> **Note:** this is decision/alerting infrastructure, not trading. The same
> caveats as the prototypes above apply before anything touches order flow.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```
