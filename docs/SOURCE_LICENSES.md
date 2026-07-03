# Source Licenses & Redistribution Rights

> **Disclaimer:** This document is internal legal-risk documentation for the product owner. It is **not legal advice** and does not create an attorney–client relationship. Verify all clauses against the live primary source and consult a qualified attorney before relying on any verdict for commercial redistribution.

**Last reviewed:** 2026-07-03

**Scope:** Whether each data source may be **redistributed / republished to a public third-party audience** (free public Telegram channel now, paid tier later) — not merely used for personal/internal analysis.

**Verification note:** Several vendor Terms-of-Service pages (finnhub.io, forexfactory.com, alpaca.markets, benzinga.com, google.com legal, yahoo legal) could **not be fetched directly** during this review — they returned HTTP 403 at the outbound gateway / Cloudflare. Where the exact primary clause could not be retrieved, the text below is labelled **[paraphrase — secondary source]** and, per our conservative policy, the verdict defaults to **UNSAFE / VERIFY** until a screenshot of the live primary clause is filed. Only clauses marked **[verbatim]** were confirmed word-for-word.

This mapping is enforced in code: `mag7bot/config.py` → `COMMERCIAL_SAFE_SOURCES` / `is_commercial_safe()`. In `PUBLIC_MODE=true`, every source **not** in that set is hard-excluded before fetch (see `mag7bot/ingest.py::public_safe_sources` and the wiring gate in `mag7bot/app.py`). Unknown/unclassified sources fail safe to UNSAFE.

---

## Summary verdict table

| Source | Verdict (public commercial redistribution) | Basis | Commercial option / cost |
|---|---|---|---|
| **Finnhub** (free tier) — `finnhub`, `earnings`, `ratings`, `insider` | **UNSAFE** | Free/standard tiers do not grant redistribution; redistribution is an explicit *paid* feature | Commercial/enterprise license via `sales@finnhub.io`; modular ~$50–75/mo per data category; all-in-one enterprise **~$3,500/mo** (unverified list price) |
| **ForexFactory** calendar (`ff_calendar_thisweek.json`) — `econ_calendar` | **UNSAFE** | No official API; ToS forbids using content without owner permission; scraping blocked | Licensed alt: **TradingEconomics** (custom, scales w/ distribution) or **FMP** economic calendar (~$100/mo + separate Data Display & Licensing Agreement) |
| **Google News RSS** — `google_news` | **UNSAFE** | Google Terms: no use of others' content without permission; personal, non-commercial only; no robots/reformatting | None for redistribution — must license each underlying publisher directly |
| **Yahoo Finance** (`query1.finance.yahoo.com`) — `yahoo_news` | **UNSAFE** | Undocumented/unofficial endpoint, no license grant; Yahoo ToS bars scraping & redistribution | Official redistribution not offered on these endpoints; would require a commercial data vendor |
| **Alpaca News API / Benzinga** — `alpaca` | **UNSAFE** (without a Benzinga commercial license) | Alpaca does not permit redistribution of API data for business purposes; Benzinga content is licensed | Benzinga commercial/enterprise license (custom quote via Benzinga sales; now also distributed via Polygon.io / "Massive"). No public price. |
| **Channel relay** (pyrogram) — `telegram_monitor` | **UNSAFE** | Republishing another channel's curated posts is republication of others' content without licence | None — remove from public product |
| **SEC EDGAR** — `edgar` | **SAFE** | U.S. Government work, public domain (17 U.S.C. §105) | n/a |
| **FRED** (St. Louis Fed API) — `macro` | **SAFE** | FRED API terms permit use/redistribution with attribution; underlying series mostly public-domain gov data | Free (attribution required) |
| **federalreserve.gov RSS** — `fed` | **SAFE** | U.S. Government work, public domain | Free |
| **Nasdaq trading-halts RSS** — `halts` | **SAFE** | Public regulatory halt notices published as a free public RSS feed | Free |
| **Own computed price-moves** — `pricemove` | **SAFE** | Original derived computation from raw numeric market data (facts/percentages are not copyrightable) | n/a |

---

## 1. Finnhub (finnhub.io) — company news, earnings calendar, analyst ratings, insider transactions

**Verdict: UNSAFE on the free tier for public redistribution.**

