"""One-time Telegram user-account setup.

Run this interactively ONCE to authenticate pyrogram with your personal
Telegram account (not the bot token). The session is saved locally and reused
on every subsequent bot start.

    python -m mag7bot.telegram_setup

You will be prompted for your phone number and the OTP Telegram sends you.
If you have two-step verification (2FA) enabled, you will also be prompted for
your cloud password.

After setup, the session file lives next to your DB (see DB_PATH in .env).
Keep it safe — it grants read access to your Telegram account.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .config import load_config


async def _setup() -> None:
    try:
        from pyrogram import Client
    except ImportError:
        print("pyrogram is not installed. Run: pip install pyrogram TgCrypto")
        return

    cfg = load_config(dry_run=True)

    api_id = int(getattr(cfg, "telegram_api_id", "") or 0)
    api_hash = getattr(cfg, "telegram_api_hash", "") or ""
    if not api_id or not api_hash:
        print(
            "Missing TELEGRAM_API_ID or TELEGRAM_API_HASH in .env.\n"
            "Get them from https://my.telegram.org → API Development Tools."
        )
        return

    session_path = cfg.db_path.parent / "telegram_user"
    print(f"Session will be saved to: {session_path}.session")

    app = Client(str(session_path), api_id=api_id, api_hash=api_hash)

    async with app:
        me = await app.get_me()
        print(f"\n✅ Authenticated as: {me.first_name} (@{me.username}, id={me.id})")
        print("Session saved. You can now start the bot normally.")


def main() -> None:
    asyncio.run(_setup())


if __name__ == "__main__":
    main()
