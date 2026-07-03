# Mag 7 News Bot (Telegram)

A long-running Telegram service that delivers **source-verified** market news
on the Magnificent Seven (plus MU/PLTR and US/global macro & central banks) to a
Telegram channel, configured privately via a 1:1 DM with the bot. Every alert
carries a canonical link to a primary or reputable source — no social-media
noise, no unverifiable claims. Built to the spec in `mag7newsbotprd.md`.

> **Public / commercial mode:** set `PUBLIC_MODE=true` to hard-restrict the feed
> to sources licensed for commercial redistribution (EDGAR, FRED, Fed RSS, Nasdaq
> halts, computed price-moves). Commercially-unsafe sources are excluded **in
> code**, not by env convention. See [`docs/SOURCE_LICENSES.md`](docs/SOURCE_LICENSES.md)
> for the per-source basis and [`mag7bot/ROADMAP.md`](mag7bot/ROADMAP.md) for the
> launch plan.

> Early standalone experiments (AJ-research distillers) are archived under
> [`prototypes/`](prototypes/) and are **not** part of this product.

> **Goal:** a **fast, high-signal, source-verified market feed** for a focused
> watchlist (Mag 7 + MU/PLTR) plus **macro & central banks — US and the major
> economies (UK/EU/JP)**. The product is **precision on a focused watchlist**, not
> a firehose: a **curated, low-noise feed tuned to the owner's definition of
> signal** (it learns what to surface and how to say it), where every post is
> worth reading. Instant push is reserved for Tier-1 events; everything else rolls
> into the daily digest. Each alert: `$TICKER`(s) first, a bite-size fact-forward
> summary (hard numbers when available), a verified `(link)`, and a timestamp — in
> a consistent house voice. **24/7**, deduped/cross-confirmed. Coverage spans
> primary + wire — EDGAR, Benzinga/Alpaca, earnings, analyst ratings, trading
> halts, **unusual price moves**, and a **daily economic-calendar preview** —
> plus curated-channel relay (Walter/SM/Kobeissi) re-summarised into the house
> format (watchlist-company *and* macro/Fed posts → tagged `MACRO`). Knobs:
> `FEED_VOLUME`, `QUIET_HOURS`, `SUMMARY_MODE`.

