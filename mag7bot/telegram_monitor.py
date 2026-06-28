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
from .schemas import RawItem, Tier

# Cashtag pattern — $NVDA, $AAPL etc.
_CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")


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

                tickers = db.watchlist_tickers(cfg.db_path, cfg.feed_id)
                ticker = _find_ticker(text, tickers)
                if ticker is None:
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
            channels = db.list_telegram_channel_usernames(cfg.db_path)
            print(f"📡 Telegram channel monitor connected. Watching: {', '.join(channels) or '(none yet — use /add_channel)'}")
            # Keep running until the event loop stops.
            await asyncio.Event().wait()
        except Exception as exc:
            print(f"⚠️  Telegram channel monitor failed to start: {exc}")
            print("   Run `python -m mag7bot.telegram_setup` to authenticate.")
        finally:
            try:
                await app.stop()
            except Exception:
                pass
