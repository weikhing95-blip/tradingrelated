"""SQLite state layer (PRD §9).

One database, multiple tables. Connections are short-lived (opened per call)
with WAL enabled so the polling jobs and the command handlers can touch the DB
concurrently without tripping over each other. At MVP volumes this is plenty.

All timestamps are stored as Unix epoch seconds (UTC). JSON-valued columns
(`groups`, `links`, `categories_enabled`) are stored as TEXT and (de)serialised
at the boundary.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

from .schemas import Event, EventType, Materiality, RawItem, SentMode, Tier

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    ticker  TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    cik     TEXT NOT NULL,
    sector  TEXT NOT NULL,
    groups  TEXT NOT NULL DEFAULT '[]'   -- JSON array
);

CREATE TABLE IF NOT EXISTS feed (
    feed_id          INTEGER PRIMARY KEY,
    owner_user_id    INTEGER NOT NULL,
    channel_id       TEXT NOT NULL,
    digest_time_sgt  TEXT NOT NULL DEFAULT '0900',  -- "HHMM"
    quiet_start      INTEGER NOT NULL DEFAULT 0,
    quiet_end        INTEGER NOT NULL DEFAULT 7
);

CREATE TABLE IF NOT EXISTS watchlist (
    feed_id            INTEGER NOT NULL,
    ticker             TEXT NOT NULL,
    cik                TEXT,
    categories_enabled TEXT NOT NULL DEFAULT '[]',  -- JSON array; [] = all enabled
    mute_until         REAL,                        -- epoch secs; NULL = not muted
    PRIMARY KEY (feed_id, ticker)
);

CREATE TABLE IF NOT EXISTS raw_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    payload        TEXT NOT NULL,   -- raw source JSON
    fetched_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id         INTEGER NOT NULL,
    ticker          TEXT NOT NULL,
    type            TEXT NOT NULL,
    summary         TEXT NOT NULL,
    links           TEXT NOT NULL DEFAULT '[]',  -- JSON array
    tier            TEXT NOT NULL,
    source_name     TEXT NOT NULL DEFAULT '',
    materiality     TEXT NOT NULL,
    confirmed_count INTEGER NOT NULL DEFAULT 1,
    unconfirmed     INTEGER NOT NULL DEFAULT 0,
    sent_mode       TEXT NOT NULL DEFAULT 'pending',
    ts              REAL NOT NULL,
    dedup_key       TEXT NOT NULL DEFAULT '',  -- normalised headline, for dedup
    tickers         TEXT NOT NULL DEFAULT '[]'  -- JSON: all watchlist tickers in the story
);
CREATE INDEX IF NOT EXISTS idx_events_ticker_ts ON events (ticker, ts);
CREATE INDEX IF NOT EXISTS idx_events_sentmode ON events (sent_mode);

CREATE TABLE IF NOT EXISTS seen (
    source        TEXT NOT NULL,
    item_id       TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    PRIMARY KEY (source, item_id)
);

-- Owner-approved publishers added at runtime (on top of the config defaults).
CREATE TABLE IF NOT EXISTS whitelist_extra (
    domain   TEXT PRIMARY KEY,   -- canonical domain, lowercase
    name     TEXT NOT NULL DEFAULT '',
    tier     TEXT NOT NULL DEFAULT 'tier2',
    added_at REAL NOT NULL
);

-- Telegram channels to monitor (managed via /add_channel, /remove_channel).
CREATE TABLE IF NOT EXISTS telegram_channels (
    username   TEXT PRIMARY KEY,   -- e.g. '@WalterBloomberg'
    name       TEXT NOT NULL DEFAULT '',
    added_by   TEXT NOT NULL DEFAULT 'manual',  -- 'manual' or 'research_agent'
    added_at   REAL NOT NULL
);

-- Audit log for the autonomous research agent.
CREATE TABLE IF NOT EXISTS research_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at     REAL NOT NULL,
    action     TEXT NOT NULL,   -- 'added_source', 'rejected_source', 'suggested_channel'
    target     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT ''
);

-- Per-source health: powers /status and owner failure alerts.
CREATE TABLE IF NOT EXISTS source_health (
    source              TEXT PRIMARY KEY,
    last_ok_ts          REAL,            -- last successful fetch
    last_count          INTEGER DEFAULT 0,
    last_error_ts       REAL,
    last_error          TEXT NOT NULL DEFAULT '',
    consecutive_errors  INTEGER NOT NULL DEFAULT 0,
    updated_at          REAL NOT NULL DEFAULT 0
);

-- Owner feedback on posted alerts (non-blocking learning loop).
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER,
    ticker      TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    event_type  TEXT NOT NULL,
    verdict     TEXT NOT NULL DEFAULT 'down',
    created_at  REAL NOT NULL
);

-- Learned suppression rules, promoted from repeated 'down' feedback. Owner can
-- list them (/rules) and remove them (/unrule); nothing is suppressed silently.
CREATE TABLE IF NOT EXISTS suppression_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    UNIQUE(ticker, event_type)
);

-- Owner-supplied "this is how it should read" summaries (✏️ corrections), fed
-- back to the LLM summarizer as house-style few-shot examples.
CREATE TABLE IF NOT EXISTS summary_examples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL DEFAULT '',
    event_type   TEXT NOT NULL DEFAULT '',
    old_summary  TEXT NOT NULL DEFAULT '',
    good_summary TEXT NOT NULL,
    created_at   REAL NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);
"""


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for DBs created before a column existed.
    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so a volume
    that predates a new column needs an explicit ALTER (guarded by a column
    check so it's a no-op on fresh DBs)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "dedup_key" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN dedup_key TEXT NOT NULL DEFAULT ''")
    if "tickers" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN tickers TEXT NOT NULL DEFAULT '[]'")


# --------------------------------------------------------------------------- #
# companies                                                                     #
# --------------------------------------------------------------------------- #


def upsert_company(
    path: Path, ticker: str, name: str, cik: str, sector: str, groups: List[str]
) -> None:
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO companies (ticker, name, cik, sector, groups)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   name=excluded.name, cik=excluded.cik,
                   sector=excluded.sector, groups=excluded.groups""",
            (ticker.upper(), name, cik, sector, json.dumps(groups)),
        )


def groups_for(path: Path, ticker: str) -> List[str]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT groups FROM companies WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
    return json.loads(row["groups"]) if row else []


# --------------------------------------------------------------------------- #
# feed (config)                                                                 #
# --------------------------------------------------------------------------- #


def upsert_feed(
    path: Path,
    feed_id: int,
    owner_user_id: int,
    channel_id: str,
    digest_time_sgt: str,
    quiet_start: int,
    quiet_end: int,
) -> None:
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO feed
                   (feed_id, owner_user_id, channel_id, digest_time_sgt, quiet_start, quiet_end)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(feed_id) DO UPDATE SET
                   owner_user_id=excluded.owner_user_id,
                   channel_id=excluded.channel_id,
                   digest_time_sgt=excluded.digest_time_sgt,
                   quiet_start=excluded.quiet_start,
                   quiet_end=excluded.quiet_end""",
            (feed_id, owner_user_id, channel_id, digest_time_sgt, quiet_start, quiet_end),
        )


