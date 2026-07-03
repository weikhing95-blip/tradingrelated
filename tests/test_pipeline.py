"""Unit tests for the Mag 7 News Bot pipeline stages.

These cover the pure, deterministic logic — whitelist, classification, dedup,
materiality, formatting and the source parsers — plus an end-to-end
``build_events`` run against a temporary SQLite DB. No network, no Telegram.
"""

from __future__ import annotations

import json
import time

import pytest

from mag7bot.config import WHITELIST_DOMAINS, Config, load_config
from mag7bot.pipeline import classify, dedup, formatter, materiality, summarize, whitelist
from mag7bot.schemas import EventType, Materiality, RawItem, Tier
from mag7bot.sources import edgar, finnhub, fixtures, google_news, yahoo_news


def _item(headline, ticker="NVDA", publisher="Reuters", url="https://www.reuters.com/x",
          source="finnhub", form_type=None, tier=Tier.WIRE, ts=0.0):
    return RawItem(
        source=source, source_item_id=str(abs(hash(headline)) % 10_000),
        ticker=ticker, tier=tier, headline=headline, url=url, publisher=publisher,
        published_at=ts, form_type=form_type,
    )


# --------------------------------------------------------------------------- #
# whitelist                                                                     #
# --------------------------------------------------------------------------- #


def test_whitelist_keeps_approved_and_drops_unknown():
    keep = _item("x", publisher="Reuters", url="https://www.reuters.com/a")
    drop = _item("y", publisher="RandomBlog", url="https://randomblog.example/z")
    assert whitelist.is_approved(keep, WHITELIST_DOMAINS)
    assert not whitelist.is_approved(drop, WHITELIST_DOMAINS)


def test_whitelist_host_suffix_not_spoofable():
    spoof = _item("z", publisher="", url="https://evil-reuters.com.attacker.net/a")
    assert not whitelist.is_approved(spoof, WHITELIST_DOMAINS)


def test_whitelist_diversified_additions():
    # Vetted additions pass — by URL host and by publisher name (Google News).
    assert whitelist.approved("Barron's", "https://www.barrons.com/articles/x", WHITELIST_DOMAINS)
    assert whitelist.approved("Nikkei Asia", "https://news.google.com/rss/articles/Z", WHITELIST_DOMAINS)
    assert whitelist.approved("Morningstar", "https://www.morningstar.com/news/x", WHITELIST_DOMAINS)
    # Rejected listicle/opinion sources stay out.
    assert not whitelist.approved("The Motley Fool", "https://www.fool.com/investing/x", WHITELIST_DOMAINS)
    assert not whitelist.approved("Benzinga", "https://www.benzinga.com/x", WHITELIST_DOMAINS)
    # nasdaq.com intentionally not whitelisted (syndication noise).
    assert not whitelist.approved("", "https://www.nasdaq.com/articles/x", WHITELIST_DOMAINS)


def test_whitelist_wsj_not_mangled():
    # Regression: a www-prefix strip bug once turned wsj.com into sj.com.
    wsj = _item("z", publisher="", url="https://www.wsj.com/articles/a")
    assert whitelist.is_approved(wsj, WHITELIST_DOMAINS)


def test_whitelist_loaded_from_data_file():
    # WHITELIST_DOMAINS now loads from data/whitelist_domains.txt, lowercased,
    # comments stripped. Sanity-check the load wired up correctly.
    assert len(WHITELIST_DOMAINS) >= 50
    assert "reuters.com" in WHITELIST_DOMAINS
    assert "s&p global" in WHITELIST_DOMAINS
    assert "nasdaq.com" not in WHITELIST_DOMAINS  # deliberately excluded
    assert all(d == d.lower() and not d.startswith("#") for d in WHITELIST_DOMAINS)


# --------------------------------------------------------------------------- #
# classify                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "headline,expected",
    [
        ("NVIDIA to acquire startup in $700M deal", EventType.MA),
        ("Apple faces antitrust probe in Europe", EventType.LEGAL_REGULATORY),
        ("Tesla CFO steps down, names successor", EventType.MANAGEMENT_CHANGE),
        ("Morgan Stanley upgrades NVDA to overweight", EventType.ANALYST),
        ("Microsoft unveils new Surface lineup", EventType.PRODUCT_LAUNCH),
        ("Some generic market chatter", EventType.NEWS),
    ],
)
def test_classify_news_keywords(headline, expected):
    assert classify.classify(_item(headline)) == expected


def test_classify_edgar_forms():
    eightk = _item("Apple files 8-K — Results of Operations", source="edgar",
                   form_type="8-K", tier=Tier.PRIMARY)
    assert classify.classify(eightk) == EventType.EARNINGS
    tenq = _item("Apple files 10-Q", source="edgar", form_type="10-Q", tier=Tier.PRIMARY)
    assert classify.classify(tenq) == EventType.EARNINGS
    form4 = _item("Insider transaction", source="edgar", form_type="4", tier=Tier.PRIMARY)
    assert classify.classify(form4) == EventType.SEC_FILING


def test_classify_word_boundary():
    # "issues" must not match the "sues" legal keyword.
    assert classify.classify(_item("Company issues new guidance")) != EventType.LEGAL_REGULATORY


def _event(materiality, ticker="NVDA", etype=EventType.NEWS):
    from mag7bot.schemas import Event, SentMode
    return Event(ticker=ticker, type=etype, summary="x", links=[], tier=Tier.WIRE,
                 source_name="Reuters", materiality=materiality, confirmed_count=1,
                 sent_mode=SentMode.PENDING, ts=1_700_000_000.0, id=1)


def test_feed_volume_routing(cfg):
    import dataclasses
    from mag7bot import ingest
    from mag7bot.schemas import Materiality

    now = 1_700_000_000.0
    fire = dataclasses.replace(cfg, feed_volume="firehose")
    mod = dataclasses.replace(cfg, feed_volume="moderate")
    low = dataclasses.replace(cfg, feed_volume="low")

    # Firehose pushes even LOW; moderate pushes MATERIAL+; low only CRITICAL.
    assert ingest.should_push_now(fire, _event(Materiality.LOW), now) is True
    assert ingest.should_push_now(mod, _event(Materiality.LOW), now) is False
    assert ingest.should_push_now(mod, _event(Materiality.MATERIAL), now) is True
    assert ingest.should_push_now(low, _event(Materiality.MATERIAL), now) is False
    assert ingest.should_push_now(low, _event(Materiality.CRITICAL), now) is True


def test_24_7_no_quiet_hours(cfg):
    import dataclasses
    from datetime import datetime
    from mag7bot import ingest
    from mag7bot.config import SGT

    three_am_sgt = datetime(2026, 6, 1, 3, 0, tzinfo=SGT).timestamp()
    on = dataclasses.replace(cfg, quiet_hours_enabled=True, quiet_start_hour=0, quiet_end_hour=7)
    off = dataclasses.replace(cfg, quiet_hours_enabled=False)
    assert ingest.in_quiet_hours(on, three_am_sgt) is True
    assert ingest.in_quiet_hours(off, three_am_sgt) is False  # 24/7 default


def test_format_alert_line1_is_ticker_only():
    from mag7bot.schemas import Event, SentMode
    ev = Event(ticker="NVDA", type=EventType.EARNINGS, summary="Q2 beat",
               links=["https://www.reuters.com/a"], tier=Tier.WIRE, source_name="Reuters",
               materiality=Materiality.CRITICAL, confirmed_count=1,
               sent_mode=SentMode.PENDING, ts=0.0)
    msg = formatter.format_alert(ev)
    lines = msg.split("\n")
    assert lines[0] == "$NVDA"        # ticker only — no price reaction
    assert "%" not in lines[0]        # price movement removed entirely
    assert msg.count("$NVDA") == 1    # cashtag appears exactly once


def test_extended_companies_tracked():
    from mag7bot import companies

    # Regression: ALL_COMPANIES must include EXTENDED (MU, PLTR), not just Mag7.
    assert "MU" in companies.ALL_COMPANIES
    assert "PLTR" in companies.ALL_COMPANIES
    assert companies.cik_for("PLTR") == "0001321655"
    assert "micron" in companies.aliases_for("MU")


def test_telegram_monitor_constructor_signature():
    # Regression: app wiring must match this signature (it previously didn't,
    # which would TypeError and crash startup when Telegram creds were set).
    from pathlib import Path

    from mag7bot.telegram_monitor import TelegramChannelMonitor

    m = TelegramChannelMonitor(
        api_id=123, api_hash="abc", session_path=Path("/tmp/x.session"),
        cfg=None, publisher=None, llm_client=None,
    )
    assert m is not None


def test_relevance_drops_mentioned_not_about():
    from mag7bot.pipeline import relevance

    # Real screenshot false-positives: company only mentioned / opinion piece.
    assert not relevance.is_company_specific("Mesh came out of stealth with a $50 million Series A", "GOOGL")
    assert not relevance.is_company_specific("I Correctly Predicted Alphabet Would Join the Dow Jones", "GOOGL")
    assert not relevance.is_company_specific("3 AI stocks to buy before the rally", "NVDA")
    # Genuinely company-specific news passes.
    assert relevance.is_company_specific("Alphabet beats earnings expectations in Q2", "GOOGL")
    assert relevance.is_company_specific("Nvidia to acquire Run:ai for $700M", "NVDA")
    # Alias match (Facebook → META).
    assert relevance.is_company_specific("Facebook parent Meta unveils new AI model", "META")


def test_relevance_applied_in_build_events(cfg):
    from mag7bot import ingest
    from mag7bot.schemas import RawItem, Tier as T

    now = 1_700_000_000.0
    about = RawItem(source="yahoo_news", source_item_id="a1", ticker="GOOGL", tier=T.WIRE,
                    headline="Alphabet reports record cloud revenue", url="https://finance.yahoo.com/a",
                    publisher="Yahoo Finance", published_at=now)
    mentioned = RawItem(source="yahoo_news", source_item_id="a2", ticker="GOOGL", tier=T.WIRE,
                        headline="Mesh came out of stealth with a $50M Series A", url="https://finance.yahoo.com/b",
                        publisher="Yahoo Finance", published_at=now)
    events = ingest.build_events(cfg, [about, mentioned], now)
    summaries = {e.summary for e in events}
    assert any("Alphabet" in s for s in summaries)
    assert not any("Mesh" in s for s in summaries)  # dropped: not about Google


