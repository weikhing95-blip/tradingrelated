# Mag 7 News Bot — Expansion Roadmap

Multi-language + multi-platform distribution. Captured as a plan to pull from
later; **not yet built**. The content engine (sources → events → summary) is
already platform- and language-agnostic, so everything below is *additive* to
the **output layer**, not a rewrite.

## Architecture enabler (already in place)
- **`Publisher` protocol** (`publisher.py`): `ChannelPublisher` / `StdoutPublisher`
  share one interface. New platforms = new `Publisher` implementations; the
  pipeline fans each event out to all configured publishers. No pipeline changes.
- **LLM summary step**: a natural place to add a translation pass.
- **Per-output config**: each publisher can carry its own language + disclaimer.

## Guiding principle
**Distribution amplifies demand; it doesn't create it.** Validate the English
channel has an audience before multiplying outputs.

---

## Phase 0 — Validate (gate before any expansion)
- Reddit soft-launch (SEA-first: r/singaporefi via a weekly thread; then
  r/algotrading / r/SideProject).
- Confirm: content lands, dedup/curation hold under real volume, an audience
  actually subscribes.
- **Exit criteria:** steady subscribers + positive signal → proceed.

## Phase 1 — Korean localization (highest demand)
**Why:** Korean retail (서학개미) are huge holders of US tech + leveraged ETFs;
strong margin culture. Clear product-market fit.
- **Translate the *verified* English summary → Korean** (translate *after* the
  faithfulness guard passes, so translation can't introduce new facts).
- Dedicated **Korean Telegram channel** (don't mix languages in one channel).
- Config: `LANGUAGE` per publish target; multi-channel publish (one per language).
- Korean disclaimer + house voice (`soul`) variant.
- **Cost:** ~1 extra cheap LLM call per pushed alert (or emit EN+KO in one call).
- **Effort:** moderate. **Risk:** low (translation is bounded).

## Phase 2 — Discord (cheapest distribution win)
- `DiscordPublisher` via channel **webhook** (no bot hosting needed).
- Same alerts, same format; map `$TICKER`/links to Discord markdown.
- Popular with active-trading communities.
- **Effort:** low (an afternoon). **Risk:** low.

## Phase 3 — X / Twitter (reach, but paid)
- `XPublisher` via the X API.
- **Cost/risk:** API is paid (~$100/mo Basic tier) with rate limits — gate on
  whether reach justifies it. 280-char (bite-size format fits); thread when long.
- **Effort:** moderate (auth + caps). **Risk:** medium (cost, policy).

## Phase 4 — Threads / Facebook (optional)
- Meta Graph API (Pages) + Threads API. Requires app review.
- FB organic reach for finance is weak; Threads is growing.
- **Effort:** moderate. **Risk:** medium (review, low organic reach).

## Skip — WhatsApp
- **No public API to broadcast to WhatsApp Channels.** The Business/Cloud API is
  for *opt-in customer messaging*; broadcasting market news violates its policy.
  Revisit only if Meta opens a Channels posting API.

---

## Cross-cutting design notes
- **Publisher fan-out:** `run_cycle` pushes each event to every configured
  publisher; one failing platform must not block the others (per-publisher
  try/except, like the per-source isolation already in `run_cycle`).
- **Language layer:** translate the verified summary; never re-run the
  faithfulness guard against translated text (it checks English entities/numbers).
- **Dedup unaffected:** dedup happens on events *before* the output layer, so a
  story is still deduped once and then fanned out to all platforms/languages.
- **Cost scales with (events × languages)**, not subscribers — still cheap on
  Haiku, but worth a guard if many languages are added.
- **Disclaimers per platform/language** (the `/post_disclaimer` text, localised).
- **Curation/learning** (👎/✏️/soul) stays owner-side and language-agnostic; a
  Korean channel could get its own `soul` variant later.

## Rough sequencing
`Phase 0 (validate) → Phase 1 (Korean) → Phase 2 (Discord) → Phase 3 (X) → Phase 4 (Threads/FB)`
WhatsApp: skip.