def get_feed(path: Path, feed_id: int) -> Optional[sqlite3.Row]:
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM feed WHERE feed_id = ?", (feed_id,)
        ).fetchone()


def set_digest_time(path: Path, feed_id: int, hhmm: str) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE feed SET digest_time_sgt = ? WHERE feed_id = ?", (hhmm, feed_id)
        )


# --------------------------------------------------------------------------- #
# watchlist                                                                     #
# --------------------------------------------------------------------------- #


def add_ticker(path: Path, feed_id: int, ticker: str, cik: Optional[str]) -> None:
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO watchlist (feed_id, ticker, cik)
               VALUES (?, ?, ?)
               ON CONFLICT(feed_id, ticker) DO UPDATE SET cik=excluded.cik""",
            (feed_id, ticker.upper(), cik),
        )


def remove_ticker(path: Path, feed_id: int, ticker: str) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE feed_id = ? AND ticker = ?",
            (feed_id, ticker.upper()),
        )
        return cur.rowcount > 0


def get_watchlist(path: Path, feed_id: int) -> List[sqlite3.Row]:
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM watchlist WHERE feed_id = ? ORDER BY ticker", (feed_id,)
        ).fetchall()


def watchlist_tickers(path: Path, feed_id: int) -> List[str]:
    return [r["ticker"] for r in get_watchlist(path, feed_id)]


def set_mute(path: Path, feed_id: int, ticker: str, mute_until: Optional[float]) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE watchlist SET mute_until = ? WHERE feed_id = ? AND ticker = ?",
            (mute_until, feed_id, ticker.upper()),
        )
        return cur.rowcount > 0


def is_muted(path: Path, feed_id: int, ticker: str, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with connect(path) as conn:
        row = conn.execute(
            "SELECT mute_until FROM watchlist WHERE feed_id = ? AND ticker = ?",
            (feed_id, ticker.upper()),
        ).fetchone()
    return bool(row and row["mute_until"] and row["mute_until"] > now)


def set_categories(path: Path, feed_id: int, ticker: str, categories: List[str]) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE watchlist SET categories_enabled = ? WHERE feed_id = ? AND ticker = ?",
            (json.dumps(categories), feed_id, ticker.upper()),
        )


def categories_for(path: Path, feed_id: int, ticker: str) -> List[str]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT categories_enabled FROM watchlist WHERE feed_id = ? AND ticker = ?",
            (feed_id, ticker.upper()),
        ).fetchone()
    return json.loads(row["categories_enabled"]) if row else []


# --------------------------------------------------------------------------- #
# seen (idempotency)                                                            #
# --------------------------------------------------------------------------- #


def is_seen(path: Path, source: str, item_id: str) -> bool:
    with connect(path) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM seen WHERE source = ? AND item_id = ?", (source, item_id)
            ).fetchone()
            is not None
        )


def seen_count(path: Path) -> int:
    with connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM seen").fetchone()["n"])


def seen_count_for_source(path: Path, source: str) -> int:
    with connect(path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM seen WHERE source = ?", (source,)
            ).fetchone()["n"]
        )


def mark_seen(path: Path, source: str, item_id: str, ticker: str) -> None:
    with connect(path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO seen (source, item_id, ticker, first_seen_at)
               VALUES (?, ?, ?, ?)""",
            (source, item_id, ticker, time.time()),
        )


