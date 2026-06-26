"""Channel publishers (PRD §3 publish plane).

Alerts and digests go *only* to the channel (F9). Two implementations share one
interface so the whole pipeline can run offline:

  - ``ChannelPublisher`` posts to the Telegram channel via a PTB Bot.
  - ``StdoutPublisher`` prints — used by ``--dry-run`` and tests.

``run_digest`` compiles the buffered (pending) events into one daily message,
sends it, and marks those events delivered.
"""

from __future__ import annotations

from typing import Protocol

from . import db
from .config import Config
from .pipeline.formatter import format_alert, format_digest
from .schemas import Event, SentMode


class Publisher(Protocol):
    async def push(self, event: Event) -> None: ...
    async def send_digest(self, text: str) -> None: ...


class ChannelPublisher:
    """Posts to the configured Telegram channel."""

    def __init__(self, bot, channel_id: str) -> None:
        self._bot = bot
        self._channel_id = channel_id

    async def push(self, event: Event) -> None:
        await self._bot.send_message(
            chat_id=self._channel_id,
            text=format_alert(event),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def send_digest(self, text: str) -> None:
        await self._bot.send_message(
            chat_id=self._channel_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


class StdoutPublisher:
    """Prints messages instead of sending — for dry-run / tests."""

    def __init__(self) -> None:
        self.pushed: list[str] = []
        self.digests: list[str] = []

    async def push(self, event: Event) -> None:
        msg = format_alert(event)
        self.pushed.append(msg)
        print("\n--- INSTANT PUSH ---\n" + msg)

    async def send_digest(self, text: str) -> None:
        self.digests.append(text)
        print("\n--- DAILY DIGEST ---\n" + text)


async def run_digest(cfg: Config, publisher: Publisher, now: float) -> int:
    """Compile + send the daily digest; mark buffered events delivered (F7)."""
    pending = db.pending_digest_events(cfg.db_path, cfg.feed_id)
    tickers = db.watchlist_tickers(cfg.db_path, cfg.feed_id)
    text = format_digest(pending, tickers, now)
    await publisher.send_digest(text)
    for event in pending:
        if event.id is not None:
            db.mark_event_sent(cfg.db_path, event.id, SentMode.DIGEST)
    return len(pending)
