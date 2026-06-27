"""Unit tests for the Mag 7 News Bot pipeline stages.

These cover the pure, deterministic logic — whitelist, classification, dedup,
materiality, formatting and the source parsers — plus an end-to-end
``build_events`` run against a temporary SQLite DB. No network, no Telegram.
"""

from __future__ import annotations

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


# --------------------------------------------------------------------------- #
# materiality                                                                   #
# --------------------------------------------------------------------------- #


def test_materiality_routing():
    assert materiality.score(_item("x"), EventType.MA) == Materiality.CRITICAL
    assert materiality.score(_item("x"), EventType.EARNINGS) == Materiality.CRITICAL
    assert materiality.score(_item("x"), EventType.LEGAL_REGULATORY) == Materiality.MATERIAL
    assert materiality.score(_item("x"), EventType.ANALYST) == Materiality.LOW


def test_materiality_form4_demoted():
    f4 = _item("insider sale", source="edgar", form_type="4", tier=Tier.PRIMARY)
    assert materiality.score(f4, EventType.SEC_FILING) == Materiality.LOW


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
    # Ticker-first: $cashtag leads the line so the company is obvious.
    assert msg.startswith("$NVDA 🔴 NVIDIA to acquire Run:ai")
    assert "📄 Tier 2 — Reuters" in msg
    assert "cross-confirmed (2)" in msg
    # Links are hyperlinked behind the publisher domain, not raw URLs.
    assert '<a href="https://www.reuters.com/a">reuters.com</a>' in msg
    assert '<a href="https://www.bloomberg.com/b">bloomberg.com</a>' in msg
    assert "🔗 https://" not in msg  # no raw URL dumped inline


def test_format_alert_google_link_uses_source_name():
    from mag7bot.schemas import Event, SentMode

    ev = Event(
        ticker="AAPL", type=EventType.NEWS, summary="Apple news",
        links=["https://news.google.com/rss/articles/CBMiAAA?oc=5"],
        tier=Tier.WIRE, source_name="Yahoo Finance", materiality=Materiality.LOW,
        confirmed_count=1, unconfirmed=True, sent_mode=SentMode.PENDING, ts=0.0,
    )
    msg = formatter.format_alert(ev)
    # The visible label is the publisher name, not the ugly google host.
    assert ">Yahoo Finance</a>" in msg
    assert ">news.google.com<" not in msg  # never shown as visible text


def test_format_digest_lists_empty_tickers():
    text = formatter.format_digest([], ["AAPL", "NVDA"], now=0.0)
    assert "AAPL: no material events" in text
    assert "NVDA: no material events" in text


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
    assert "Nvidia old listicle news" not in summaries  # >48h old → dropped


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