def test_classify_structured_and_exec_commentary():
    from mag7bot.sources import earnings as earnings_src

    # Structured sources carry their type.
    macro_item = _item("US Core CPI (May) +0.3% MoM", ticker="MACRO", source="macro", tier=Tier.PRIMARY)
    assert classify.classify(macro_item) == EventType.MACRO
    earn_item = _item("AAPL Q2 earnings — EPS $1.52", ticker="AAPL", source="earnings")
    assert classify.classify(earn_item) == EventType.EARNINGS
    # Exec commentary vs management change.
    assert classify.classify(_item("Nvidia CEO Jensen Huang said AI demand is strong")) == EventType.EXEC_COMMENTARY
    assert classify.classify(_item("Tesla CEO steps down, names successor")) == EventType.MANAGEMENT_CHANGE


def test_materiality_macro_and_exec():
    assert materiality.score(_item("x", ticker="MACRO", source="macro"), EventType.MACRO) == Materiality.CRITICAL
    assert materiality.score(_item("x"), EventType.EXEC_COMMENTARY) == Materiality.MATERIAL


def test_earnings_parser_beat_miss():
    from mag7bot.sources import earnings as e

    payload = {"earningsCalendar": [
        {"symbol": "AAPL", "year": 2026, "quarter": 2, "epsActual": 1.52,
         "epsEstimate": 1.50, "revenueActual": 94.8e9, "revenueEstimate": 94.5e9},
        {"symbol": "AAPL", "year": 2026, "quarter": 3, "epsActual": None,
         "epsEstimate": 1.60, "revenueActual": None, "revenueEstimate": 96e9},
    ]}
    items = e._parse("AAPL", payload)
    assert len(items) == 1  # only the reported quarter (Q3 has no date → skipped)
    assert items[0].source_item_id == "AAPL-2026Q2"
    assert "EPS $1.52" in items[0].headline and "beat" in items[0].headline
    assert "$94.80B" in items[0].headline


def test_earnings_preview_for_upcoming_report():
    from mag7bot.sources import earnings as e

    payload = {"earningsCalendar": [
        {"symbol": "AAPL", "year": 2026, "quarter": 3, "date": "2026-07-31",
         "hour": "amc", "epsActual": None, "epsEstimate": 1.42,
         "revenueActual": None, "revenueEstimate": 85.9e9},
    ]}
    items = e._parse("AAPL", payload)
    assert len(items) == 1
    it = items[0]
    assert it.source_item_id == "AAPL-2026Q3-preview"  # distinct from the actuals id
    assert it.payload["preview"] is True
    assert "expected" in it.headline and "after close" in it.headline
    assert "EPS $1.42" in it.headline and "$85.90B" in it.headline
    # Classifies as EARNINGS but is MATERIAL (push), not CRITICAL like the print.
    assert classify.classify(it) == EventType.EARNINGS
    assert materiality.score(it, EventType.EARNINGS) == Materiality.MATERIAL


def test_macro_summary_index_and_level():
    from mag7bot.sources import macro as m

    index_obs = [{"date": f"2026-{mm:02d}-01", "value": str(100 + i * 0.3)}
                 for i, mm in enumerate(range(13, 0, -1))]
    line = m._summary({"label": "US Core CPI", "kind": "index"}, index_obs)
    assert "US Core CPI" in line and "MoM" in line and "YoY" in line
    level_obs = [{"date": "2026-06-21", "value": "233000"}, {"date": "2026-06-14", "value": "224000"}]
    lvl = m._summary({"label": "US Initial Jobless Claims", "kind": "level"}, level_obs)
    assert "233,000" in lvl and "WoW" in lvl


def test_format_macro_no_cashtag():
    from mag7bot.schemas import Event, SentMode

    ev = Event(ticker="MACRO", type=EventType.MACRO, summary="US Core CPI (May) — +0.3% MoM, +3.2% YoY",
               links=["https://fred.stlouisfed.org/series/CPILFESL"], tier=Tier.PRIMARY,
               source_name="FRED", materiality=Materiality.CRITICAL, confirmed_count=1,
               sent_mode=SentMode.PENDING, ts=0.0)
    msg = formatter.format_alert(ev)
    # Line 1: MACRO — no $TICKER cashtag, no text type label, no emoji.
    assert msg.split("\n")[0] == "MACRO"
    assert "ECONOMIC DATA" not in msg  # text type label dropped
    assert "$MACRO" not in msg  # no cashtag for economy-wide events
    # Line 2 carries the actual indicator + reading.
    assert "US Core CPI (May)" in msg


def test_classify_listicle_not_ma():
    # "best stocks to buy" listicles must not be misread as M&A.
    assert classify.classify(_item("Why Alphabet is one of the best stocks to buy now")) != EventType.MA
    # Genuine M&A still classifies.
    assert classify.classify(_item("Nvidia to acquire startup Run:ai")) == EventType.MA


# --------------------------------------------------------------------------- #
# dedup                                                                         #
# --------------------------------------------------------------------------- #


def test_dedup_collapses_near_duplicates():
    a = _item("NVIDIA to acquire AI startup Run:ai in $700M deal", url="https://www.reuters.com/a")
    b = _item("Nvidia to acquire AI startup Run:ai in a $700 million deal", url="https://www.bloomberg.com/b")
    groups = dedup.collapse([a, b])
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_dedup_keeps_distinct_stories_apart():
    a = _item("NVIDIA upgraded by Morgan Stanley")
    b = _item("NVIDIA faces antitrust probe in EU")
    groups = dedup.collapse([a, b])
    assert len(groups) == 2


def test_dedup_does_not_cross_tickers():
    a = _item("acquires startup", ticker="NVDA")
    b = _item("acquires startup", ticker="AAPL")
    groups = dedup.collapse([a, b])
    assert len(groups) == 2


def test_dedup_matches_reordered_headline_via_jaccard():
    """Same facts, different word order across outlets — SequenceMatcher's
    sequence ratio dips below threshold, but token-overlap (Jaccard) catches
    it, so the two collapse into one story."""
    a = _item("Nvidia unveils Blackwell GPU architecture at GTC keynote")
    b = _item("At GTC keynote, Nvidia unveils its Blackwell GPU architecture")
    assert dedup.similar(
        dedup.normalize(a.headline, "NVDA"), dedup.normalize(b.headline, "NVDA")
    )


def test_find_existing_matches_on_dedup_key_not_summary():
    """The stored summary is a paraphrase that no longer resembles the headline;
    dedup must match against the event's dedup_key (normalised headline)."""
    from mag7bot.schemas import Event, EventType, Materiality, Tier

    headline = "Nvidia to acquire Run:ai in a deal"
    ev = Event(
        ticker="NVDA",
        type=EventType.MA,
        summary="The chipmaker is buying an Israeli orchestration firm.",  # unlike headline
        tier=Tier.WIRE,
        materiality=Materiality.CRITICAL,
        ts=1_700_000_000.0,
        dedup_key=dedup.normalize(headline, "NVDA"),
    )
    # A reworded re-report of the same story still matches via dedup_key.
    assert dedup.find_existing("NVIDIA to acquire Run:ai in deal", "NVDA", [ev]) is ev


def test_find_existing_falls_back_to_summary_for_premigration_rows():
    """Rows written before the dedup_key column existed have an empty key; we
    fall back to the (weaker) summary comparison so they still dedup."""
    from mag7bot.schemas import Event, EventType, Materiality, Tier

    ev = Event(
        ticker="NVDA",
        type=EventType.MA,
        summary="Nvidia to acquire Run:ai in a deal",
        tier=Tier.WIRE,
        materiality=Materiality.CRITICAL,
        ts=1_700_000_000.0,
        dedup_key="",  # pre-migration
    )
    assert dedup.find_existing("Nvidia to acquire Run:ai in deal", "NVDA", [ev]) is ev


# --------------------------------------------------------------------------- #
# materiality                                                                   #
# --------------------------------------------------------------------------- #


def test_materiality_routing():
    assert materiality.score(_item("x"), EventType.MA) == Materiality.CRITICAL
    assert materiality.score(_item("x"), EventType.EARNINGS) == Materiality.CRITICAL
    assert materiality.score(_item("x"), EventType.LEGAL_REGULATORY) == Materiality.MATERIAL
    # Routine analyst notes → LOW; significant initiations/target moves → MATERIAL.
    assert materiality.score(_item("routine analyst note"), EventType.ANALYST) == Materiality.LOW
    assert materiality.score(_item("initiates coverage on AAPL"), EventType.ANALYST) == Materiality.MATERIAL


def test_materiality_form4_demoted():
    # EDGAR Form 4 raw filings always go to digest.
    f4 = _item("insider sale", source="edgar", form_type="4", tier=Tier.PRIMARY)
    assert materiality.score(f4, EventType.SEC_FILING) == Materiality.LOW


def test_materiality_insider_threshold():
    # Small trade → digest; large trade → instant push.
    small = _item("CEO sold 100 AAPL shares (~$22,800)", source="insider", tier=Tier.PRIMARY)
    small.payload["value_usd"] = 22_800
    assert materiality.score(small, EventType.SEC_FILING) == Materiality.LOW
    large = _item("CEO sold 120,000 AAPL shares (~$27.4M)", source="insider", tier=Tier.PRIMARY)
    large.payload["value_usd"] = 27_400_000
    assert materiality.score(large, EventType.SEC_FILING) == Materiality.MATERIAL


def test_materiality_halt_is_critical():
    halt = _item("Trading halt on NVDA shares")
    assert materiality.score(halt, EventType.NEWS) == Materiality.CRITICAL


def test_unconfirmed_label_logic():
    assert materiality.is_unconfirmed(Tier.WIRE, 1) is True
    assert materiality.is_unconfirmed(Tier.WIRE, 2) is False
    assert materiality.is_unconfirmed(Tier.PRIMARY, 1) is False


# --------------------------------------------------------------------------- #
# summarize                                                                     #
# --------------------------------------------------------------------------- #


def test_summarize_verbatim_truncates():
    long = "x" * 400
    out = summarize.choose_summary(long, mode="verbatim")
    assert len(out) <= summarize.MAX_LEN


def test_summarize_prefers_content_blurb():
    headline = "Microsoft raises prices"
    body = "Microsoft said console storage and memory prices increased by over 2.5x and expects another doubling by 2027."
    out = summarize.choose_summary(headline, body, mode="verbatim")
    assert out.startswith("Microsoft said console storage")  # uses the blurb
    # No blurb → falls back to the headline.
    assert summarize.choose_summary(headline, "", mode="verbatim") == headline
    # Trivial/short blurb is ignored in favour of the headline.
    assert summarize.choose_summary(headline, "ok", mode="verbatim") == headline


def test_summarize_faithfulness_guard():
    # Summary inventing a number absent from the headline is rejected.
    assert not summarize._faithful("Deal worth $999M", "Company makes an acquisition")
    assert summarize._faithful("Company makes acquisition", "Company makes an acquisition deal")


