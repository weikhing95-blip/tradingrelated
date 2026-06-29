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

> **This is the actively-developed product in the repo.** The "Distilling AJ
> research" prototypes above are parked in the backlog.

A separate product in this repo (`mag7bot/`): a long-running Telegram service
that delivers **source-verified** news on the Magnificent Seven (plus MU/PLTR
and US macro/Fed) to a private channel, configured privately via a 1:1 DM with
the bot. Every alert carries a canonical link to a primary or reputable source —
no social media, no unverifiable noise. Built to the spec in `mag7newsbotprd.md`.

> **Goal:** a **fast, high-signal, source-verified market feed** for a focused
> watchlist (Mag 7 + MU/PLTR) plus US macro & the Fed — matching the speed and
> density of **Walter Bloomberg / SM News** in a consistent house voice.
> Each alert: `$TICKER` first, a bite-size fact-forward summary (hard numbers
> when available), a verified `(link)`, and a timestamp. **24/7**, **firehose**
> volume, deduped/cross-confirmed, with curated-channel relay (Walter/SM/Kobeissi)
> re-summarised into the house format — relaying both watchlist-company posts and
> **US macro/Fed** posts (Fed, CPI, rates, jobs, tariffs → tagged `MACRO`). Knobs:
> `FEED_VOLUME`, `QUIET_HOURS`, `SUMMARY_MODE`.

```
EDGAR · Finnhub · earnings · macro · Fed · halts · ratings · Google/Yahoo
        + channel relay (Walter Bloomberg / SM News / Kobeissi)
                 │
                 ▼
 whitelist ─► relevance ─► recency ─► classify ─► dedup ─► materiality ─► summarize
                                                                │
                              material/push ──► instant alert ──┐   │
                              low ──────────────► daily digest ─┴──►  📢 channel
        owner DM ──► user_id auth gate ──► commands ──► state DB
```

- **Two surfaces (PRD §3):** the owner DM is the *control plane* (commands,
  confirmations); the private channel is the *publish plane* (broadcast-only
  alerts + digests). Commands run **only** in the owner DM and **only** for the
  stored owner `user_id`; everyone else gets a refusal.
- **Verified links (PRD §4):** a domain whitelist (exact URL-host / whole-word
  publisher match) drops anything outside the approved set. The same story
  across sources — **and across tickers, by canonical URL** — collapses to one
  `✅ cross-confirmed (N)` alert (6h window).
- **Noise filters:** opinion/listicle headlines, auto-generated price-move filler
  ("X Moves -5.7%", "Stock Up 3%"), off-topic relay posts, and aggregator/consent
  boilerplate are all dropped — only new information reaches the channel.
- **Routing (PRD §6, §10):** rule-based materiality sends 8-K / earnings / M&A /
  legal / management / halts / index changes as instant pushes; analyst actions
  and minor news go to the daily digest. Quiet hours are **off by default
  (24/7)**; critical types override them when enabled.

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
`DIGEST_TIME_SGT`, `SUMMARY_MODE` (`verbatim` default, or `llm`),
`ANTHROPIC_API_KEY` (for `llm` mode), `FRED_API_KEY` (macro), the source toggles
(`ENABLE_*`), and tunables `FEED_VOLUME` / `QUIET_HOURS` / `MAX_ITEM_AGE_HOURS` /
`DEDUP_WINDOW_HOURS`. See `.env.example` for the full annotated list.

### Channel relay (optional, reads Walter Bloomberg / SM News / Kobeissi)

The relay reads curated public channels via a **pyrogram user client** (your own
Telegram account, not the bot token). Set `TELEGRAM_API_ID` + `TELEGRAM_API_HASH`
(from my.telegram.org), then authenticate once to create the session:

```bash
python -m mag7bot.telegram_setup     # phone + OTP (+2FA) → telegram_user.session
```

On a host with no file upload (e.g. Railway), set `TELEGRAM_SESSION_B64` to the
gzip+base64 of that session file (`gzip -c telegram_user.session | base64 -w0`)
and the bot writes it on boot. Your account must **join** each channel to receive
its posts — verify from the DM with `/channels` (shows ✅ joined per channel).

## Coverage: macro, earnings, halts, ratings

Beyond per-company news, the bot covers what moves these names:

- **Economic releases (`macro`, needs `FRED_API_KEY`):** polls FRED for **Core
  CPI, CPI, Core PCE, PCE, PPI, and initial jobless claims** and posts on each
  new release, e.g. `📊 US Core CPI (May 2026) — +0.3% MoM, +3.2% YoY`. These are
  economy-wide (no ticker), Tier 1, treated as critical (instant push). Off
  until `FRED_API_KEY` is set (free from fredaccount.stlouisfed.org).