# --------------------------------------------------------------------------- #
# raw_items                                                                     #
# --------------------------------------------------------------------------- #


def insert_raw_item(path: Path, item: RawItem) -> int:
    with connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO raw_items (source, source_item_id, ticker, payload, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                item.source,
                item.source_item_id,
                item.ticker,
                json.dumps(item.payload),
                time.time(),
            ),
        )
        return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# events                                                                        #
# --------------------------------------------------------------------------- #


def _row_to_event(row: sqlite3.Row) -> Event:
    keys = row.keys()
    return Event(
        id=row["id"],
        ticker=row["ticker"],
        tickers=(json.loads(row["tickers"]) if "tickers" in keys and row["tickers"] else [row["ticker"]]),
        type=EventType(row["type"]),
        summary=row["summary"],
        links=json.loads(row["links"]),
        tier=Tier(row["tier"]),
        source_name=row["source_name"],
        materiality=Materiality(row["materiality"]),
        confirmed_count=row["confirmed_count"],
        unconfirmed=bool(row["unconfirmed"]),
        sent_mode=SentMode(row["sent_mode"]),
        ts=row["ts"],
        dedup_key=(row["dedup_key"] if "dedup_key" in row.keys() else ""),
    )


def insert_event(path: Path, feed_id: int, event: Event) -> int:
    with connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO events
                   (feed_id, ticker, type, summary, links, tier, source_name,
                    materiality, confirmed_count, unconfirmed, sent_mode, ts,
                    dedup_key, tickers)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                feed_id,
                event.ticker,
                event.type.value,
                event.summary,
                json.dumps(event.links),
                event.tier.value,
                event.source_name,
                event.materiality.value,
                event.confirmed_count,
                int(event.unconfirmed),
                event.sent_mode.value,
                event.ts,
                event.dedup_key,
                json.dumps(event.tickers or [event.ticker]),
            ),
        )
        return int(cur.lastrowid)