def test_summarize_faithfulness_rejects_fabricated_entity():
    # An invented proper noun (no number involved) must be caught.
    assert not summarize._faithful(
        "Apple announces partnership with OpenAI",
        "Apple declined to comment on AI plans.",
    )
    # Pure verb/wording rephrasing with the same entities is allowed.
    assert summarize._faithful("Nvidia buys Run:ai", "Nvidia to acquire Run:ai startup")
    # Sentence-initial capitalisation is not treated as a fabricated entity.
    assert summarize._faithful("Tesla shares rose", "tesla shares rose on the news")


def test_summarize_faithfulness_allows_inflected_forms():
    """A plural/possessive of a name that IS in the source must not be flagged
    as fabricated — that benign mismatch was silently downgrading good LLM
    summaries to bare headlines."""
    src = "Nvidia introduced its Rubin GPU line at the GTC conference; Apple responded."
    # Plural "GPUs" for source "GPU", possessive "Apple's" for "Apple" → faithful.
    assert summarize._faithful("Nvidia debuted its Rubin GPUs at GTC", src)
    assert summarize._faithful("Apple's response followed the Rubin GPU reveal", src)
    # A genuinely new name is still rejected (stemming must not over-match).
    assert not summarize._faithful("Nvidia debuted Rubin GPUs, pressuring AMD", src)


class _StubAnthropic:
    """Minimal stand-in for the Anthropic client used by summarize.choose_summary."""

    def __init__(self, summary):
        parsed = type("P", (), {"summary": summary})()
        resp = type("R", (), {"parsed_output": parsed})()
        self.messages = type("M", (), {"parse": lambda _self, **kw: resp})()


def test_summarize_llm_falls_back_on_fabrication():
    client = _StubAnthropic("Apple announces partnership with OpenAI.")
    out = summarize.choose_summary(
        "Apple comments on AI", "Apple declined to comment on AI plans.",
        mode="llm", client=client, use_llm=True,
    )
    assert "OpenAI" not in out  # fabrication rejected → safe rich-verbatim used


def test_summarize_llm_used_when_faithful():
    client = _StubAnthropic("Nvidia buys Run:ai.")
    out = summarize.choose_summary(
        "Nvidia to acquire Run:ai", "Nvidia to acquire Run:ai startup in a deal.",
        mode="llm", client=client, use_llm=True,
    )
    assert out == "Nvidia buys Run:ai."


class _CapturingAnthropic:
    """Stub that records the prompt content and returns a fixed summary."""

    def __init__(self, summary):
        self.captured = {}
        parsed = type("P", (), {"summary": summary})()
        resp = type("R", (), {"parsed_output": parsed})()

        def _parse(_self, **kw):
            self.captured.update(kw)
            return resp

        self.messages = type("M", (), {"parse": _parse})()


def test_summarize_subject_anchors_company_name():
    """The known company name (from the ticker) is fed to the model and is
    allowed by the faithfulness guard even when absent from a vague teaser body —
    so summaries name 'Palantir', not 'a major data-analytics company'."""
    client = _CapturingAnthropic("Japan signals support for Palantir's AI initiative.")
    out = summarize.choose_summary(
        "Palantir gets a powerful AI signal",
        "A key U.S. ally signaled support for a major data-analytics company.",
        mode="llm", client=client, use_llm=True, subject="Palantir",
    )
    # Subject is injected into the prompt…
    content = client.captured["messages"][0]["content"]
    assert "Subject company: Palantir" in content
    # …and a summary naming the subject survives the faithfulness guard.
    assert out == "Japan signals support for Palantir's AI initiative."


def test_faithful_allows_subject_company_token():
    # "Palantir" is the known subject (passed in source text) → not a fabrication.
    assert summarize._faithful(
        "Palantir wins a defense AI contract", "Palantir A key ally backs the initiative."
    )


def test_whitelist_publisher_whole_word_not_substring():
    # Whole-word publisher match: a spoof label on a non-whitelisted domain is
    # rejected, but a legitimate multi-word publisher is kept.
    spoof = _item("x", publisher="Reutersclone Daily", url="https://spam.example/x")
    assert not whitelist.is_approved(spoof, WHITELIST_DOMAINS)
    legit = _item("y", publisher="Thomson Reuters", url="https://spam.example/y")
    assert whitelist.is_approved(legit, WHITELIST_DOMAINS)


def test_classify_whole_word_keywords():
    # "profitability" must NOT classify as EARNINGS (was a left-boundary leak).
    assert classify.classify(_item("Apple profitability concerns weigh on shares")) != EventType.EARNINGS
    # A genuine earnings headline still classifies.
    assert classify.classify(_item("Apple beats estimates on record revenue")) == EventType.EARNINGS


def test_relay_helpers_and_offtopic():
    from mag7bot import telegram_monitor as tm

    assert tm._find_ticker("$NVDA breaking out", ["NVDA"]) == "NVDA"
    assert tm._find_ticker("Nvidia ships new chip", ["NVDA"]) == "NVDA"
    assert tm._has_cashtag("$NVDA up 3%", ["NVDA"]) is True
    assert tm._has_cashtag("Nvidia up 3%", ["NVDA"]) is False
    # Off-topic detector fires on clearly non-market topics.
    assert tm._OFF_TOPIC.search("Massive earthquake hits region") is not None
    assert tm._OFF_TOPIC.search("Nvidia reports record revenue") is None


# --------------------------------------------------------------------------- #
# formatter                                                                     #
# --------------------------------------------------------------------------- #


def test_format_alert_spec():
    from mag7bot.schemas import Event, SentMode

    ev = Event(
        ticker="NVDA", type=EventType.MA, summary="NVIDIA to acquire Run:ai",
        links=["https://www.reuters.com/a", "https://www.bloomberg.com/b"],
        tier=Tier.WIRE, source_name="Reuters", materiality=Materiality.CRITICAL,
        confirmed_count=2, unconfirmed=False, sent_mode=SentMode.PENDING, ts=0.0,
    )
    msg = formatter.format_alert(ev)
    lines = msg.split("\n")
    # Line 1: just $TICKER — no type label, no emoji, no "Tier N —" label.
    assert lines[0] == "$NVDA"
    assert "🔴" not in lines[0]  # category emoji removed from line 1
    assert "Tier 2" not in msg  # users never see the tier label anymore
    # Line 2: bite-size summary on its own line (no cashtag duplication).
    assert lines[1] == "NVIDIA to acquire Run:ai"
    # Line 3: source + status + timestamp condensed onto one row.
    assert "cross-confirmed (2)" in lines[2]
    assert "🕒" in lines[2]
    # Links are still hyperlinked, now behind "link 1 · link 2" (no publisher).
    assert "Reuters" not in msg  # source/publisher label dropped from line 3
    assert 'href="https://www.reuters.com/a"' in msg
    assert 'href="https://www.bloomberg.com/b"' in msg
    assert "🔗 https://" not in msg  # no raw URL dumped inline


def test_format_alert_single_link_says_link():
    from mag7bot.schemas import Event, SentMode

    ev = Event(
        ticker="AAPL", type=EventType.NEWS, summary="Apple news",
        links=["https://news.google.com/rss/articles/CBMiAAA?oc=5"],
        tier=Tier.WIRE, source_name="Yahoo Finance", materiality=Materiality.LOW,
        confirmed_count=1, unconfirmed=True, sent_mode=SentMode.PENDING, ts=0.0,
    )
    msg = formatter.format_alert(ev)
    # Line 3 is just the clean "link" label (no publisher) — the raw domain
    # only ever appears inside the href attribute.
    assert "🔗 " in msg
    assert "Yahoo Finance" not in msg  # source/publisher label dropped
    assert ">link</a>" in msg
    assert "news.google.com" not in msg.replace('href="https://news.google.com/rss/articles/CBMiAAA?oc=5"', "")


def test_format_digest_lists_empty_tickers():
    text = formatter.format_digest([], ["AAPL", "NVDA"], now=0.0)
    # MarketBrief header + per-ticker "no new events" notes.
    assert text.startswith("📰 MarketBrief Daily")
    assert "📈 BY COMPANY" in text
    assert "$AAPL  — no new events" in text
    assert "$NVDA  — no new events" in text
    # Always-present footer with the update count.
    assert "MarketBrief · 0 updates today" in text


# --------------------------------------------------------------------------- #
# source parsers                                                                #
# --------------------------------------------------------------------------- #


def test_edgar_parser_keeps_only_interesting_forms():
    items = edgar._parse("AAPL", "0000320193", fixtures.EDGAR_AAPL_SUBMISSIONS)
    forms = {i.form_type for i in items}
    assert forms == {"8-K", "10-Q", "4"}  # SC 13G dropped
    assert all(i.url.startswith("https://www.sec.gov/Archives/edgar/data/320193/") for i in items)


_GNEWS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>NVIDIA stock - Google News</title>
<item>
  <title>Nvidia hits record high on AI demand - Reuters</title>
  <link>https://news.google.com/rss/articles/CBMiAAA?oc=5</link>
  <guid isPermaLink="false">CBMiAAA</guid>
  <pubDate>Wed, 25 Jun 2025 12:00:00 GMT</pubDate>
  <source url="https://www.reuters.com">Reuters</source>
</item>
<item>
  <title>Random rumor about Nvidia - RandomBlog</title>
  <link>https://news.google.com/rss/articles/ZZZ?oc=5</link>
  <guid isPermaLink="false">ZZZ</guid>
  <pubDate>Wed, 25 Jun 2025 11:00:00 GMT</pubDate>
  <source url="https://randomblog.example">RandomBlog</source>
