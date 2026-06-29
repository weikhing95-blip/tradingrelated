"""Telegram channel monitor — scrapes public channels for ticker-relevant news.

Monitors a configurable list of public Telegram channels (e.g. @WalterBloomberg)
using pyrogram (MTProto user client). Messages that mention a tracked ticker or
company alias are translated to RawItems and fed through the existing pipeline.

Requirements beyond the bot token:
  - TELEGRAM_API_ID / TELEGRAM_API_HASH   from my.telegram.org
  - TELEGRAM_USER_PHONE                   your phone number (+6512345678)
  - A session file (created on first run via `python -m mag7bot.telegram_setup`)

The monitor runs as a background asyncio task alongside the PTB bot. It does NOT
poll — pyrogram fires an event handler the moment a message arrives, giving
near-zero latency. Channels are read from the `telegram_channels` DB table so
they can be managed at runtime with /add_channel and /remove_channel.

Relevance gate (same as other sources):
  - Only emits if the message contains a $TICKER cashtag or a company alias for
    a ticker currently on the watchlist.
  - Geopolitical / unrelated messages (Iran, earthquakes, …) are dropped.
  - No publisher whitelist check — channel curation replaces it.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import List, Optional

from . import companies, db, ingest
from .config import Config
from .pipeline import relevance
from .schemas import RawItem, Tier

# Cashtag pattern — $NVDA, $AAPL etc.
_CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")

# Clearly non-market topics. A relayed post matched only by a company *alias*
# (not a $cashtag) that reads as one of these is almost always a false positive
# (e.g. "Google" in a privacy-lawsuit-unrelated human-interest story), so it's
# dropped. Cashtag-confident posts are never dropped on this.
_OFF_TOPIC = re.compile(
    r"\b(earthquake|hurricane|typhoon|wildfire|flood|tsunami|plane crash|"
    r"shooting|celebrity|royal family|world cup|olympics|super bowl|"
    r"box office|weather|horoscope)\b",
    re.IGNORECASE,
)


# US macro / Fed relevance — relayed posts with no watchlist ticker but clear
# macro content still forward (tagged MACRO), matching the /goal's "US macro &
# Fed" scope. Kept deliberately specific so general world news doesn't leak in.
_MACRO_RE = re.compile(
    r"\b(fed|fomc|federal reserve|powell|rate hike|rate cut|rate decision|"
    r"interest rate|rate-hike|rate-cut|basis points|bps|cpi|core cpi|inflation|"
    r"pce|ppi|jobless claims|nonfarm|payrolls|jobs report|unemployment rate|"
    r"\bgdp\b|treasury yield|tariff|tariffs)\b",
    re.IGNORECASE,
)


def _has_cashtag(text: str, tickers: List[str]) -> bool:
    up = text.upper()
    return any(m.group(1) in tickers for m in _CASHTAG.finditer(up))


def _is_macro(text: str) -> bool:
    return _MACRO_RE.search(text) is not None


def _find_ticker(text: str, tickers: List[str]) -> Optional[str]:
    """Return the first watchlist ticker found in the message, or None."""
    text_upper = text.upper()
    # 1. Cashtag match — most reliable in finance channels.
    for m in _CASHTAG.finditer(text_upper):
        t = m.group(1)
        if t in tickers:
            return t
    # 2. Company alias / name match.
    text_lower = text.lower()
    for ticker in tickers:
        for alias in companies.aliases_for(ticker):
            if re.search(r"\b" + re.escape(alias.lower()) + r"\b", text_lower):
                return ticker
    return None


def _make_raw_item(ticker: str, text: str, channel: str, msg_id: int, ts: float) -> RawItem:
    headline = text.strip().splitlines()[0][:200]  # first line, ≤200 chars
    body = text.strip()[:600]
    return RawItem(
        source="telegram",
        source_item_id=f"{channel.lstrip('@')}-{msg_id}",
        ticker=ticker,
        tier=Tier.WIRE,
        headline=headline,
        body=body,
        url=f"https://t.me/{channel.lstrip('@')}/{msg_id}",
        publisher=channel,
        published_at=ts,
        payload={"channel": channel, "message_id": msg_id},
    )


class TelegramChannelMonitor:
    """Background task: monitors channels, translates messages to pipeline events."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_path: Path,
        cfg: Config,
        publisher,
        llm_client=None,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_path = session_path
        self._cfg = cfg
        self._publisher = publisher
        self._llm_client = llm_client
        self._app = None  # live pyrogram Client once started (for /channels checks)

    async def check_membership(self, usernames):
        """Live-verify each channel via the running pyrogram client.

        Returns a list of (username, joined, detail). ``joined`` is True only if
        the account is a participant (a prerequisite for receiving the channel's
        messages). Returns ('', False, reason) entries if the client isn't up."""
        app = self._app
        if app is None:
            return [("", False, "relay client not running")]
        results = []
        for u in usernames:
            try:
                chat = await app.get_chat(u)
                try:
                    await app.get_chat_member(chat.id, "me")
                    results.append((u, True, getattr(chat, "title", "") or ""))
                except Exception:
                    results.append((u, False, "resolves but account NOT joined"))
            except Exception as exc:
                results.append((u, False, f"cannot resolve: {type(exc).__name__}"))
        return results

    async def start(self) -> None:
        """Connect the pyrogram client and start listening. Runs forever."""
        try:
            from pyrogram import Client, filters
            from pyrogram.handlers import MessageHandler
        except ImportError:
            print(
                "⚠️  pyrogram not installed — Telegram channel monitor disabled. "
                "Run: pip install pyrogram TgCrypto"
            )
            return

        session_str = str(self._session_path.with_suffix(""))

        app = Client(
            session_str,
            api_id=self._api_id,
            api_hash=self._api_hash,
        )

        cfg = self._cfg
        publisher = self._publisher
        llm_client = self._llm_client

        async def on_channel_message(client, message) -> None:
            try:
                # Identify the channel by its username.
                chat = message.chat
                username = f"@{chat.username}" if chat.username else None
                if username is None:
                    return

                # Only handle messages from channels we're monitoring.
                monitored = {u.lower() for u in db.list_telegram_channel_usernames(cfg.db_path)}
                if username.lower() not in monitored:
                    return

                text = message.text or message.caption or ""
                if not text.strip():
                    return

                # Opinion/listicle/promo posts are noise even from a good channel.
                first_line = text.strip().splitlines()[0]
                if relevance.is_low_quality(first_line):
                    return

                tickers = db.watchlist_tickers(cfg.db_path, cfg.feed_id)
                ticker = _find_ticker(text, tickers)
                if ticker is None:
                    # No watchlist company — relay anyway if it's US macro/Fed.
                    if _is_macro(text):
                        ticker = "MACRO"
                    else:
                        return
                # Alias-only match on a clearly off-topic post → false positive.
                # (MACRO relays are intentional, so the off-topic guard skips them.)
                elif _OFF_TOPIC.search(text) and not _has_cashtag(text, tickers):
                    return

                item = _make_raw_item(ticker, text, username, message.id, message.date.timestamp())
                item_id = item.source_item_id

                # Idempotency: skip if already processed.
                if db.is_seen(cfg.db_path, "telegram", item_id):
                    return
                db.mark_seen(cfg.db_path, "telegram", item_id, ticker)

                now = time.time()
                events = ingest.build_events(cfg, [item], now, llm_client)
                for event in events:
                    if event.id is None:
                        continue
                    if ingest.should_push_now(cfg, event, now):
                        await publisher.push(event)
                        db.mark_event_sent(cfg.db_path, event.id, __import__("mag7bot.schemas", fromlist=["SentMode"]).SentMode.PUSH)
            except Exception as exc:
                print(f"⚠️  telegram_monitor error: {exc}")

        app.add_handler(MessageHandler(on_channel_message, filters.channel))

        try:
            await app.start()
            self._app = app  # expose to /channels for live membership checks
            channels = db.list_telegram_channel_usernames(cfg.db_path)
            print(f"📡 Telegram channel monitor connected. Watching: {', '.join(channels) or '(none yet — use /add_channel)'}")
            # Keep running until the event loop stops.
            await asyncio.Event().wait()
        except Exception as exc:
            print(f"⚠️  Telegram channel monitor failed to start: {exc}")
            print("   Run `python -m mag7bot.telegram_setup` to authenticate.")
        finally:
            self._app = None
            try:
                await app.stop()
            except Exception:
                pass
