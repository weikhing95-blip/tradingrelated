# Operations Runbook — Mag 7 News Bot

Recovery procedures for the common failure modes. The bot is a single long-running
worker on Railway with SQLite state on a volume (`DB_PATH`). Most state is
recoverable; the one thing worth protecting is the `seen` table, which prevents a
duplicate-flood on restart.

Health at a glance: DM the bot **`/status`** (mode, last push, per-source health,
last backup, last healthcheck) and **`/metrics`** (latency/volume/quality).

---

## Backups (how they work)

A nightly job (`BACKUP_TIME_SGT`, default 03:30 SGT) makes a consistent snapshot
via SQLite's online backup API, gzips it, and **DMs it to the owner** (the
off-volume copy — Railway's volume is durable but still a single point of
failure). `/status` shows the last successful backup time. Keep a few of these
DMs; each is a full restore point.

> No object storage is wired by default — the owner DM *is* the off-volume store.
> If you add S3/R2 later, upload there in `scheduler.backup_job` and keep the DM
> as a fallback.

---

## (a) Volume loss / corrupted DB

Symptoms: bot restarts into an empty DB (re-primes from scratch, or `/status`
shows 0 events and every source "pending"), or SQLite errors in the logs.

1. Find the most recent **🗄 Nightly DB backup** file in your owner DM; download it.
2. Restore it onto the volume (run inside the container or with the volume mounted):
   ```bash
   python scripts/restore_db.py mag7bot-YYYYMMDD-HHMMSS.db.gz --out "$DB_PATH" --force
   ```
   The script verifies the `seen` table is intact before overwriting — that's what
   stops a duplicate-flood.
3. Restart the service. On boot the cold-start prime marks current items as seen
   **without alerting**; because `seen` was restored, only genuinely new events fire.
4. Confirm with `/status` (events > 0, sources returning to "ok").

If you have **no** backup: let the bot cold-start prime on an empty DB. You'll get
no historical alerts and no duplicate-flood (prime suppresses the backlog), but
learned suppression rules / summary examples / soul edits are lost.

---

## (b) Bot token revoked / invalid

Symptoms: preflight prints `❌ Token check failed`; nothing posts.

1. In @BotFather, `/revoke` + reissue (or read the existing) token.
2. Update `TELEGRAM_BOT_TOKEN` in Railway → Variables → redeploy.
3. Re-verify the bot is still a **channel admin with "Post Messages"** (a new token
   is the same bot, but double-check after any BotFather change).
4. `python -m mag7bot.app --check` (or watch the deploy preflight) → expect all ✅.

---

## (c) Single source outage

Symptoms: a `⚠️ Source 'x' is failing (N polls in a row)` DM (only after a
sustained outage — single blips are silent by design).

- **Usually self-heals** — you'll get a `✅ recovered` DM. No action needed.
- If it persists: check the source's own status page; confirm the relevant API key
  is still valid (`FINNHUB_API_KEY`, `FRED_API_KEY`, `ALPACA_*`). A broken source is
  **isolated** — it never aborts the cycle or the other sources, so the rest of the
  feed keeps running. `/diag` live-probes every source and shows item counts.
- To silence a chronically-broken optional source, toggle its `ENABLE_*` var off.

---

## (d) Railway redeploy

Every merge to the deploy branch auto-redeploys. A redeploy restarts the worker:

1. The **volume persists**, so `DB_PATH` (and `soul.md`) survive — no restore needed.
2. On boot the preflight runs (token, channel, post rights) — watch the logs for ✅.
3. The relay session is re-materialised from `TELEGRAM_SESSION_B64` if needed.
4. No cold-start flood: `seen` is intact on the volume, so only new events fire.

If a redeploy comes up on an **empty** volume (misconfigured mount), treat it as
case (a) and restore from the latest backup.

---

## Kill-and-restore test (verify before relying on backups)

1. `/status` — note the current event count and that `seen` has rows.
2. Trigger a backup out-of-band (or wait for the nightly DM), download the `.gz`.
3. Stop the service.
4. `python scripts/restore_db.py <backup>.db.gz --out /tmp/restored.db` → confirm it
   prints `seen table intact: N row(s)`.
5. Point `DB_PATH` at the restored file (or restore over the volume) and start.
6. Confirm **zero duplicate alerts** in the first few minutes — the cold-start prime
   must suppress the backlog because `seen` is intact.