</item>
</channel></rss>"""


def test_google_news_parser_and_whitelist():
    items = google_news.parse_rss(_GNEWS_RSS, "NVDA")
    assert len(items) == 2
    reuters = items[0]
    # " - Reuters" suffix stripped from the headline.
    assert reuters.headline == "Nvidia hits record high on AI demand"
    assert reuters.publisher == "Reuters"
    assert reuters.ticker == "NVDA"
    assert reuters.source == "google_news"
    # Whitelist keeps Reuters, drops the random blog (by publisher name).
    assert whitelist.is_approved(items[0], WHITELIST_DOMAINS)
    assert not whitelist.is_approved(items[1], WHITELIST_DOMAINS)


def test_google_news_query_overrides():
    assert "Alphabet" in google_news._query("GOOGL")
    assert google_news._query("NVDA") == "NVIDIA stock"


def test_gnews_article_id_extraction():
    assert google_news._gnews_article_id(
        "https://news.google.com/rss/articles/CBMiABC123?oc=5"
    ) == "CBMiABC123"
    assert google_news._gnews_article_id(
        "https://news.google.com/articles/XYZ789"
    ) == "XYZ789"
    # A non-article Google URL (or a real publisher URL) yields no id.
    assert google_news._gnews_article_id("https://www.reuters.com/tech/x") == ""


def test_gnews_decoding_params_parse():
    html = '<c-wiz><div data-n-a-sig="SIG_ABC" data-n-a-ts="1700000000">x</div></c-wiz>'
    sig, ts = google_news._parse_decoding_params(html)
    assert sig == "SIG_ABC" and ts == "1700000000"
    # Missing attributes → empty (decoder then bails, falls back to the link).
    assert google_news._parse_decoding_params("<html>no params</html>") == ("", "")


def test_gnews_batch_payload_shape():
    payload = google_news._build_batch_payload("CBMiABC", "SIG", "1700000000")
    assert "f.req" in payload
    outer = json.loads(payload["f.req"])
    # [[["Fbv4je", "<inner-json>", null, "generic"]]]
    rpc = outer[0][0]
    assert rpc[0] == "Fbv4je" and rpc[3] == "generic"
    inner = json.loads(rpc[1])
    assert inner[0] == "garturlreq"
    assert inner[-3] == "CBMiABC" and inner[-2] == 1700000000 and inner[-1] == "SIG"


def test_gnews_batch_response_extracts_url():
    # Mimic Google's anti-JSON-prefixed, chunked batchexecute body.
    real = "https://www.reuters.com/technology/nvidia-record-2026-06-29/"
    body = (
        ")]}'\n\n"
        '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"' + real + '\\"]",null,null,null,"generic"]]'
    )
    assert google_news._parse_batch_response(body) == real
    # Garbage / scheme change → "" (caller falls back, never crashes).
    assert google_news._parse_batch_response(")]}'\n\nnot json at all") == ""
    assert google_news._parse_batch_response("") == ""


_YAHOO_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Yahoo! Finance: AAPL News</title>
<item>
  <title>Apple unveils new iPhone lineup</title>
  <link>https://finance.yahoo.com/news/apple-unveils-iphone-123456.html</link>
  <guid>https://finance.yahoo.com/news/apple-unveils-iphone-123456.html</guid>
  <pubDate>Wed, 25 Jun 2025 09:30:00 GMT</pubDate>
  <description>Apple announced ...</description>
</item>
</channel></rss>"""


def test_yahoo_news_parser():
    items = yahoo_news.parse_rss(_YAHOO_RSS, "AAPL")
    assert len(items) == 1
    it = items[0]
    assert it.source == "yahoo_news"
    assert it.ticker == "AAPL"
    assert it.headline == "Apple unveils new iPhone lineup"
    assert "finance.yahoo.com" in it.publisher
    # Yahoo Finance is whitelisted by default.
    assert whitelist.is_approved(it, WHITELIST_DOMAINS)


def test_effective_whitelist_includes_db_extras(cfg):
    from mag7bot import db, ingest

    base = set(ingest.effective_whitelist(cfg))
    assert "exampletimes.com" not in base
    db.add_whitelist_domain(cfg.db_path, "exampletimes.com", "Example Times", "tier2")
    after = ingest.effective_whitelist(cfg)
    assert "exampletimes.com" in after
    # And it now passes the whitelist for an item from that publisher.
    item = _item("x", publisher="Example Times", url="https://www.exampletimes.com/articles/a")
    assert whitelist.approved(item.publisher, item.url, after)
    assert db.remove_whitelist_domain(cfg.db_path, "exampletimes.com")
    assert "exampletimes.com" not in ingest.effective_whitelist(cfg)


def test_finnhub_parser_shape():
    items = finnhub._parse("NVDA", fixtures.FINNHUB_NVDA)
    assert len(items) == 4
    assert all(i.source == "finnhub" and i.url and i.headline for i in items)


# --------------------------------------------------------------------------- #
# end-to-end build_events                                                       #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    c = load_config(dry_run=True)
    from mag7bot import seed
    seed.seed(c)
    return c


def test_build_events_end_to_end(cfg):
    from mag7bot import ingest

    now = 1_700_000_000.0  # fixed, deterministic
    events = ingest.build_events(cfg, fixtures.sample_raw_items(now), now)
    # 6 raw → 1 dropped (whitelist) → Run:ai pair collapses → 4 events.
    assert len(events) == 4
    by_type = {e.type for e in events}
    assert EventType.MA in by_type and EventType.EARNINGS in by_type
    ma = next(e for e in events if e.type == EventType.MA)
    assert ma.confirmed_count == 2 and len(ma.links) == 2


class _FakeSource:
    """A Source that returns canned items, for testing prime()."""

    name = "finnhub"

    def __init__(self, items):
        self._items = items

    async def fetch_new(self, tickers, is_seen):
        return [i for i in self._items if not is_seen(i.source, i.source_item_id)]


def test_prime_marks_seen_without_alerting(cfg):
    import asyncio

    from mag7bot import db, ingest

    items = [i for i in fixtures.sample_raw_items(1_700_000_000.0) if i.source == "finnhub"]
    src = _FakeSource(items)

    assert db.seen_count(cfg.db_path) == 0
    primed = asyncio.run(ingest.prime(cfg, [src]))
    assert primed == len(items)
    assert db.seen_count(cfg.db_path) == len(items)
    # No events were created by priming.
    assert db.recent_events(cfg.db_path, cfg.feed_id, "NVDA", 0) == []
    # A second prime finds nothing new (idempotent).
    assert asyncio.run(ingest.prime(cfg, [_FakeSource(items)])) == 0


def test_build_events_drops_stale_news_keeps_fresh(cfg):
    from mag7bot import ingest

    now = 1_700_000_000.0
    fresh = _item("Nvidia to acquire Run:ai", url="https://www.reuters.com/a", ts=now - 3600)
    stale = _item("Nvidia old listicle news", url="https://www.cnbc.com/b", ts=now - 10 * 86400)
    events = ingest.build_events(cfg, [fresh, stale], now)
    summaries = {e.summary for e in events}
    assert "Nvidia to acquire Run:ai" in summaries
    assert "Nvidia old listicle news" not in summaries  # too old → dropped


def test_build_events_drops_news_within_old_default_window(cfg):
    """A 36h-old news item used to pass the 48h window; with the tighter 24h
    default it's now dropped — only the latest news reaches the channel."""
    from mag7bot import ingest

    now = 1_700_000_000.0
    item = _item("Nvidia yesterday-plus news", url="https://www.reuters.com/c", ts=now - 36 * 3600)
    events = ingest.build_events(cfg, [item], now)
    assert events == []


def test_build_events_drops_news_with_no_timestamp(cfg):
    """News with no usable publish date can't be proven fresh → dropped
    (aggregators sometimes omit the date on resurfaced old articles)."""
    from mag7bot import ingest

    now = 1_700_000_000.0
    item = _item("Nvidia undated aggregator item", url="https://www.reuters.com/d", ts=0.0)
    events = ingest.build_events(cfg, [item], now)
    assert events == []


def test_build_events_keeps_stale_tier1_filing(cfg):
    from mag7bot import ingest

    now = 1_700_000_000.0
    old_filing = _item(
        "Apple files 8-K", ticker="AAPL", source="edgar", form_type="8-K",
        tier=Tier.PRIMARY, url="https://www.sec.gov/x", ts=now - 10 * 86400,
    )
    events = ingest.build_events(cfg, [old_filing], now)
    assert len(events) == 1  # Tier-1 filings are exempt from the recency guard


def test_build_events_cross_confirm_updates_not_duplicates(cfg):
    from mag7bot import db, ingest

    now = 1_700_000_000.0
    first = _item("NVIDIA to acquire Run:ai in deal", url="https://www.reuters.com/a", ts=now)
    ingest.build_events(cfg, [first], now)
    # Same story from another wire shortly after → updates, no new event.
    second = _item("Nvidia to acquire Run:ai in a deal", url="https://www.bloomberg.com/b", ts=now + 60)
    new = ingest.build_events(cfg, [second], now + 60)
    assert new == []  # collapsed into the existing event
    recent = db.recent_events(cfg.db_path, cfg.feed_id, "NVDA", now - 3600)
    assert len(recent) == 1
    assert recent[0].confirmed_count == 2
    assert len(recent[0].links) == 2
    # The normalised headline is persisted so later cycles can dedup against it.
    assert recent[0].dedup_key and "acquire" in recent[0].dedup_key


def test_build_events_same_article_across_tickers_posts_once(cfg):
    """A multi-company article (same URL surfaced for two tickers) must produce
    a single alert, not one per ticker (the Micron-vs-Nvidia fool.com case)."""
    from mag7bot import db, ingest

    now = 1_700_000_000.0
    url = "https://www.fool.com/investing/micron-vs-nvidia-ai"
    mu = _item("Micron stock compared to Nvidia in the AI boom", ticker="MU", url=url, ts=now)
    nvda = _item("Nvidia is a major AI winner; article weighs Micron", ticker="NVDA", url=url, ts=now)

    events = ingest.build_events(cfg, [mu, nvda], now)
    assert len(events) == 1  # collapsed by URL across tickers

    all_recent = db.recent_events_window(cfg.db_path, cfg.feed_id, now - 3600)
    assert len(all_recent) == 1


def test_canon_url_ignores_scheme_www_and_query():
    from mag7bot.ingest import _canon_url

    a = _canon_url("https://www.fool.com/x/article?utm=tw#top")
    b = _canon_url("http://fool.com/x/article/")
    assert a == b == "fool.com/x/article"
    assert _canon_url("") == ""  # blank never matches


# --------------------------------------------------------------------------- #
# Observability: source health + run_cycle isolation/alerting                   #
# --------------------------------------------------------------------------- #


class _NamedSource:
    """Minimal named Source for run_cycle isolation/alerting tests."""

    def __init__(self, name, items=None, boom=False):
        self.name = name
        self.tier = Tier.WIRE
        self._items = items or []
        self._boom = boom
        self.fetch_calls = 0

    async def fetch_new(self, tickers, is_seen):
        self.fetch_calls += 1
        if self._boom:
            raise RuntimeError("api down")
        return self._items


class _CapturePublisher:
    def __init__(self):
        self.pushed = []

    async def push(self, event):
        self.pushed.append(event)


def test_source_health_error_then_recovery(cfg):
    from mag7bot import db

    now = 1_700_000_000.0
    assert db.record_source_error(cfg.db_path, "finnhub", "boom", now) == 1
    assert db.record_source_error(cfg.db_path, "finnhub", "boom again", now + 1) == 2
    # A successful fetch returns the prior error count (2) and resets it to 0.
    assert db.record_source_ok(cfg.db_path, "finnhub", 5, now + 2) == 2
    assert db.record_source_ok(cfg.db_path, "finnhub", 3, now + 3) == 0  # already healthy
    health = {h["source"]: h for h in db.get_source_health(cfg.db_path)}
    assert health["finnhub"]["consecutive_errors"] == 0
    assert health["finnhub"]["last_count"] == 3