def recent_events(path: Path, feed_id: int, ticker: str, since_ts: float) -> List[Event]:
    """Events for a ticker at or after `since_ts` — the dedup lookback window."""
    with connect(path) as conn:
        rows = conn.execute(
            """SELECT * FROM events
               WHERE feed_id = ? AND ticker = ? AND ts >= ?
               ORDER BY ts""",
            (feed_id, ticker.upper(), since_ts),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def recent_events_window(path: Path, feed_id: int, since_ts: float) -> List[Event]:
    """All events for the feed at or after `since_ts`, any ticker — used for
    cross-ticker URL dedup (one article surfacing under several tickers)."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE feed_id = ? AND ts >= ? ORDER BY ts",
            (feed_id, since_ts),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def update_event_links(
    path: Path, event_id: int, links: List[str], confirmed_count: int
) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE events SET links = ?, confirmed_count = ? WHERE id = ?",
            (json.dumps(links), confirmed_count, event_id),
        )


def get_event(path: Path, event_id: int) -> Optional[Event]:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_event(row) if row else None


# --------------------------------------------------------------------------- #
# feedback + learned suppression rules (non-blocking curation learning)         #
# --------------------------------------------------------------------------- #


def record_feedback(
    path: Path, event_id: Optional[int], ticker: str, source: str,
    event_type: str, verdict: str, now: float,
) -> None:
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO feedback
                   (event_id, ticker, source, event_type, verdict, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_id, ticker.upper(), source, event_type, verdict, now),
        )


def feedback_count(path: Path, ticker: str, event_type: str, verdict: str = "down") -> int:
    """Distinct events with this verdict for a (ticker, event_type) — so two
    'down' taps on the *same* alert count once, not twice."""
    with connect(path) as conn:
        row = conn.execute(
            """SELECT COUNT(DISTINCT COALESCE(event_id, -id)) AS n FROM feedback
               WHERE ticker = ? AND event_type = ? AND verdict = ?""",
            (ticker.upper(), event_type, verdict),
        ).fetchone()
    return int(row["n"]) if row else 0


def add_suppression_rule(
    path: Path, ticker: str, event_type: str, hits: int, now: float
) -> bool:
    """Promote (or re-activate) a learned suppression rule. Returns True if it was
    newly created or flipped from inactive→active (i.e. a state change worth
    announcing), False if it was already active."""
    with connect(path) as conn:
        existing = conn.execute(
            "SELECT active FROM suppression_rules WHERE ticker = ? AND event_type = ?",
            (ticker.upper(), event_type),
        ).fetchone()
        if existing is not None and existing["active"]:
            return False
        conn.execute(
            """INSERT INTO suppression_rules (ticker, event_type, hits, created_at, active)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(ticker, event_type)
               DO UPDATE SET active = 1, hits = excluded.hits, created_at = excluded.created_at""",
            (ticker.upper(), event_type, hits, now),
        )
    return True


def is_suppressed(path: Path, ticker: str, event_type: str) -> bool:
    with connect(path) as conn:
        row = conn.execute(
            """SELECT 1 FROM suppression_rules
               WHERE ticker = ? AND event_type = ? AND active = 1""",
            (ticker.upper(), event_type),
        ).fetchone()
    return row is not None


def active_suppression_rules(path: Path) -> List[dict]:
    with connect(path) as conn:
        rows = conn.execute(
            """SELECT id, ticker, event_type, hits, created_at FROM suppression_rules
               WHERE active = 1 ORDER BY created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def deactivate_suppression_rule(path: Path, rule_id: int) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE suppression_rules SET active = 0 WHERE id = ? AND active = 1",
            (rule_id,),
        )
        return cur.rowcount > 0