**Key evidence.** Finnhub maintains a dedicated page titled **"Pricing for startups and enterprise. Commercial use with redistribution right."** (URL: `https://finnhub.io/pricing-startups-and-enterprise`). The existence of a separate paid track whose headline benefit is *"commercial use with redistribution right"* is strong structural evidence that the **free and standard self-serve tiers do NOT include redistribution rights**, and that redistribution to a public audience is gated behind a negotiated commercial/enterprise agreement.

Finnhub FAQ / support material further states that a subscriber **"must delete all of [Finnhub's] proprietary data at the end of [the] subscription to avoid violation of [the] terms of service, the exchanges' agreement and their partners' terms of service."** **[paraphrase — secondary source; finnhub.io ToS not directly retrievable during review]** This retention restriction is inconsistent with permanent public republication.

Finnhub directs redistribution/commercial-license inquiries to **`sales@finnhub.io`**.

**Primary clause:** *Not retrievable during this review (finnhub.io/terms returned 403 at the gateway).* Per conservative policy, treat free-tier redistribution as prohibited until the verbatim License Grant clause is captured from `https://finnhub.io/terms` and filed. **Action item: screenshot the live `finnhub.io/terms` "License"/"Grant of License" section.**

**Commercial option / cost (list prices, unverified against live page — confirm before using in COGS):**
- Modular self-serve data add-ons: Market Data Basic ~**$49.99/mo**; Fundamentals ~**$50/mo**; Estimates ~**$75/mo**; Economic data ~**$50/mo** (a multi-category stack reaches ~$150–200/mo). These self-serve tiers still do **not** clearly convey public-redistribution rights.
- All-in-one **enterprise subscription ~$3,500/mo** (reported list price), which is the track associated with redistribution rights — exact scope and price must be confirmed in writing with `sales@finnhub.io`.

**URLs:**
- `https://finnhub.io/terms`
- `https://finnhub.io/pricing`
- `https://finnhub.io/pricing-startups-and-enterprise`

---

## 2. ForexFactory economic calendar (`ff_calendar_thisweek.json`)

**Verdict: UNSAFE.**

**Reasoning.** ForexFactory (operated by **Fair Economy, Inc.**, governed by Florida law) offers **no official/licensed API** for its economic calendar; the `ff_calendar_thisweek.json` feed is an internal endpoint that the site actively protects (Cloudflare / anti-scraping). ForexFactory's terms state that **"Users may not use content from [Fair Economy, Inc.'s] Services unless Users obtain permission from its owner or are otherwise permitted by law,"** and that these terms **do not grant the right to use branding/logos.** **[paraphrase — secondary source; forexfactory.com/notices returned 403 during review]** The site also enforces DMCA takedowns and reserves copyright.

Scraping and republishing the calendar to a public channel therefore (a) likely breaches the ToS access/permission terms, and (b) risks a compilation-copyright / hot-news claim on the aggregated calendar. Note: individual economic *facts* (e.g., "US CPI actual = X%") are not themselves copyrightable, but ForexFactory's *curated calendar, forecasts, and formatting* are a protected compilation, and the access method violates the ToS regardless.

**Licensed alternatives (recommended path):**
- **TradingEconomics API** — calendar endpoint available; pricing **"is adjusted to the features you use, your volume of requests, and the distribution you make,"** i.e. redistribution is explicitly priced in. Custom quote required. URL: `https://tradingeconomics.com/api/pricing.aspx`.
- **Financial Modeling Prep (FMP)** economic-calendar API — from **~$100/mo**; however FMP states that **"displaying or redistributing data sourced from FMP requires a specific Data Display and Licensing Agreement with FMP"** **[paraphrase — secondary source]**, so a plain subscription is not sufficient for public redistribution — a Data Display & Licensing Agreement must be executed. URL: `https://site.financialmodelingprep.com/developer/docs/pricing`.

**URLs:**
- `https://www.forexfactory.com/notices`
- `https://tradingeconomics.com/api/pricing.aspx`
- `https://site.financialmodelingprep.com/developer/docs/pricing`

---

## 3. Google News RSS

**Verdict: UNSAFE.**

**Reasoning.** Google News aggregates third-party publishers' headlines/snippets. Google's Terms of Service state that **"you may not use content from our Services unless you obtain permission from its owner or are otherwise permitted by law,"** and Google News guidance limits consumer use of the feed to **personal, non-commercial** reading. Google additionally prohibits using the service **to reformat or display results, or to use any robot, spider, or other device to monitor or copy content from the service.** **[paraphrase — closely tracks the primary clauses; policies.google.com/terms and the Google News Terms of Use returned 403 during review]**