def test_run_cycle_isolates_failing_source_and_alerts(cfg):
    import asyncio

    from mag7bot import db, ingest

    now = 1_700_000_000.0
    good = _NamedSource("finnhub", items=[_item("Nvidia ships new GPU", url="https://www.reuters.com/g", ts=now)])
    bad = _NamedSource("yahoo_news", boom=True)
    alerts = []

    async def alert(msg):
        alerts.append(msg)

    pub = _CapturePublisher()
    events = asyncio.run(ingest.run_cycle(cfg, [good, bad], pub, now, None, alert=alert))

    # The good source still produced + pushed an event despite the bad one failing.
    assert any(e.ticker == "NVDA" for e in events)
    assert pub.pushed
    # The failing source was isolated and recorded, but a *single* blip stays
    # silent — the owner is only alerted on a sustained outage (see threshold test).
    health = {h["source"]: h for h in db.get_source_health(cfg.db_path)}
    assert health["yahoo_news"]["consecutive_errors"] == 1
    assert health["finnhub"]["consecutive_errors"] == 0
    assert sum("failing" in m for m in alerts) == 0


def test_public_mode_excludes_unsafe_sources(cfg):
    import asyncio
    import dataclasses

    from mag7bot import ingest
    from mag7bot.config import is_commercial_safe

    # Sanity: the classification table matches the launch spec.
    assert is_commercial_safe("edgar") and is_commercial_safe("macro")
    assert is_commercial_safe("halts") and is_commercial_safe("pricemove")
    assert not is_commercial_safe("google_news")
    assert not is_commercial_safe("yahoo_news")
    assert not is_commercial_safe("alpaca")
    assert not is_commercial_safe("finnhub")
    assert not is_commercial_safe("unknown_future_source")  # fail-safe default

    now = 1_700_000_000.0
    safe = _NamedSource("edgar", items=[
        _item("Nvidia files 8-K", url="https://www.sec.gov/x", ts=now)])
    unsafe = _NamedSource("google_news", items=[
        _item("Nvidia rumor roundup", url="https://news.google.com/x", ts=now)])
    pub = _CapturePublisher()

    public_cfg = dataclasses.replace(cfg, public_mode=True)
    events = asyncio.run(ingest.run_cycle(public_cfg, [safe, unsafe], pub, now, None))

    # The unsafe source is never even fetched in public mode; the safe one runs.
    assert unsafe.fetch_calls == 0
    assert safe.fetch_calls == 1
    assert all(e.source_name != "google_news" for e in events)

    # Outside public mode, both are fetched (no legal gate).
    safe2 = _NamedSource("edgar")
    unsafe2 = _NamedSource("google_news")
    asyncio.run(ingest.run_cycle(cfg, [safe2, unsafe2], pub, now + 1, None))
    assert unsafe2.fetch_calls == 1 and safe2.fetch_calls == 1


def test_source_alert_only_on_sustained_outage_then_recovery(cfg):
    import asyncio

    from mag7bot import ingest
    from mag7bot.config import SOURCE_ALERT_THRESHOLD

    now = 1_700_000_000.0
    alerts = []

    async def alert(msg):
        alerts.append(msg)

    pub = _CapturePublisher()
    bad = _NamedSource("yahoo_news", boom=True)

    # Blips below the threshold: no alert yet.
    for i in range(SOURCE_ALERT_THRESHOLD - 1):
        asyncio.run(ingest.run_cycle(cfg, [bad], pub, now + i, None, alert=alert))
    assert sum("failing" in m for m in alerts) == 0

    # Crossing the threshold alerts exactly once (not again on further failures).
    asyncio.run(ingest.run_cycle(cfg, [bad], pub, now + 10, None, alert=alert))
    asyncio.run(ingest.run_cycle(cfg, [bad], pub, now + 11, None, alert=alert))
    assert sum("failing" in m for m in alerts) == 1

    # Recovery is announced once, since the outage was announced.
    good = _NamedSource("yahoo_news", items=[
        _item("Nvidia ships new GPU", url="https://www.reuters.com/g", ts=now)])
    asyncio.run(ingest.run_cycle(cfg, [good], pub, now + 20, None, alert=alert))
    assert sum("recovered" in m for m in alerts) == 1


# --------------------------------------------------------------------------- #
# Track 4: trading halts + analyst ratings sources                              #
# --------------------------------------------------------------------------- #

_HALTS_RSS = """<?xml version="1.0"?>
<rss xmlns:ndaq="http://www.nasdaqtrader.com/" version="2.0"><channel>
<item>
  <ndaq:IssueSymbol>NVDA</ndaq:IssueSymbol>
  <ndaq:ReasonCode>LUDP</ndaq:ReasonCode>
  <ndaq:HaltDate>06/29/2026</ndaq:HaltDate>
  <ndaq:HaltTime>09:31:00</ndaq:HaltTime>
</item>
<item>
  <ndaq:IssueSymbol>ZZZZ</ndaq:IssueSymbol>
  <ndaq:ReasonCode>T1</ndaq:ReasonCode>
</item>
</channel></rss>"""


def test_halts_parser_filters_watchlist_and_is_critical():
    from mag7bot.sources.halts import _parse_halts

    items = _parse_halts(_HALTS_RSS, ["NVDA", "AAPL"])
    assert len(items) == 1  # ZZZZ is off-watchlist
    it = items[0]
    assert it.ticker == "NVDA" and it.source == "halts" and it.tier == Tier.PRIMARY
    assert "halted" in it.headline.lower()
    assert classify.classify(it) == EventType.TRADING_HALT
    assert materiality.score(it, EventType.TRADING_HALT) == Materiality.CRITICAL


def test_ratings_parser_changes_only_and_material():
    from mag7bot.sources.ratings import _parse

    rows = [
        {"symbol": "NVDA", "company": "Morgan Stanley", "fromGrade": "Equal-Weight",
         "toGrade": "Overweight", "action": "up", "gradeTime": 1_700_000_000},
        {"symbol": "NVDA", "company": "Citi", "toGrade": "Buy",
         "action": "main", "gradeTime": 1_700_000_001},  # reiteration → skipped
        {"symbol": "NVDA", "company": "BofA", "fromGrade": "Buy",
         "toGrade": "Neutral", "action": "down", "gradeTime": 1_700_000_002},
    ]
    items = _parse("NVDA", rows)
    assert len(items) == 2  # "main" dropped
    up = items[0]
    assert "upgrades NVDA to Overweight" in up.headline
    assert classify.classify(up) == EventType.ANALYST
    # A genuine rating change promotes ANALYST from LOW to MATERIAL.
    assert materiality.score(up, EventType.ANALYST) == Materiality.MATERIAL


# --------------------------------------------------------------------------- #
# Price-move detector                                                           #
# --------------------------------------------------------------------------- #

# A daily-close series (oldest→newest); the LAST value is "today".
# Calm ~1%/day history then a +6% jump today → fires.
_CALM_HISTORY = [100, 101, 100, 102, 101, 103, 102, 104, 103, 105, 104]


def test_pricemove_fires_above_multiple_and_floor():
    from mag7bot.sources import pricemove as pm

    series = _CALM_HISTORY + [104 * 1.062]  # +6.2% today on a ~1.4% baseline
    verdict = pm.assess_move(series, multiplier=2.0, min_pct=3.0, lookback=10)
    assert verdict is not None
    move, baseline, ratio = verdict
    assert round(move * 100, 1) == 6.2 and ratio > 2.0


def test_pricemove_respects_floor():
    from mag7bot.sources import pricemove as pm

    series = _CALM_HISTORY + [104 * 1.02]  # +2% today — below the 3% floor
    assert pm.assess_move(series, multiplier=2.0, min_pct=3.0, lookback=10) is None


def test_pricemove_respects_multiplier_independently():
    from mag7bot.sources import pricemove as pm

    # Volatile name (~5%/day baseline); +4% today clears the 3% floor but is below
    # 2× the baseline (~10%), so the multiplier gate (not the floor) rejects it.
    volatile = [100, 105, 100, 105, 100, 105, 100, 105, 100, 105, 100]
    series = volatile + [100 * 1.04]
    assert pm.assess_move(series, multiplier=2.0, min_pct=3.0, lookback=10) is None


def test_pricemove_uses_yesterday_not_month_ago():
    """Regression: the move must be vs *yesterday's* close, not a month-ago
    reference. A series that ran up ~29% over the window but only +1.75% on the
    last day must NOT fire (the old chartPreviousClose fallback reported the whole
    monthly move as a 'daily' move, flagging every ticker at once)."""
    from mag7bot.sources import pricemove as pm

    # 90 → 116 over the window (+28.9% total) but 114 → 116 on the last day (+1.75%).
    series = [90, 92, 94, 96, 98, 100, 104, 108, 110, 113, 114, 116]
    verdict = pm.assess_move(series, multiplier=2.0, min_pct=3.0, lookback=10)
    assert verdict is None  # +1.75% day move is below the floor — no false alert


def test_pricemove_sanity_cap_rejects_absurd_move():
    from mag7bot.sources import pricemove as pm

    # A 60% one-day jump (e.g. a split artifact) exceeds the plausibility cap.
    series = _CALM_HISTORY + [104 * 1.60]
    assert pm.assess_move(series, multiplier=2.0, min_pct=3.0, lookback=10) is None


def test_pricemove_needs_enough_history():
    from mag7bot.sources import pricemove as pm

    assert pm.assess_move([100, 120], multiplier=2.0, min_pct=3.0, lookback=10) is None
    assert pm.assess_move([], multiplier=2.0, min_pct=3.0, lookback=10) is None


def test_pricemove_parse_chart_and_classify():
    from mag7bot.pipeline import classify, materiality
    from mag7bot.sources import pricemove as pm

    payload = {
        "chart": {"result": [{
            "meta": {"regularMarketPrice": 110.4, "regularMarketTime": 1_700_000_000},
            "indicators": {"quote": [{"close": _CALM_HISTORY + [110.4]}]},
        }], "error": None}
    }
    closes, current, as_of = pm.parse_chart(payload)
    assert current == 110.4 and len(closes) == 12

    item = pm.build_item("NVDA", 0.062, 110.40, as_of)
    assert item.source == "pricemove" and item.ticker == "NVDA"
    # Short + precise: move % and last price only — no baseline-ratio noise.
    assert "up 6.2%" in item.headline and "last $110.40" in item.headline
    assert "2-week" not in item.headline and "×" not in item.headline
    # Structured signal: routes as PRICE_MOVE → MATERIAL (push, not quiet-override).
    assert classify.classify(item) == EventType.PRICE_MOVE
    assert materiality.score(item, EventType.PRICE_MOVE) == Materiality.MATERIAL