def add_summary_example(
    path: Path, ticker: str, event_type: str, old_summary: str,
    good_summary: str, now: float,
) -> int:
    with connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO summary_examples
                   (ticker, event_type, old_summary, good_summary, created_at, active)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (ticker.upper(), event_type, old_summary, good_summary, now),
        )
        return int(cur.lastrowid)


def recent_summary_examples(path: Path, limit: int = 5) -> List[str]:
    """The most recent owner-approved summaries — house-style few-shot examples."""
    with connect(path) as conn:
        rows = conn.execute(
            """SELECT good_summary FROM summary_examples
               WHERE active = 1 ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [r["good_summary"] for r in rows]


def list_summary_examples(path: Path) -> List[dict]:
    with connect(path) as conn:
        rows = conn.execute(
            """SELECT id, ticker, event_type, good_summary, created_at
               FROM summary_examples WHERE active = 1 ORDER BY created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def deactivate_summary_example(path: Path, example_id: int) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE summary_examples SET active = 0 WHERE id = ? AND active = 1",
            (example_id,),
        )
        return cur.rowcount > 0


def feedback_summary(path: Path, verdict: str = "down") -> List[dict]:
    """Per-(ticker, event_type) feedback tallies, most-flagged first — for /feedback."""
    with connect(path) as conn:
        rows = conn.execute(
            """SELECT ticker, event_type, COUNT(DISTINCT COALESCE(event_id, -id)) AS n
               FROM feedback WHERE verdict = ?
               GROUP BY ticker, event_type ORDER BY n DESC""",
            (verdict,),
        ).fetchall()
    return [dict(r) for r in rows]


def pending_digest_events(path: Path, feed_id: int) -> List[Event]:
    """Events buffered for the digest (not yet delivered)."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE feed_id = ? AND sent_mode = ? ORDER BY ticker, ts",
            (feed_id, SentMode.PENDING.value),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def mark_event_sent(path: Path, event_id: int, mode: SentMode) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE events SET sent_mode = ? WHERE id = ?", (mode.value, event_id)
        )


# --------------------------------------------------------------------------- #
# whitelist_extra (owner-approved publishers)                                   #
# --------------------------------------------------------------------------- #


def add_whitelist_domain(path: Path, domain: str, name: str, tier: str) -> None:
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO whitelist_extra (domain, name, tier, added_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET name=excluded.name, tier=excluded.tier""",
            (domain.strip().lower(), name, tier, time.time()),
        )


def remove_whitelist_domain(path: Path, domain: str) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "DELETE FROM whitelist_extra WHERE domain = ?", (domain.strip().lower(),)
        )
        return cur.rowcount > 0


def get_whitelist_extra(path: Path) -> List[str]:
    """Just the domains, for combining with the config whitelist."""
    with connect(path) as conn:
        rows = conn.execute("SELECT domain FROM whitelist_extra ORDER BY domain").fetchall()
    return [r["domain"] for r in rows]


def list_whitelist_extra(path: Path) -> List[sqlite3.Row]:
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM whitelist_extra ORDER BY tier, domain"
        ).fetchall()


# --------------------------------------------------------------------------- #
# telegram_channels                                                             #
# --------------------------------------------------------------------------- #


def add_telegram_channel(path: Path, username: str, name: str = "", added_by: str = "manual") -> None:
    """Add a channel to the monitor list (idempotent)."""
    username = username.lstrip("@").lower()
    with connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO telegram_channels (username, name, added_by, added_at) VALUES (?,?,?,?)",
            (f"@{username}", name or username, added_by, time.time()),
        )


def remove_telegram_channel(path: Path, username: str) -> bool:
    username = username.lstrip("@").lower()
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM telegram_channels WHERE username = ?", (f"@{username}",))
    return cur.rowcount > 0