```
EDGAR · Finnhub · Alpaca/Benzinga · earnings · macro · Fed · halts · ratings · price-moves · Google/Yahoo
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
- **Dedup (lexical + semantic):** near-identical headlines and shared URLs
  collapse deterministically (free). A **semantic check** (cheap Haiku call,
  `ENABLE_SEMANTIC_DEDUP`, llm mode) then catches the same story **re-worded by a
  different outlet** — no shared words or URL — that lexical dedup misses. It only
  runs when a recent same-company alert exists to compare against, so it rarely
  fires; a match merges the link instead of reposting.
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
- **Economic-calendar preview (`ENABLE_ECON_CALENDAR`, free):** a once-a-day
  forward heads-up of the day's **high-impact** macro releases with consensus +
  previous, e.g. `🇬🇧 GBP · GDP (QoQ, Q1) — 🕒 09:00 SGT / prev 0.2% · est 0.6%`.
  Uses the free ForexFactory weekly feed (no key); posts at `ECON_CALENDAR_TIME_SGT`
  for the configured `ECON_CALENDAR_CURRENCIES` / `ECON_CALENDAR_IMPACT`. Skips
  days with nothing qualifying. On demand any time with **`/calendar`**.
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
- **Unusual price moves (`pricemove`, free):** polls the Yahoo chart API and
  flags a name when its move *on the day* is large **relative to its own recent
  behaviour** — at least `PRICE_MOVE_MULTIPLIER`× (default **2×**) the trailing
  2-week average daily move *and* above a `PRICE_MOVE_MIN_PCT` floor (default
  **3%**). The alert itself is short and precise — just the move and last price,
  e.g. `$NVDA — up 6.2% on the day — last $110.40`. One alert per name per day.
  This is a *computed* signal, deliberately distinct
  from the auto-generated "X Moves 5%" news articles the relevance filter drops
  (those are stale, substance-free filler). Material (push), not a quiet-hours
  override. Toggle with `ENABLE_PRICE_MOVE`.

- **Alpaca / Benzinga news (`alpaca`, free key):** Alpaca's News API streams
  **Benzinga's real-time professional wire** — and unlike a headline-only RSS
  feed it carries the **full article body**, so the summarizer works from real
  substance with no fetch/extraction guesswork. Benzinga tags each story's
  tickers itself (more reliable than alias-matching the headline), so it's
  treated as a trusted structured source; the low-quality/filler filter still
  applies. Needs free `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` (market-data keys —
  no funded account). Off until the keys are set. *(Posting house-style summaries
  rather than verbatim content keeps clear of Benzinga's redistribution terms.)*

Structured sources (EDGAR, earnings, macro, halts, ratings, price-moves, Alpaca)
bypass the publisher whitelist and the company-relevance gate — they're trusted
data, not free-text news.

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

## Going public

The bot scales to any audience for free — a channel broadcasts one copy whether
it has 5 or 50,000 subscribers, and LLM cost is **per-event, not per-subscriber**.
Before sharing the channel publicly:

1. **`PUBLIC_MODE=true`** (legal gate) — a public product may only republish from
   sources licensed for **commercial redistribution**. This flag hard-excludes
   every commercially-unsafe source **in code** (Finnhub, Google News, Yahoo,
   Alpaca/Benzinga, the channel relay, and the ForexFactory econ-calendar),
   leaving only EDGAR, FRED, Fed RSS, Nasdaq halts, and computed price-moves. It
   is not an env convention that can be toggled back on per-source — the
   exclusion is enforced at wiring time *and* before every fetch. The per-source
   basis is documented in [`docs/SOURCE_LICENSES.md`](docs/SOURCE_LICENSES.md).
   **Note:** the safe feed is much thinner than the personal firehose — to
   restore breadth, license a commercial news API (see the licence doc's COGS
   notes).
2. **`PUBLIC_CHANNEL=true`** — keeps the owner-only 👎/✏️ curation buttons **off**
   the public posts (so subscribers never see controls they can't use) and instead
   DMs the owner a mirror of each alert *with* the buttons. Curate privately; the
   channel stays clean.
3. **`/post_disclaimer`** — posts and pins a "not financial advice" disclaimer
   (the daily digest + calendar also carry a one-line footer). Needs the bot to
   have "Pin Messages" admin rights.
4. **`PUBLIC_CHANNEL_HANDLE=@yourchannel`** — every public alert then carries a
   forward-friendly footer (`@yourchannel · ℹ️ Not financial advice`) that
   survives Telegram forwards, so a reshared post still points home.
5. Keep any linked **discussion group** off or moderated, and keep posting
   house-style **summaries** (not verbatim source text) to stay clear of data
   providers' redistribution terms.

A weekly **"what you missed"** roundup (`ENABLE_WEEKLY_ROUNDUP`, Sundays) posts
the week's top events by tier — a shareable recap that doubles as growth content.

## Observability & health

- **`/status`** — mode (personal/public), 24h event counts, last push,
  relay-monitor liveness, **per-source health** (last fetch count, freshness,
  error streak), and **last backup / last healthcheck ping**.
- **`/metrics`** — 7-day launch KPIs: **median latency by tier** (publish→post),
  posts/day, **👎 rate** (target <5%), and dedup collapse rate. Every published
  alert writes a `metrics` row; these are the numbers that gate going public.
- **`/channels`** — monitored relay channels with a **live membership check**
  (✅ joined vs ⚠️ not-joined/unresolved).
- **Failure alerts** — a source failing a *sustained* run of polls, recovering,
  or a failed channel post sends the owner a one-time DM; a broken source is
  isolated so it never aborts the polling cycle. Transient single blips are
  silent.
- **Backups & recovery** — a nightly job (`BACKUP_TIME_SGT`) DMs the owner a
  gzipped DB snapshot (off-volume safety net); `scripts/restore_db.py` restores
  it without a duplicate-flood (the `seen` table is preserved). Recovery steps
  for volume loss / token revocation / source outage / redeploy are in
  [`docs/RUNBOOK.md`](docs/RUNBOOK.md).
- **External liveness** — set `HEALTHCHECK_PING_URL` and the bot pings it each
  successful poll cycle, so a monitor (e.g. healthchecks.io) alerts if the worker
  dies.

## Curation learning (non-blocking feedback)

The feed never waits for approval — posts go out instantly. To curate it, every
alert carries two **owner-only** buttons (`ENABLE_FEEDBACK_LEARNING`, on by
default; subscribers' taps are ignored):

- **👎 not useful** — *curation.* When the same **(ticker, event type)** is
  flagged **3×**, the bot **auto-promotes a suppression rule** — future matches
  are dropped *before* summarising (so they cost nothing) — and DMs you what it
  learned. Manage with **`/rules`**, **`/unrule <id>`**, **`/feedback`** (tallies).
- **✏️ fix summary** — *summary quality.* The bot DMs you to reply with a better
  summary; your reply is stored as a **house-style few-shot example** and fed to
  the LLM summarizer so future summaries match your tone/specificity. Manage with
  **`/examples`**, **`/forget <id>`** (and **`/cancel`** to abort a pending edit).

It's the SENTINEL pattern adapted to never block posting: corrections become
durable rules (curation, deterministic, no LLM cost) and durable style examples
(summaries). Fully transparent and reversible.

### The "soul" (house voice that learns)

Beyond the rotating ✏️ examples, the bot keeps a durable **house-voice style
guide** — its *soul* — persisted on the data volume (`soul.md`) and injected into
every LLM summary. It's the permanent memory the 5-example window lacks. Two ways
it improves:
- **You** edit it — `/soul` to view, `/soul_reset` to restore the default.
- **The LLM** refines it — a **weekly review** (or `/soul_review` on demand)
  distils your accumulated 👎/✏️ feedback into an updated guide, auto-applied with
  the previous version kept as `soul.prev.md` and a DM to you.

Safety: the soul carries **style only**. The hard faithfulness / no-fabrication
rules live in an immutable system prompt the review can't touch, so a bad soul
edit can never weaken the guardrails.

## Yahoo Finance (breadth wire)

Yahoo Finance's per-ticker headline RSS is **on by default** (`ENABLE_YAHOO_NEWS`,
free, no key) as a supplementary Tier-2 wire. Links are direct (no redirect
wrapper) so the article extractor reads them well; it's whitelist-filtered and
dedup cross-confirms with Finnhub/Google News. *(The Yahoo RSS endpoint is
undocumented and historically flaky — the source fails soft if it's unavailable.)*

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

## Google News (breadth aggregator)

The Google News RSS feed is **on by default** (`ENABLE_GOOGLE_NEWS`, free) as a
breadth source behind the dedicated APIs (PRD §4, F11). Toggle off anytime.

Because Google News aggregates *everyone*, two safeguards apply automatically:
the **domain whitelist is re-applied** to each item's actual publisher (from the
feed's `<source>` element), and each link is **resolved to the canonical
publisher URL** — first by following the redirect, then (for modern opaque
`news.google.com/articles/<token>` links, which embed no real URL) by **decoding
the token** via Google's internal batchexecute RPC. Once resolved to the real
publisher, the article extractor (JSON-LD + main-content) reads it like any other
wire. The decode is **best-effort**: if Google changes its scheme it degrades to
"skip the link," never an error. Items from non-whitelisted publishers are
dropped; overlapping stories **cross-confirm** with Finnhub/Yahoo via dedup
rather than duplicating. Hard-paywalled publishers (WSJ/FT/Barron's) still can't
be read — they're not whitelisted as article sources for that reason.

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
| `/status` | Health: mode, events, last push, relay liveness, per-source health, last backup/healthcheck |
| `/metrics` | 7-day KPIs: median latency by tier, posts/day, 👎 rate, dedup collapse rate |
| `/channels` | List relay channels + live membership check (✅ joined) |
| `/add_channel @user [name]` · `/remove_channel @user` | Manage relay channels |
| `/test` | Post a sample alert to the channel (publish health-check) |
| `/diag` | Live-probe each news source and report how many items it returns |
| `/suggest_sources` | Agent proposes reputable publishers to add (needs `ANTHROPIC_API_KEY`) |
| `/add_source <domain> [name] [tier]` · `/remove_source <domain>` | Approve / remove a whitelist publisher |
| `/rules` · `/unrule <id>` | List / remove learned 👎 suppression rules |
| `/feedback` | 👎 tallies per ticker+type (trending toward a mute) |
| `/examples` · `/forget <id>` | List / remove ✏️ house-style summary examples |
| `/cancel` | Abort a pending ✏️ summary edit |
| `/soul` · `/soul_reset` | View / reset the learned house voice (style guide) |
| `/soul_review` | Distil recent feedback into the house voice now (LLM) |
| `/calendar` | Today's high-impact macro calendar (forward preview) |
| `/post_disclaimer` | Post + pin the "not financial advice" disclaimer (before going public) |

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

> **Note:** this is decision/alerting infrastructure, not trading — it reports
> news, it does not place orders.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```