def test_pricemove_parse_chart_handles_bad_payload():
    from mag7bot.sources import pricemove as pm

    assert pm.parse_chart({}) == ([], None, 0.0)
    assert pm.parse_chart({"chart": {"result": []}}) == ([], None, 0.0)


def test_pricemove_event_bypasses_news_filters(cfg):
    """A price-move item must reach the channel as a structured event — it is not
    a news article, so the whitelist/relevance gates (and the "X Moves %" filler
    filter) must not touch it."""
    from mag7bot import db, ingest
    from mag7bot.sources import pricemove as pm

    now = 1_700_000_000.0
    item = pm.build_item("NVDA", 0.062, 110.40, now)
    events = ingest.build_events(cfg, [item], now)
    assert len(events) == 1
    ev = events[0]
    assert ev.type == EventType.PRICE_MOVE and ev.ticker == "NVDA"
    assert "6.2%" in ev.summary and "last $110.40" in ev.summary
    assert ev.materiality == Materiality.MATERIAL
    assert db.recent_events(cfg.db_path, cfg.feed_id, "NVDA", now - 3600)


# --------------------------------------------------------------------------- #
# Alpaca (Benzinga) news                                                        #
# --------------------------------------------------------------------------- #

_ALPACA_PAYLOAD = {
    "news": [
        {
            "id": 123,
            "headline": "Palantir teams with Nvidia to deploy Nemotron models",
            "summary": "PLTR and NVDA partner on sovereign AI.",
            "content": (
                "<p>Palantir entered a strategic initiative with Nvidia to deploy "
                "AI and Nemotron open models in sovereign environments.</p>"
                "<p>The offering targets U.S. government agencies and critical "
                "infrastructure operators, including classified deployments.</p>"
            ),
            "created_at": "2026-06-29T11:30:00Z",
            "url": "https://www.benzinga.com/news/123/palantir-nvidia",
            "symbols": ["PLTR", "NVDA", "SPY"],
            "source": "benzinga",
        },
        {
            "id": 124,
            "headline": "7 AI Stocks To Buy Right Now",  # listicle filler → dropped
            "content": "<p>Here are some stocks.</p>",
            "created_at": "2026-06-29T10:00:00Z",
            "url": "https://www.benzinga.com/news/124",
            "symbols": ["NVDA"],
        },
    ]
}


def test_alpaca_parse_expands_symbols_and_filters():
    from mag7bot.sources.alpaca import _parse

    items = _parse(_ALPACA_PAYLOAD, ["NVDA", "PLTR"])
    # Story 123 → one item each for PLTR and NVDA (SPY off-watchlist); story 124
    # is a listicle → dropped by the low-quality filter.
    assert len(items) == 2
    assert {it.ticker for it in items} == {"NVDA", "PLTR"}
    assert {it.source_item_id for it in items} == {"123-NVDA", "123-PLTR"}
    for it in items:
        assert it.source == "alpaca" and it.tier == Tier.WIRE
        assert it.url == "https://www.benzinga.com/news/123/palantir-nvidia"
        assert it.publisher == "benzinga"
        assert it.published_at > 0


def test_alpaca_parse_uses_cleaned_content_as_body():
    from mag7bot.sources.alpaca import _parse

    it = _parse(_ALPACA_PAYLOAD, ["NVDA"])[0]
    # Full article body (HTML-stripped) carries the substance for the summarizer.
    assert "sovereign environments" in it.body
    assert "<p>" not in it.body  # HTML stripped


def test_alpaca_event_bypasses_news_filters_and_posts(cfg):
    """Alpaca is a trusted structured source: it skips the whitelist + headline-
    relevance gate (Benzinga tags symbols itself) and posts with a body-rich
    summary."""
    from mag7bot import ingest
    from mag7bot.sources.alpaca import _parse

    now = 1_700_000_000.0
    items = [it for it in _parse(_ALPACA_PAYLOAD, ["NVDA"]) if it.ticker == "NVDA"]
    for it in items:
        it.published_at = now  # keep within the freshness window for the test
    events = ingest.build_events(cfg, items, now)
    assert len(events) == 1
    assert events[0].ticker == "NVDA"
    assert events[0].summary  # non-empty, drawn from the Benzinga content/summary


def test_alpaca_to_epoch_parses_iso():
    from mag7bot.sources.alpaca import _to_epoch

    assert _to_epoch("2026-06-29T11:30:00Z") > 0
    assert _to_epoch("") == 0.0
    assert _to_epoch("not-a-date") == 0.0


# --------------------------------------------------------------------------- #
# Relay: macro/Fed fallback                                                     #
# --------------------------------------------------------------------------- #


def test_relay_macro_detector():
    from mag7bot import telegram_monitor as tm

    assert tm._is_macro("Gold slips as Fed rate-hike bets weigh") is True
    assert tm._is_macro("US CPI comes in hotter than expected") is True
    assert tm._is_macro("Powell signals patience on cuts") is True
    # Pure company/product chatter is not macro.
    assert tm._is_macro("Apple unveils a new MacBook") is False
    assert tm._is_macro("A celebrity wedding in Hollywood") is False


def test_macro_tagged_item_classifies_and_scores():
    # A Fed post relayed from Telegram (ticker tagged MACRO) classifies as MACRO
    # and is MATERIAL (commentary), not CRITICAL like an official release.
    it = RawItem(
        source="telegram", source_item_id="walterbloomberg-1", ticker="MACRO",
        tier=Tier.WIRE, headline="Fed rate-hike bets weigh on gold",
        url="https://t.me/WalterBloomberg/1", publisher="@WalterBloomberg",
        published_at=1_700_000_000.0,
    )
    assert classify.classify(it) == EventType.MACRO
    assert materiality.score(it, EventType.MACRO) == Materiality.MATERIAL


# --------------------------------------------------------------------------- #
# Article fetch + extraction (richer LLM summaries)                             #
# --------------------------------------------------------------------------- #

_ARTICLE_HTML = """
<html><head>
  <meta property="og:description" content="Short blurb that is the fallback.">
</head><body>
  <nav><p>Home About Subscribe Sign in</p></nav>
  <script>var x = "ignore me entirely";</script>
  <article>
    <h1>Susquehanna hikes Micron target to $2,000 after record-breaking earnings</h1>
    <p>Susquehanna has raised its price target on Micron to $2,000 from $1,750 after
       the memory chipmaker posted record-breaking fiscal third-quarter results.</p>
    <p>Micron reported revenue of $41.46 billion and adjusted EPS of $25.11, well
       above consensus of $35.91 billion and $20.86.</p>
  </article>
  <footer><p>Copyright 2026 — all rights reserved, terms and conditions apply.</p></footer>
</body></html>
"""


def test_article_extract_text_pulls_body_skips_boilerplate():
    from mag7bot import article

    text = article.extract_text(_ARTICLE_HTML)
    assert "$2,000 from $1,750" in text
    assert "$41.46 billion" in text and "$25.11" in text
    assert "ignore me entirely" not in text   # <script> skipped
    assert "Subscribe" not in text             # <nav> skipped


def test_article_extract_meta_fallback_when_thin():
    from mag7bot import article

    thin = '<html><head><meta name="description" content="' + "x" * 120 + '"></head><body></body></html>'
    out = article.extract_text(thin)
    assert out.startswith("x" * 80)  # falls back to meta description


def test_enrich_article_bodies_replaces_blurb(cfg, monkeypatch):
    import asyncio
    from mag7bot import article, ingest

    async def fake_fetch(url, timeout=8.0, max_chars=4000):
        return "FULL ARTICLE TEXT " * 20  # long, richer than the blurb

    monkeypatch.setattr(article, "fetch_article_text", fake_fetch)

    news = _item("Micron news", source="finnhub", url="https://www.reuters.com/x", ts=1_700_000_000.0)
    news.body = "tiny blurb"
    structured = _item("Apple files 8-K", source="edgar", form_type="8-K",
                       tier=Tier.PRIMARY, url="https://www.sec.gov/x")
    asyncio.run(ingest.enrich_article_bodies(cfg, [news, structured]))
    assert news.body.startswith("FULL ARTICLE TEXT")  # news enriched
    assert structured.body == ""                        # structured untouched


def test_boilerplate_blurb_falls_back_to_headline():
    from mag7bot import article

    google_junk = ("Comprehensive up-to-date news coverage, aggregated from "
                   "sources all over the world by Google News.")
    assert article.is_boilerplate(google_junk) is True
    assert article.is_boilerplate("Micron raised its target to $2,000") is False
    # Verbatim: a boilerplate body is ignored → the headline is used instead.
    out = summarize.choose_summary("Microsoft (MSFT) Moves -5.7%", google_junk, mode="verbatim")
    assert out == "Microsoft (MSFT) Moves -5.7%"
    assert "Google News" not in out


def test_extract_text_rejects_boilerplate_meta():
    from mag7bot import article

    html = ('<html><head><meta property="og:description" content="Comprehensive '
            'up-to-date news coverage, aggregated from sources all over the world '
            'by Google News."></head><body></body></html>')
    assert article.extract_text(html) == ""  # boilerplate meta → no content


def test_extract_text_pulls_jsonld_article_body():
    """Modern JS-rendered pages keep the body in a JSON-LD block, not <p> tags.
    The extractor must read it (and prefer it over a thin meta description)."""
    from mag7bot import article

    html = (
        '<html><head>'
        '<meta property="og:description" content="Nvidia raised its outlook.">'
        '<script type="application/ld+json">'
        '{"@type":"NewsArticle","headline":"Nvidia raises outlook",'
        '"articleBody":"Nvidia said data-center revenue rose 28% to $41.1 billion '
        'in the quarter and guided to continued growth as customers adopt Rubin."}'
        '</script></head><body><div id="app"><p>Loading…</p></div></body></html>'
    )
    text = article.extract_text(html)
    assert "28% to $41.1 billion" in text          # full body recovered
    assert text != "Nvidia raised its outlook."    # not the thin meta blurb


def test_extract_text_jsonld_handles_graph_wrapper():
    """Publishers often wrap objects in an @graph list — articleBody still found."""
    from mag7bot import article

    html = (
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":['
        '{"@type":"Organization","name":"Reuters"},'
        '{"@type":"NewsArticle","articleBody":"Micron reported revenue of $41.46 '
        'billion and adjusted EPS of $25.11, well above consensus estimates."}]}'
        '</script></head><body></body></html>'
    )
    text = article.extract_text(html)
    assert "$41.46 billion" in text and "$25.11" in text


def test_extract_text_jsonld_ignores_malformed_block():
    """A broken JSON-LD block must not crash extraction — fall through to prose."""
    from mag7bot import article

    html = (
        '<html><head><script type="application/ld+json">{not valid json,,,</script>'
        '</head><body><article><p>Tesla deliveries rose to a record in the '
        'quarter, the company said, beating Wall Street expectations handily.</p>'
        '</article></body></html>'
    )
    text = article.extract_text(html)
    assert "Tesla deliveries rose to a record" in text