def list_telegram_channels(path: Path) -> List[sqlite3.Row]:
    with connect(path) as conn:
        return conn.execute("SELECT * FROM telegram_channels ORDER BY added_at").fetchall()


def list_telegram_channel_usernames(path: Path) -> List[str]:
    with connect(path) as conn:
        rows = conn.execute("SELECT username FROM telegram_channels").fetchall()
    return [r["username"] for r in rows]


# --------------------------------------------------------------------------- #
# research_log                                                                  #
# --------------------------------------------------------------------------- #


def log_research(path: Path, action: str, target: str, reason: str = "") -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO research_log (run_at, action, target, reason) VALUES (?,?,?,?)",
            (time.time(), action, target, reason),
        )


def last_digest_events(path: Path, feed_id: int, since_ts: float) -> List[Event]:
    """Events delivered via digest since `since_ts` — backs the `/show` command."""
    with connect(path) as conn:
        rows = conn.execute(
            """SELECT * FROM events
               WHERE feed_id = ? AND sent_mode = ? AND ts >= ?
               ORDER BY ticker, ts""",
            (feed_id, SentMode.DIGEST.value, since_ts),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def status_summary(path: Path, feed_id: int, since: float) -> dict:
    """Operational health counts for the `/status` command.

    Returns total events since ``since`` (push + digest), the push-vs-digest
    split, and the timestamp of the most recent push (across all time).
    """
    with connect(path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE feed_id = ? AND ts >= ?",
            (feed_id, since),
        ).fetchone()["n"]
        pushes = conn.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE feed_id = ? AND ts >= ? AND sent_mode = ?",
            (feed_id, since, SentMode.PUSH.value),
        ).fetchone()["n"]
        last_push_row = conn.execute(
            "SELECT MAX(ts) AS t FROM events WHERE feed_id = ? AND sent_mode = ?",
            (feed_id, SentMode.PUSH.value),
        ).fetchone()
    last_push = last_push_row["t"] if last_push_row else None
    return {
        "total": int(total),
        "pushes": int(pushes),
        "digest": int(total) - int(pushes),
        "last_push": last_push,
    }


# --------------------------------------------------------------------------- #
# Source health (observability + failure alerting)                              #
# --------------------------------------------------------------------------- #


def record_source_ok(path: Path, source: str, count: int, now: float) -> int:
    """Record a successful fetch. Returns the number of consecutive errors the
    source had *before* this success (0 if it was already healthy), so the caller
    can decide whether the prior outage was long enough to announce a recovery."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT consecutive_errors FROM source_health WHERE source = ?", (source,)
        ).fetchone()
        prior_errors = int(row["consecutive_errors"]) if row else 0
        conn.execute(
            """INSERT INTO source_health
                   (source, last_ok_ts, last_count, consecutive_errors, updated_at)
               VALUES (?, ?, ?, 0, ?)
               ON CONFLICT(source) DO UPDATE SET
                   last_ok_ts = excluded.last_ok_ts,
                   last_count = excluded.last_count,
                   consecutive_errors = 0,
                   updated_at = excluded.updated_at""",
            (source, now, int(count), now),
        )
    return prior_errors


def record_source_error(path: Path, source: str, error: str, now: float) -> int:
    """Record a failed fetch and return the new consecutive-error count (1 on a
    fresh failure), so the caller can alert only on the ok→error transition."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT consecutive_errors FROM source_health WHERE source = ?", (source,)
        ).fetchone()
        n = (row["consecutive_errors"] if row else 0) + 1
        conn.execute(
            """INSERT INTO source_health
                   (source, last_error_ts, last_error, consecutive_errors, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                   last_error_ts = excluded.last_error_ts,
                   last_error = excluded.last_error,
                   consecutive_errors = source_health.consecutive_errors + 1,
                   updated_at = excluded.updated_at""",
            (source, now, error[:300], n, now),
        )
    return n


def get_source_health(path: Path) -> List[dict]:
    """All recorded source-health rows, most-recently-updated first."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM source_health ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
