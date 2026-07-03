# Mag 7 News Bot — Product Requirements (PRD v0.4)

> **Status:** v0.4 — revised for the public-launch pivot (broadcast media product).
> Supersedes the original private-alerting spec. Implementation lives in `mag7bot/`.

---

## §1. Product definition

The Mag 7 News Bot is a **broadcast media product**: a single Telegram channel
that publishes fast, **source-verified** market news for a focused watchlist
(the Magnificent Seven + MU/PLTR) plus US and major-economy macro / central-bank
events. It is a *publish-only* channel with a private owner control plane (1:1
DM). Every post carries a canonical link to a primary or reputable source.

**Tiers (the business model):**

| Tier | Content | Timing | Price |
|------|---------|--------|-------|
| **Free** | Daily digest + the daily macro-calendar preview | **Delayed** (digest is a once-daily roll-up; any real-time item is delayed by `FREE_TIER_DELAY_HOURS` before it reaches free subscribers) | Free |
| **Paid** | Real-time instant pushes (filings, halts, earnings, price-moves, ratings) | **Live**, as events happen | Subscription (see §Pricing) |

The free tier is the funnel; the paid tier monetises **latency** — the value is
being told *now*, not at end of day. Delaying real-time events for the free tier
requires a delayed-publish queue (Phase 6 — design doc first).

**Explicitly out of scope:**
- **Multi-tenant SaaS.** There is one owner, one channel, one watchlist. No
  per-user watchlists, per-user feeds, or self-serve tenant onboarding. The
  `feed_id`-keyed schema *could* support it later, but the product is not that.
- **Trading / execution.** This is a news product. It reports; it never places
  orders or gives buy/sell recommendations (see §Compliance).
- **Unlicensed sources in public mode.** Only commercially-safe sources publish
  (`PUBLIC_MODE`; see `docs/SOURCE_LICENSES.md`).

**Legal constraint (non-negotiable):** the public product may only publish from
sources licensed for commercial redistribution. This is enforced in code
(`PUBLIC_MODE` hard-excludes unsafe sources — not an env convention).

---

## §2. Coverage & sources

**Watchlist:** AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA + MU, PLTR. Plus
economy-wide macro/Fed (no ticker → tagged `MACRO`).

**Public-mode (commercially-safe) sources:** SEC EDGAR filings · FRED macro
releases · Federal Reserve RSS · Nasdaq trading-halts · computed unusual
price-moves. See `docs/SOURCE_LICENSES.md` for the per-source redistribution
basis and the licensed-API options that would restore breadth.

**Personal-mode-only sources (hard-disabled in public mode):** Finnhub
(news/earnings/ratings/insider), Google News, Yahoo Finance, Alpaca/Benzinga,
the pyrogram channel relay, and the ForexFactory econ-calendar.

---

## §3. Two surfaces

- **Control plane** — the owner DM. Commands run only there, only for the stored
  owner `user_id`; everyone else is refused. Curation (👎/✏️), config, health.
- **Publish plane** — the channel. Broadcast-only: instant alerts + the daily
  digest + the daily macro-calendar preview. In a public channel the curation
  buttons are kept off public posts and mirrored to the owner DM
  (`PUBLIC_CHANNEL`).

---

## §4. Verified links & de-duplication

A domain whitelist drops anything outside the approved publisher set. The same
story across sources/tickers collapses (by canonical URL, then lexical, then a
gated semantic LLM check) into one `✅ cross-confirmed (N)` alert within a 6h
window. Faithfulness guard rejects any summary that introduces a number or
proper noun absent from the source.

---

## §5. Message format

`$TICKER`(s) first · bite-size fact-forward summary (hard numbers when present) ·
verified `(link)` · timestamp (SGT). Consistent house voice (`soul.md`). Public
alerts additionally carry a forward-safe footer (channel handle + one-line
disclaimer).

---

## §6. Materiality & routing