Two independent problems for our use case: (1) Google's own aggregation feed is licensed for personal/non-commercial use, not public re-broadcast; and (2) even if Google's layer were ignored, the underlying content belongs to the individual publishers (Reuters, Bloomberg, AP, etc.), each of whom would need to license it separately. There is no Google product that grants the right to republish Google News output commercially.

**Commercial option:** None from Google for this purpose. Redistribution would require licensing each underlying newswire/publisher directly, or using a purpose-built licensed news API.

**URLs:**
- `https://policies.google.com/terms`
- `https://www.google.com/intl/en_us/terms_google_news.html`

---

## 4. Yahoo Finance undocumented endpoints (`query1.finance.yahoo.com` chart/news)

**Verdict: UNSAFE.**

**Reasoning.** The `query1.finance.yahoo.com` (and `query2...`) chart/news endpoints are **undocumented, unofficial internal APIs** with **no public license grant and no terms authorizing programmatic access**. Libraries such as `yfinance` scrape these endpoints; there is no credential or license under which Yahoo authorizes third-party redistribution of this data. Yahoo's Terms of Service prohibit accessing the services by automated means and reusing/redistributing content commercially. **[paraphrase — Yahoo legal ToS pages returned 403 during review]**

Because access itself is unauthorized and the endpoints can change, break, rate-limit, or block IPs without notice, this source is unsuitable for a published product on both **legal** (no redistribution license) and **reliability** grounds. Undocumented-endpoint use is also a standard cease-and-desist / ToS-breach exposure.

**Commercial option:** Yahoo does not offer redistribution rights on these endpoints. A licensed market-data/news vendor would be required instead.

**URLs:**
- `https://query1.finance.yahoo.com/` (undocumented; no terms)
- Yahoo Terms of Service: `https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html`

---

## 5. Alpaca Market Data News API / Benzinga

**Verdict: UNSAFE without a dedicated Benzinga commercial (redistribution) license.**

**Reasoning.** Alpaca's News API is **"an extension of Alpaca's Market Data API"** and **"all news data is currently provided directly by Benzinga."** The content is Benzinga's licensed intellectual property surfaced through Alpaca; Alpaca is a conduit, not the rights owner.

- **Alpaca's own stance:** Alpaca support states that **Alpaca does not allow redistribution of Alpaca API data for business purposes.** **[paraphrase — Alpaca support "Can I redistribute Alpaca API data via my platform?"; alpaca.markets returned 403 during review]** Alpaca's EULA restricts users from **"swapping, renting, sublicensing, transferring, selling, uploading, downloading, displaying or offering [the] databases to any other person or entity … except as expressly permitted."** **[paraphrase — secondary source]**
- **Benzinga's stance:** Benzinga news is premium licensed content. Redistributing tagged/real-time Benzinga news to an external public audience requires a **separate Benzinga commercial/redistribution license** — this is a negotiated contract, not included in an Alpaca subscription and not listed on any public pricing page. (Benzinga's premium feed is, as of 2026, also distributed via **Polygon.io / "Massive"**, again under commercial licensing.)

The Alpaca News API is explicitly positioned for showing news **in-app to an app's own end-users** under the customer's Alpaca relationship, which is materially different from re-broadcasting full headlines/articles to an open public Telegram channel.

> **Note:** the current bot already posts *house-style summaries* rather than verbatim Benzinga text, which reduces (but does not eliminate) the redistribution exposure. For the public product, `alpaca` is classified UNSAFE and hard-disabled in `PUBLIC_MODE` until a Benzinga license is in place.

**Commercial option / cost:** Benzinga enterprise/redistribution license — **custom quote via Benzinga sales** (or via Polygon.io/Massive). No public list price; redistribution rights are a bespoke contract. Confirm exact scope (headlines vs. full text; public vs. authenticated audience) and price in writing before shipping.

**URLs:**
- `https://alpaca.markets/support/redistribute-alpaca-api`
- `https://www.alpacainfo.com/members/eula`
- `https://docs.alpaca.markets/us/docs/streaming-real-time-news`
- `https://www.benzinga.com/apis/licensing/terms`

---

## 6. Channel relay (pyrogram user client) — `telegram_monitor`

**Verdict: UNSAFE.**

