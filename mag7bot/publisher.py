"""Channel publishers (PRD §3 publish plane).

Alerts and digests go *only* to the channel (F9). Two implementations share one
interface so the whole pipeline can run offline:

  - ``ChannelPublisher`` posts to the Telegram channel via a PTB Bot.
  - ``StdoutPublisher`` prints — used by ``--dry-run`` and tests.

``run_digest`` compiles the buffered (pending) events into one daily message,
sends it, and marks those events delivered.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from . import db
from .config import CHANNEL_SEND_ATTEMPTS, Config
from .pipeline.formatter import format_alert, format_digest
from .schemas import Event, SentMode


class Publisher(Protocol):
    async def push(self, event: Event) -> None: ...
    async def send_digest(self, text: str) -> None: ...


class ChannelPublisher:
    """Posts to the configured Telegram channel.

    Curation buttons (👎/✏️) are owner-only. On a **private** channel they ride on
    the channel post itself. On a **public** channel (``public_channel=True``) the
    channel post is kept clean and a mirror of the alert — *with* the buttons — is
    DM'd to the owner, so public subscribers never see (or tap) controls meant for
    the owner."""

    def __init__(
        self, bot, channel_id: str, feedback_enabled: bool = False,
        owner_id: int = 0, public: bool = False, channel_handle: str = "",
    ) -> None:
        self._bot = bot
        self._channel_id = channel_id
        self._feedback_enabled = feedback_enabled
        self._owner_id = owner_id
        self._public = public
        self._channel_handle = channel_handle

    async def _send(self, **kwargs):
        """Send a message, retrying transient network/timeout errors with
        exponential backoff. Telegram round-trips from a cloud host occasionally
        time out; one slow attempt shouldn't drop an alert or alarm the owner.
        Flood-control (RetryAfter) waits the server-instructed delay. Non-network
        errors (e.g. lost admin rights) raise immediately — no point retrying."""
        from telegram.error import NetworkError, RetryAfter, TimedOut

        # Generous per-request timeouts (PTB defaults are ~5s, too tight under load).
        kwargs.setdefault("read_timeout", 20)
        kwargs.setdefault("write_timeout", 20)
        kwargs.setdefault("connect_timeout", 15)
        delay = 1.0
        for attempt in range(1, CHANNEL_SEND_ATTEMPTS + 1):
            try:
                return await self._bot.send_message(**kwargs)
            except RetryAfter as exc:
                if attempt == CHANNEL_SEND_ATTEMPTS:
                    raise
                await asyncio.sleep(getattr(exc, "retry_after", delay) or delay)
            except (TimedOut, NetworkError):
                if attempt == CHANNEL_SEND_ATTEMPTS:
                    raise
                await asyncio.sleep(delay)
                delay *= 2

    def _buttons(self, event: Event):
        """Owner-only 👎/✏️ curation buttons; None when off or event unpersisted."""
        if not self._feedback_enabled or event.id is None:
            return None
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        return InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("👎 not useful", callback_data=f"fb:{event.id}"),
                InlineKeyboardButton("✏️ fix summary", callback_data=f"fx:{event.id}"),
            ]]
        )

    async def push(self, event: Event) -> None:
        buttons = self._buttons(event)
        # Public channel → clean post + forward-friendly footer (no buttons);
        # private → buttons on the post, no footer.
        text = format_alert(event)
        if self._public:
            from .pipeline.formatter import with_public_footer

            text = with_public_footer(text, self._channel_handle)
        await self._send(
            chat_id=self._channel_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=None if self._public else buttons,
        )
        # Public: mirror the alert to the owner's DM so they can still curate.
        if self._public and buttons is not None and self._owner_id:
            try:
                await self._send(
                    chat_id=self._owner_id,
                    text="🛠 Curate:\n" + format_alert(event),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=buttons,
                )
            except Exception:
                pass  # best-effort; never fail the channel post over the mirror

    async def send_digest(self, text: str) -> None:
        await self._send(
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