- **Earnings (`earnings`, uses the Finnhub key):** a one-time **upcoming-earnings
  preview** a few days before a watched name reports
  (`$AAPL Q3 2026 earnings expected Thu 31 Jul (after close) — consensus EPS $1.42, Rev $85.9B est`),
  then the **actuals vs estimates** when it reports
  (`$AAPL Q2 2026 earnings — EPS $1.52 vs $1.50 est ✅ beat · Rev $94.8B vs $94.5B est ✅ beat`).
- **Executive commentary:** CEO/CFO interviews and earnings-call remarks in the
  news feeds are detected (🎙) and pushed. Best-effort — depends on the wires
  covering it.
- **Trading halts (`halts`, free):** polls the Nasdaq Trader trading-halts RSS
  and posts when a watched name is halted or resumes, e.g.
  `🛑 NVDA trading halted (LUDP)`. Tier 1, critical (instant push). Toggle with
  `ENABLE_TRADING_HALTS`.
- **Analyst ratings (`ratings`, uses the Finnhub key):** a structured
  upgrade/downgrade/initiation feed (firm + from→to grade), e.g.
  `$NVDA Morgan Stanley upgrades NVDA to Overweight (from Equal-Weight)` — more
  reliable than guessing from headlines. Genuine rating changes are material;
  reiterations are dropped. Toggle with `ENABLE_ANALYST_RATINGS`.

Structured sources (EDGAR, earnings, macro, halts, ratings) bypass the publisher
whitelist and the company-relevance gate — they're trusted data, not free-text
news.

## Summaries (bite-size)

Each alert is three lines — `$TICKER`, the bite-size summary, then a compact
verified link + timestamp. Two modes (`SUMMARY_MODE`):

- **`verbatim`** (default, free, no API): uses the source's own
  summary/description blurb when it carries real content — a 1–3 sentence lede —
  otherwise falls back to the headline. Accurate, zero cost, no hallucination
  risk.
- **`llm`** (needs `ANTHROPIC_API_KEY`): for news items the bot **fetches the
  article and extracts its body** (so the summary carries the real substance —
  e.g. a price-target or earnings figure buried past the headline), then Claude
  compresses it into a neutral 1–2 sentence summary on a **cheap fast model
  (Haiku** by default; override with `SUMMARY_MODEL`). Extraction follows a
  priority ladder — **JSON-LD `articleBody`** (the clean full text reputable
  publishers embed for SEO, and often the only copy on JS-rendered pages) →
  prose from the `<article>`/`<main>` region → a description blurb — so the
  summarizer gets real substance instead of a thin headline. A source-faithfulness
  guard rejects any summary that introduces a **number or a proper noun absent
  from the source**, falling back to the verbatim blurb. Article fetch is
  best-effort (paywalled pages fall back to the blurb) and toggled by
  `ENABLE_ARTICLE_FETCH`.
  *(The Anthropic API is pay-as-you-go and separate from a Claude Max subscription
  — Max cannot fund it.)*

**Feed behaviour:**
- **`FEED_VOLUME`** — `firehose` pushes all on-watchlist items instantly (incl.
  minor; LLM-summarised); `moderate` pushes material/critical only (minor →
  digest); `low` pushes critical only.
- **`QUIET_HOURS`** — default off = **24/7**; set `true` to mute non-critical
  pushes 00:00–07:00 SGT.

## Observability & health

- **`/status`** — 24h event counts, last push, relay-monitor liveness, and
  **per-source health** (last fetch count, freshness, error streak).
- **`/channels`** — monitored relay channels with a **live membership check**
  (✅ joined vs ⚠️ not-joined/unresolved).
- **Failure alerts** — a source breaking, recovering, or a failed channel post
  sends the owner a one-time DM; a broken source is isolated so it never aborts
  the polling cycle.

## Optional: Yahoo Finance (breadth source)