def test_extract_text_prefers_article_region_over_page():
    """When an <article> region exists, its prose is used and surrounding page
    chrome (promo blocks, related links) is excluded."""
    from mag7bot import article

    html = (
        '<html><body>'
        '<div><p>Sign up for our newsletter to get the best stock tips daily now.</p></div>'
        '<article><p>Apple unveiled a new iPhone with a faster chip and improved '
        'battery life, the company announced at its fall product event today.</p>'
        '</article>'
        '<div><p>Related: ten other gadgets you should consider buying this year.</p></div>'
        '</body></html>'
    )
    text = article.extract_text(html)
    assert "Apple unveiled a new iPhone" in text
    assert "newsletter" not in text and "Related" not in text


def test_relevance_drops_price_move_filler():
    from mag7bot.pipeline import relevance as rel

    # Auto-generated price-move pieces are dropped.
    for junk in [
        "Microsoft (MSFT) Moves -5.7%: What You Should Know",
        "Why NVIDIA (NVDA) Stock Is Up 3%",
        "Tesla (TSLA) Stock Falls 4% in Afternoon Trading",
        "Apple gains 2.3% as market rallies",
        "Meta surges 6% to a new high",
    ]:
        assert rel.is_low_quality(junk) is True, junk

    # Genuine company news still passes.
    for ok in [
        "NVIDIA to acquire Run:ai in $700M deal",
        "Microsoft beats Q3 estimates on cloud strength",
        "Apple unveils new MacBook lineup",
        "Tesla recalls 1.2 million vehicles over software fault",
    ]:
        assert rel.is_low_quality(ok) is False, ok


# --------------------------------------------------------------------------- #
# Non-blocking curation learning (feedback → suppression rules)                 #
# --------------------------------------------------------------------------- #


def test_feedback_count_dedups_same_event(cfg):
    from mag7bot import db

    now = 1_700_000_000.0
    # Two 👎 on the SAME event count once (not two votes from one tap-retap).
    db.record_feedback(cfg.db_path, 50, "AAPL", "Reuters", "news", "down", now)
    db.record_feedback(cfg.db_path, 50, "AAPL", "Reuters", "news", "down", now + 1)
    assert db.feedback_count(cfg.db_path, "AAPL", "news", "down") == 1


def test_feedback_promotes_and_is_reversible(cfg):
    from mag7bot import db

    now = 1_700_000_000.0
    for i in range(3):  # three distinct events → reaches the threshold of 3
        db.record_feedback(cfg.db_path, 100 + i, "NVDA", "Reuters", "news", "down", now + i)
    assert db.feedback_count(cfg.db_path, "NVDA", "news", "down") == 3

    assert db.add_suppression_rule(cfg.db_path, "NVDA", "news", 3, now) is True
    assert db.is_suppressed(cfg.db_path, "NVDA", "news") is True
    # Re-promoting an already-active rule is a no-op (no duplicate announcement).
    assert db.add_suppression_rule(cfg.db_path, "NVDA", "news", 3, now) is False

    rules = db.active_suppression_rules(cfg.db_path)
    assert len(rules) == 1 and rules[0]["ticker"] == "NVDA"
    # /unrule reverses it.
    assert db.deactivate_suppression_rule(cfg.db_path, rules[0]["id"]) is True
    assert db.is_suppressed(cfg.db_path, "NVDA", "news") is False


def test_suppression_rule_blocks_build_events(cfg):
    from mag7bot import db, ingest

    now = 1_700_000_000.0
    control = _item("Nvidia featured in today's market session recap",
                    ticker="NVDA", source="finnhub",
                    url="https://www.reuters.com/a", ts=now)
    etype = classify.classify(control)
    # Control: with no rule, the item posts.
    assert len(ingest.build_events(cfg, [control], now)) == 1

    # Learn a suppression rule for (NVDA, <that type>); a fresh same-type item drops.
    db.add_suppression_rule(cfg.db_path, "NVDA", etype.value, 3, now)
    later = _item("Nvidia appears in another market wrap column",
                  ticker="NVDA", source="finnhub",
                  url="https://www.reuters.com/b", ts=now)
    assert ingest.build_events(cfg, [later], now) == []


def test_get_event_roundtrip(cfg):
    from mag7bot import db, ingest

    now = 1_700_000_000.0
    item = _item("NVIDIA to acquire Run:ai in a deal", ticker="NVDA",
                 source="finnhub", url="https://www.reuters.com/c", ts=now)
    events = ingest.build_events(cfg, [item], now)
    assert events and events[0].id is not None
    fetched = db.get_event(cfg.db_path, events[0].id)
    assert fetched is not None and fetched.ticker == "NVDA"


def test_is_nonsummary_detects_refusals():
    from mag7bot.pipeline import summarize as s

    # Empty / whitespace / refusal / meta output → not postable.
    assert s.is_nonsummary("")
    assert s.is_nonsummary("   ")
    assert s.is_nonsummary("No article text provided; unable to generate summary.")
    assert s.is_nonsummary("No content available.")
    assert s.is_nonsummary("Unable to summarize the article.")
    assert s.is_nonsummary("Insufficient information to summarize.")
    # Genuine news that merely contains similar words → postable.
    assert not s.is_nonsummary("The company said it was unable to meet demand this quarter.")
    assert not s.is_nonsummary("Management cannot provide full-year guidance amid uncertainty.")
    assert not s.is_nonsummary("Target stock surged 40% this year under its new CEO.")


def test_empty_content_item_is_dropped(cfg):
    from mag7bot import ingest

    now = 1_700_000_000.0
    # An item with no headline and no body has nothing to summarise — it must not
    # reach the channel as a value-less "No article text provided" alert.
    blank = _item("", ticker="TSLA", source="finnhub",
                  url="https://www.reuters.com/blank", ts=now)
    assert ingest.build_events(cfg, [blank], now) == []


# --------------------------------------------------------------------------- #
# Summary-quality learning (✏️ corrections → few-shot examples)                 #
# --------------------------------------------------------------------------- #


def test_summarize_includes_house_style_examples():
    # Stub summary stays faithful to the source so it passes the guard; the point
    # is that the examples are injected into the prompt.
    client = _CapturingAnthropic("Palantir is backed by a key ally.")
    out = summarize.choose_summary(
        "Palantir gets AI signal", "A key ally backs Palantir.",
        mode="llm", client=client, use_llm=True, subject="Palantir",
        examples=["NVDA up 6% after earnings beat — record data-center revenue."],
    )
    content = client.captured["messages"][0]["content"]
    assert "House-style examples" in content
    assert "record data-center revenue" in content
    assert out == "Palantir is backed by a key ally."


def test_summary_examples_crud(cfg):
    from mag7bot import db

    now = 1_700_000_000.0
    db.add_summary_example(cfg.db_path, "NVDA", "earnings", "old vague",
                           "Nvidia beat on data-center revenue.", now)
    assert db.recent_summary_examples(cfg.db_path) == ["Nvidia beat on data-center revenue."]
    rows = db.list_summary_examples(cfg.db_path)
    assert len(rows) == 1 and rows[0]["ticker"] == "NVDA"
    assert db.deactivate_summary_example(cfg.db_path, rows[0]["id"]) is True
    assert db.recent_summary_examples(cfg.db_path) == []  # forgotten → not reused


def test_build_events_threads_examples_into_summary(cfg):
    import dataclasses
    from mag7bot import db, ingest

    now = 1_700_000_000.0
    db.add_summary_example(cfg.db_path, "NVDA", "product_launch", "old",
                           "Concrete house-style line.", now)

    captured = {}

    class _C:
        def __init__(self):
            parsed = type("P", (), {"summary": "Nvidia ships a product."})()
            resp = type("R", (), {"parsed_output": parsed})()

            def _parse(_self, **kw):
                captured.update(kw)
                return resp

            self.messages = type("M", (), {"parse": _parse})()

    llm_cfg = dataclasses.replace(cfg, summary_mode="llm")
    item = _item("Nvidia unveils a product", ticker="NVDA", source="finnhub",
                 url="https://www.reuters.com/z", ts=now)
    ingest.build_events(llm_cfg, [item], now, client=_C())
    content = captured["messages"][0]["content"]
    assert "Concrete house-style line." in content


# --------------------------------------------------------------------------- #
# Soul (house-voice style guide) + LLM review                                   #
# --------------------------------------------------------------------------- #


def test_soul_seeds_and_persists(cfg):
    from mag7bot import soul

    text = soul.load(cfg)
    assert "House voice" in text                      # seeded with the default
    assert soul.soul_path(cfg).exists()               # written to the volume
    assert soul.save(cfg, "# Custom voice\n- be terse") is True
    assert soul.load(cfg).startswith("# Custom voice")
    # Previous version is backed up for easy revert.
    assert (soul.soul_path(cfg).parent / "soul.prev.md").exists()
    soul.reset(cfg)
    assert "House voice" in soul.load(cfg)


def test_soul_injected_into_summary_prompt():
    client = _CapturingAnthropic("Nvidia ships a new chip.")
    summarize.choose_summary(
        "Nvidia unveils a chip", "Nvidia unveils a chip today.",
        mode="llm", client=client, use_llm=True,
        style_guide="- ALWAYS lead with the dollar figure.",
    )
    system = client.captured["system"]
    assert "ALWAYS lead with the dollar figure." in system
    # The immutable safety rules are still present (style is appended, not replacing).
    assert "Do NOT" in system or "do NOT" in system


def test_soul_propose_update_with_stub(cfg):
    from mag7bot import soul

    class _C:
        def __init__(self):
            parsed = type("P", (), {"soul": "# House voice\n- Lead with the number."})()
            resp = type("R", (), {"parsed_output": parsed})()
            self.messages = type("M", (), {"parse": lambda _s, **kw: resp})()

    out = soul.propose_update(
        _C(), soul.DEFAULT_SOUL,
        corrections=["Nvidia beat on data-center revenue, up 28%."],
        muted=["$AAPL analyst"], model="x",
    )
    assert out is not None and "Lead with the number" in out


def test_build_events_threads_soul_into_summary(cfg):
    import dataclasses
    from mag7bot import ingest, soul

    now = 1_700_000_000.0
    soul.save(cfg, "# House voice\n- DISTINCTIVE-SOUL-MARKER lead with the number.")

    captured = {}

    class _C:
        def __init__(self):
            parsed = type("P", (), {"summary": "Nvidia ships a product."})()
            resp = type("R", (), {"parsed_output": parsed})()

            def _parse(_self, **kw):
                captured.update(kw)
                return resp

            self.messages = type("M", (), {"parse": _parse})()

    llm_cfg = dataclasses.replace(cfg, summary_mode="llm")
    item = _item("Nvidia unveils a product", ticker="NVDA", source="finnhub",
                 url="https://www.reuters.com/soul", ts=now)
    ingest.build_events(llm_cfg, [item], now, client=_C())
    assert "DISTINCTIVE-SOUL-MARKER" in captured["system"]