**Reasoning.** The relay reads curated public Telegram channels (Walter Bloomberg / SM News / Kobeissi) and re-summarises their posts. This is republication of **another party's editorial curation and content** with no licence, and would also breach those channels' implicit terms. It is appropriate for **personal** monitoring only. Hard-disabled in `PUBLIC_MODE` (see `mag7bot/app.py`).

---

## Already-decided SAFE sources (basis of record)

- **SEC EDGAR** (`edgar`) — Filings and EDGAR data are **U.S. Government works in the public domain (17 U.S.C. §105)**; SEC imposes only a fair-access rate limit / User-Agent identification requirement, not a redistribution restriction. **SAFE** to republish. (Note: republish the *filing facts*; do not imply SEC endorsement.)
- **FRED (St. Louis Fed API)** (`macro`) — The **FRED API Terms of Use** permit use and redistribution of FRED data subject to **attribution** to the source and to FRED/ALFRED; most underlying series are U.S. Government public-domain data. **SAFE** with attribution. Note that a minority of FRED series are copyrighted by third parties (e.g., some proprietary indices) — attribute per FRED's per-series notice.
- **federalreserve.gov RSS** (`fed`) — Content authored by the Federal Reserve Board (a U.S. Government body) is a **public-domain government work**. **SAFE.**
- **Nasdaq trading-halts RSS** (`halts`) — Regulatory trading-halt notices published on a **free public RSS feed** for market-wide dissemination; factual halt data intended for public redistribution. **SAFE** (attribute Nasdaq/UTP as source; do not restyle as official Nasdaq product).
- **Own computed price-moves** (`pricemove`) — Percentages/moves we compute from raw numeric quotes are **original derived output**; individual numeric facts are not copyrightable, and the computation is our own. **SAFE** *subject to* the redistribution terms of the *underlying* quote source used to compute them. ⚠️ The current implementation computes moves from the Yahoo chart endpoint (UNSAFE access). For the public product, repoint `pricemove` at a SAFE/licensed raw-quote feed before relying on this verdict.

---

## Consensus / estimate data for macro alerts (GM-B3-01) — DRAFT, owner sign-off required

The unified macro render (`{actual} vs est {consensus} · prior`) needs a
**consensus estimate**, which government releases do not provide. The candidate
free source is the **ForexFactory** weekly calendar's forecast column.

**Verdict: UNSAFE** for the public/commercial channel — the same basis as the
ForexFactory calendar row above (no official API; ToS bars using the content
without permission; the consensus/forecast column is part of ForexFactory's
protected compilation). Do **not** back the `consensus` field from ForexFactory
for the public product.

**Fallback (implemented, GM-B3-03):** when no licensed consensus is available the
alert renders **actual + prior only** — never a blank slot and never a fabricated
estimate. The macro sources leave `consensus` empty; the formatter degrades
gracefully.

**Licensed options (COGS input for Phase 6), behind the `consensus.py` provider seam (GM-B3-02):**
- **TradingEconomics API** — calendar w/ consensus; redistribution priced into the plan (custom quote). `https://tradingeconomics.com/api/pricing.aspx`
- **Financial Modeling Prep** — economic-calendar w/ estimates from ~$100/mo, **plus** a Data Display & Licensing Agreement for redistribution. `https://site.financialmodelingprep.com/developer/docs/pricing`

**Owner action:** approve keeping consensus **empty** for launch (actual+prior
only), or authorise budget for a licensed calendar provider.

## Open action items before public launch

1. **Capture verbatim primary clauses** (screenshots + date) for Finnhub `/terms`, ForexFactory notices/terms, Yahoo ToS, Alpaca support + EULA, Benzinga licensing terms — the gateway blocked direct retrieval during this review.
2. **Do not publish** Finnhub, ForexFactory-scraped, Google News, Yahoo-endpoint, or Alpaca/Benzinga news content until a written redistribution license (or a SAFE licensed replacement) is in place. (Enforced now by `PUBLIC_MODE`.)
3. **Repoint `pricemove`** at a SAFE/licensed raw-quote feed (its current Yahoo chart source is UNSAFE), then it remains SAFE to publish the computed move.
4. **Pricing for COGS:** obtain written quotes for (a) Finnhub commercial/enterprise, (b) a licensed economic-calendar API (TradingEconomics or FMP + Data Display & Licensing Agreement), and (c) a Benzinga/Polygon news redistribution license.