Rule-based by event type. Push: 8-K, earnings, M&A, legal/regulatory,
management change, halts, index changes, unusual price-moves. Digest: analyst
actions, minor news. Critical types (halts, earnings, M&A) override quiet hours
when those are enabled (off by default — 24/7).

---

## §Pricing hypothesis

**What's being sold:** real-time latency + a curated, low-noise, source-verified
feed. Free users get the same facts on a delay; paid users get them live.

**Price hypothesis:** a low monthly price is the right entry point for a
retail-trader audience — anchor **~US$9–15/month** (or a local-currency
equivalent), with an annual plan at ~2 months off. Validate against actual
free-tier conversion before committing (Phase 6 gate).

### Payment-rail comparison

| Rail | Fee | Payout | Pros | Cons |
|------|-----|--------|------|------|
| **Telegram Stars** | Telegram takes a platform cut; Apple/Google IAP fees apply when purchased in-app (up to ~30%) | Via Telegram → limited withdrawal options (Fragment/TON) | Native in-app checkout; no external site; lowest friction inside Telegram; built-in subscription support for channels | High effective take rate when routed through app-store IAP; payout/withdrawal friction; Telegram-locked |
| **Whop** | ~3% + payment-processor fees (Stripe ~2.9% + $0.30) → ~**6% all-in** | Direct to bank via Stripe | Purpose-built for paid communities/Telegram; handles subscriptions, dunning, affiliates; card + Apple/Google Pay on web (avoids IAP cut) | External checkout page (more friction than in-app); another platform dependency |
| **Stripe direct** (+ own bot billing) | ~2.9% + $0.30 | Direct to bank | Lowest fees; full control | Must build subscription lifecycle, invite-link gating, and churn handling yourself |

**Working assumption:** start with **Whop** (fastest path to a compliant paid
Telegram tier without building billing, ~6% all-in, avoids the app-store 30%),
and revisit **Telegram Stars** if native in-app conversion proves materially
higher. Confirm current fee schedules at launch — rates change.

> Payment-rail fees are the COGS denominator alongside any licensed-data cost
> (`docs/SOURCE_LICENSES.md`). Price must clear (data licence + rail fee) per
> subscriber with margin before the paid tier is worth running.

---

## §Launch KPIs

Measured live via the `metrics` table and the `/metrics` command (Phase 3C).
These are the numbers that gate expansion (Phase 4 exit criteria) and go in the
channel bio.

| KPI | Definition | Target |
|-----|------------|--------|
| **Median Tier-1 latency** | Time from a Tier-1 event (8-K, halt, earnings) being published to our post going out | **≤ 5 minutes** |
| **Posts/day** | Published alerts per day (7-day rolling) | Stable, non-zero; enough to be worth following, not so many it's spam |
| **Precision proxy (👎 rate)** | Share of published alerts the owner marks 👎 "not useful" | **< 5%** |

Secondary: dedup collapse rate (how much duplication the pipeline removes),
per-source health/latency, free→paid conversion (Phase 6).

**Benchmark discipline:** spot-check Tier-1 latency against Walter Bloomberg on
≥3 events/week and log it in `docs/BENCHMARKS.md` (Phase 4).

---

## §Reliability (Phase 3)

- **Backup & restore:** nightly SQLite backup, off-volume or DM'd to the owner;
  `scripts/restore_db.py` restores without duplicate-flooding (the `seen` table
  is preserved so cold-start prime does not re-fire). Runbook in `docs/RUNBOOK.md`.
- **External liveness:** `HEALTHCHECK_PING_URL` pinged each successful poll cycle
  so an external monitor (e.g. healthchecks.io) alerts within ~10 min of the
  worker dying.
- **Observability:** `/status` (health, last backup, last healthcheck, public
  mode), `/metrics` (latency/volume/quality), per-source failure DMs.

---

## §Roadmap

Post-launch expansion (Korean localisation, Discord/X/Threads distribution) is
tracked in `mag7bot/ROADMAP.md`. Monetisation is Phase 6 of the launch plan and
is gated on 4+ weeks of free-tier retention data.