# --------------------------------------------------------------------------- #
# Economic-calendar preview + broadened macro relay                             #
# --------------------------------------------------------------------------- #

_FF_JSON = """[
 {"title":"GDP (QoQ) (Q1)","country":"GBP","date":"2026-06-30T09:00:00+08:00","impact":"High","forecast":"0.6%","previous":"0.2%"},
 {"title":"German CPI (MoM) (Jun)","country":"EUR","date":"2026-06-30T15:00:00+08:00","impact":"High","forecast":"0.1%","previous":"-0.2%"},
 {"title":"Chicago PMI (Jun)","country":"USD","date":"2026-06-30T16:45:00+08:00","impact":"High","forecast":"60.0","previous":"62.7"},
 {"title":"Some low-impact thing","country":"USD","date":"2026-06-30T18:00:00+08:00","impact":"Low","forecast":"","previous":""},
 {"title":"Aussie jobs","country":"AUD","date":"2026-06-30T08:00:00+08:00","impact":"High","forecast":"","previous":""},
 {"title":"Next-day CPI","country":"USD","date":"2026-07-01T12:00:00+08:00","impact":"High","forecast":"0.2%","previous":"0.1%"}
]"""


def test_calendar_parse_filter_and_format():
    from datetime import datetime
    from mag7bot import economic_calendar as ec
    from mag7bot.config import SGT

    events = ec.parse_calendar(_FF_JSON)
    assert len(events) == 6
    day = datetime(2026, 6, 30, 12, 0, tzinfo=SGT)
    todays = ec.events_for_day(events, day, ["USD", "EUR", "GBP"], ["High"])
    titles = [e.title for e in todays]
    # High-impact USD/EUR/GBP on the 30th only: low-impact, AUD (off-list) and the
    # next-day CPI are excluded.
    assert titles == ["GDP (QoQ) (Q1)", "German CPI (MoM) (Jun)", "Chicago PMI (Jun)"]

    text = ec.format_calendar_digest(todays, day)
    assert "high-impact macro" in text
    assert "Chicago PMI (Jun)" in text and "prev 62.7 · est 60.0" in text
    assert "🕒 16:45 SGT" in text


def test_calendar_empty_day_is_blank():
    from datetime import datetime
    from mag7bot import economic_calendar as ec
    from mag7bot.config import SGT

    assert ec.format_calendar_digest([], datetime(2026, 6, 30, tzinfo=SGT)) == ""
    assert ec.parse_calendar("not json") == []


def test_relay_macro_detector_covers_more_indicators():
    from mag7bot import telegram_monitor as tm

    # Previously-missed high-impact indicators now relay (tagged MACRO).
    assert tm._is_macro("USD | Chicago PMI (Jun) Forecast 60.0") is True
    assert tm._is_macro("JOLTS Job Openings come in below forecast") is True
    assert tm._is_macro("CB Consumer Confidence ticks higher") is True
    assert tm._is_macro("US retail sales rebound in May") is True
    assert tm._is_macro("ECB holds rates; Lagarde stays cautious") is True
    # Still covered.
    assert tm._is_macro("GBP | GDP (QoQ) (Q1)") is True
    assert tm._is_macro("German CPI rises 0.1%") is True
    # Non-macro chatter still ignored.
    assert tm._is_macro("Apple unveils a new MacBook") is False


# --------------------------------------------------------------------------- #
# Multi-company tagging (header shows every watched name in the story)          #
# --------------------------------------------------------------------------- #


def test_tickers_in_finds_all_watchlist_companies():
    from mag7bot import companies

    wl = ["AAPL", "GOOGL", "MSFT", "NVDA"]
    found = companies.tickers_in(
        "CMA proposes new Apple and Google mobile platform rules", wl
    )
    assert "AAPL" in found and "GOOGL" in found
    assert "MSFT" not in found and "NVDA" not in found
    # Cashtags also match.
    assert companies.tickers_in("$MSFT and $NVDA partner", wl) == ["MSFT", "NVDA"]


def test_build_events_tags_multiple_tickers(cfg):
    from mag7bot import ingest

    now = 1_700_000_000.0
    item = _item(
        "CMA proposes new Apple and Google mobile platform rules for UK developers",
        ticker="AAPL", source="finnhub", url="https://www.reuters.com/cma", ts=now,
    )
    events = ingest.build_events(cfg, [item], now)
    assert len(events) == 1
    ev = events[0]
    assert ev.ticker == "AAPL"                  # primary unchanged (dedup/DB key)
    assert "AAPL" in ev.tickers and "GOOGL" in ev.tickers  # both tagged
    assert ev.tickers[0] == "AAPL"              # primary first


def test_format_alert_shows_multiple_tickers():
    from mag7bot.schemas import Event, EventType, Materiality, Tier

    ev = Event(
        ticker="AAPL", tickers=["AAPL", "GOOGL"], type=EventType.LEGAL_REGULATORY,
        summary="UK regulator proposes new platform rules for Apple and Google.",
        tier=Tier.WIRE, materiality=Materiality.MATERIAL, ts=1_700_000_000.0,
        links=["https://www.reuters.com/x"],
    )
    line1 = formatter.format_alert(ev).splitlines()[0]
    assert line1 == "$AAPL · $GOOGL"


def test_format_alert_single_ticker_unchanged():
    from mag7bot.schemas import Event, EventType, Materiality, Tier

    ev = Event(
        ticker="NVDA", type=EventType.NEWS, summary="Nvidia ships a chip.",
        tier=Tier.WIRE, materiality=Materiality.MATERIAL, ts=1_700_000_000.0,
    )
    assert formatter.format_alert(ev).splitlines()[0] == "$NVDA"  # tickers empty → falls back


# --------------------------------------------------------------------------- #
# Semantic dedup (LLM fallback for paraphrased duplicates)                      #
# --------------------------------------------------------------------------- #


class _DupStub:
    """Stub LLM that returns a fixed duplicate_of id (or None)."""

    def __init__(self, match_id):
        parsed = type("P", (), {"duplicate_of": match_id})()
        resp = type("R", (), {"parsed_output": parsed})()
        self.captured = {}

        def _parse(_self, **kw):
            self.captured.update(kw)
            return resp

        self.messages = type("M", (), {"parse": _parse})()


def test_semantic_find_matches_and_validates_id():
    from mag7bot.pipeline import dedup
    from mag7bot.schemas import Event, EventType, Materiality, Tier

    cand = Event(id=7, ticker="AAPL", type=EventType.LEGAL_REGULATORY,
                 summary="CMA proposes new Apple/Google app payment rules.",
                 tier=Tier.WIRE, materiality=Materiality.MATERIAL, ts=1.0)
    # Model says id 7 → returns that event.
    assert dedup.semantic_find(_DupStub(7), "UK CMA forces Apple, Google on payments", [cand], "m") is cand
    # Model says none → None.
    assert dedup.semantic_find(_DupStub(None), "x", [cand], "m") is None
    # Model hallucinates a non-candidate id → None (validated against candidates).
    assert dedup.semantic_find(_DupStub(999), "x", [cand], "m") is None
    # No candidates → no call, None.
    assert dedup.semantic_find(_DupStub(7), "x", [], "m") is None


def test_build_events_semantic_dedup_collapses_paraphrase(cfg):
    import dataclasses
    from mag7bot import db, ingest

    now = 1_700_000_000.0
    llm_cfg = dataclasses.replace(cfg, summary_mode="llm")

    # First story posts (stub summary stays faithful so it survives the guard).
    first = _item("CMA Proposes New Apple And Google Mobile Platform Rules",
                  ticker="AAPL", source="finnhub", url="https://www.reuters.com/cma1", ts=now)
    ev1 = ingest.build_events(llm_cfg, [first], now, client=_StubAnthropic("Apple and Google face new CMA platform rules."))
    assert len(ev1) == 1
    first_id = ev1[0].id

    # A differently-worded re-report from another outlet (different URL/words).
    second = _item(
        "UK Competition and Markets Authority moves to force Apple, Google open payments",
        ticker="AAPL", source="yahoo_news", url="https://finance.yahoo.com/cma2", ts=now + 1500,
    )
    # The semantic check (stub) says it duplicates the first → no new event.
    ev2 = ingest.build_events(llm_cfg, [second], now + 1500, client=_DupStub(first_id))
    assert ev2 == []
    # Only one event exists for AAPL, with both links merged.
    recent = db.recent_events(llm_cfg.db_path, llm_cfg.feed_id, "AAPL", now - 3600)
    assert len(recent) == 1 and len(recent[0].links) == 2


# --------------------------------------------------------------------------- #
# Public-channel prep: curation buttons → owner DM; clean public posts          #
# --------------------------------------------------------------------------- #


class _FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kw):
        self.calls.append(kw)
        return type("M", (), {"message_id": 123})()


def _push_event():
    from mag7bot.schemas import Event, EventType, Materiality, Tier
    return Event(id=5, ticker="NVDA", type=EventType.NEWS, summary="Nvidia ships a chip.",
                 tier=Tier.WIRE, materiality=Materiality.MATERIAL, ts=1_700_000_000.0,
                 links=["https://www.reuters.com/x"])


def test_publisher_private_keeps_buttons_on_channel():
    import asyncio
    from mag7bot import publisher as pub

    bot = _FakeBot()
    p = pub.ChannelPublisher(bot, "@chan", feedback_enabled=True, owner_id=99, public=False)
    asyncio.run(p.push(_push_event()))
    assert len(bot.calls) == 1                       # channel only, no DM mirror
    assert bot.calls[0]["chat_id"] == "@chan"
    assert bot.calls[0]["reply_markup"] is not None  # buttons on the channel post


def test_publisher_public_moves_buttons_to_owner_dm():
    import asyncio
    from mag7bot import publisher as pub

    bot = _FakeBot()
    p = pub.ChannelPublisher(bot, "@chan", feedback_enabled=True, owner_id=99, public=True)
    asyncio.run(p.push(_push_event()))
    assert len(bot.calls) == 2
    channel, dm = bot.calls[0], bot.calls[1]
    assert channel["chat_id"] == "@chan" and channel["reply_markup"] is None  # clean public post
    assert dm["chat_id"] == 99 and dm["reply_markup"] is not None             # buttons in owner DM
    assert dm["text"].startswith("🛠 Curate")


def test_digest_carries_disclaimer_footer():
    out = formatter.format_digest([], ["NVDA"], 1_700_000_000.0)
    assert "not financial advice" in out