Set `ENABLE_YAHOO_NEWS=true` to add Yahoo Finance's per-ticker headline RSS as a
supplementary Tier-2 source. Off by default; toggle anytime. Links are direct
(no redirect wrapper) and the query is per-ticker, so it's lower-noise than
Google News; it's whitelist-filtered and dedup cross-confirms with Finnhub.
*(The Yahoo RSS endpoint is undocumented and historically flaky — the source
fails soft if it's unavailable.)*

## Source-research agent + extensible whitelist

`/suggest_sources` runs a Claude agent that proposes reputable publishers (with a
suggested tier + rationale) **not already** whitelisted — you stay in control and
approve with `/add_source <domain>`. Approved domains persist in the DB and take
effect on the next poll (no redeploy). `/sources` shows config defaults plus your
additions; `/remove_source <domain>` removes an addition. The whitelist itself
lives in `mag7bot/data/whitelist_domains.txt` (edit without touching code).

> The **weekly autonomous** version of this agent runs on Opus and is **off by
> default** (it can consume the budget meant for the cheap Haiku summaries).
> Enable it with `ENABLE_RESEARCH_AGENT=true` only if you want unattended weekly
> source discovery. The interactive `/suggest_sources` command works regardless
> (needs `ANTHROPIC_API_KEY`).

Use `/diag` anytime to live-probe every configured source and see how many items
each returns — handy for confirming connectivity when the channel is quiet.

## Optional: Google News (breadth aggregator)

Set `ENABLE_GOOGLE_NEWS=true` to add the undocumented Google News RSS feed as a
supplementary Tier-2 source (PRD §4, F11). It's **off by default** and safe to
toggle on/off anytime — it's a breadth safety net behind the dedicated APIs, not
a backbone.

Because Google News aggregates *everyone*, two safeguards apply automatically:
the **domain whitelist is re-applied** to each item's actual publisher (from the
feed's `<source>` element), and the `news.google.com` redirect links are
**best-effort resolved** to the canonical publisher URL. Items from
non-whitelisted publishers are dropped; overlapping stories **cross-confirm**
with Finnhub via the existing dedup rather than duplicating. Enabling it on an
already-running bot won't replay old news — the new source is primed silently on
the next start (same cold-start logic as a fresh deploy).

> Caveats (per the PRD): the feed is undocumented and can change without notice,
> and it's marked personal/non-commercial — fine for a single owner reading
> privately, revisit before any shared/commercial use.

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
`🟢 Primed N pre-existing item(s) as seen`. After that, DM the bot `/status`
to confirm health and `/watchlist`, then wait for the first live alert.

## Commands (owner DM only)

| Command | Action |
|---------|--------|
| `/watchlist` | Show tracked tickers |
| `/add TSLA` · `/remove AMZN` | Edit the watchlist |
| `/digest 0900` | Set the daily digest time (SGT) |
| `/mute NVDA 24h` | Mute a ticker temporarily (units `m`/`h`/`d`) |
| `/categories [TICKER [type]]` | View / toggle event types per ticker |
| `/sources` | Show the active source whitelist (config + approved additions) |
| `/show` | Expand items from the last digest |
| `/status` | 24h health: events, last push, relay liveness, per-source health |
| `/channels` | List relay channels + live membership check (✅ joined) |
| `/add_channel @user [name]` · `/remove_channel @user` | Manage relay channels |
| `/test` | Post a sample alert to the channel (publish health-check) |
| `/diag` | Live-probe each news source and report how many items it returns |
| `/suggest_sources` | Agent proposes reputable publishers to add (needs `ANTHROPIC_API_KEY`) |
| `/add_source <domain> [name] [tier]` · `/remove_source <domain>` | Approve / remove a whitelist publisher |

## Layout

| Path | Purpose |
| ---- | ------- |
| `mag7bot/config.py` | Env/secrets, SGT tz, tunables |
| `mag7bot/data/whitelist_domains.txt` | Approved-publisher whitelist (edit without code) |
| `mag7bot/schemas.py` | `RawItem` / `Event` + enums |
| `mag7bot/db.py` | SQLite state (companies, feed, watchlist, raw_items, events, seen, source_health, telegram_channels) |
| `mag7bot/companies.py` | Ticker → CIK / name / aliases (Mag7 + MU/PLTR) |
| `mag7bot/sources/` | edgar · finnhub · earnings · macro · fed · insider · halts · ratings · google_news · yahoo_news + fixtures |
| `mag7bot/pipeline/` | whitelist · relevance · classify · dedup · materiality · summarize · formatter |
| `mag7bot/article.py` | Fetch + extract article text for richer LLM summaries |
| `mag7bot/ingest.py` | One ingest cycle: fetch → enrich → pipeline → events → route |
| `mag7bot/commands.py` | Telegram command handlers + owner auth gate |
| `mag7bot/telegram_monitor.py` | Relay: reads curated channels via a pyrogram user client |
| `mag7bot/publisher.py` | Channel publish (instant push + digest) |
| `mag7bot/scheduler.py` | JobQueue polling + daily digest jobs |
| `mag7bot/app.py` | Entry point (`--dry-run` / `--check` / live) |
| `tests/test_pipeline.py` | Unit + end-to-end pipeline tests |

> **Note:** this is decision/alerting infrastructure, not trading. The same
> caveats as the prototypes above apply before anything touches order flow.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```
