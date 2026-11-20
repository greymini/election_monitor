# Project Pulse — runbook

Operational steps for the field modules (expense ledger, ground reports). Commands are run from the
repo root with the virtualenv active (`.venv\Scripts\activate` on Windows, `. .venv/bin/activate`
elsewhere). Nothing here prints a secret; never paste one into a ticket or chat.

Before going live, and after any change to the host, run:

```
python -m scripts.preflight
```

It checks secrets, migrations, the media directory, the worker and the webhook. Each check prints
`OK`, `WARN` or `FAIL`. Exit code 1 means something failed, so do not go live.

---

## 1. Run locally

Windows (PowerShell):

```
python -m venv .venv
.venv\Scripts\activate
pip install -c constraints.txt -e ".[dev]"
copy .env.example .env          # then fill in real values: see the comments in the file
python -m scripts.dev_stack up --seed-team      # embedded PostgreSQL, migrations, demo team
```

macOS / Linux:

```
python3.11 -m venv .venv
source .venv/bin/activate
pip install -c constraints.txt -e ".[dev]"
cp .env.example .env
python -m scripts.dev_stack up --seed-team
```

Then run three processes, each in its own terminal:

| Process | Command | What it does |
|---|---|---|
| API | `uvicorn pulse.api.main:create_app --factory --port 8000` | team, ledger, reports, settings, `/health`, webhook |
| Bot (polling) | `python -m scripts.run_polling` | receives Telegram updates by long polling |
| Worker | `python -m scripts.run_worker` | media downloads, the outbox, auto-file, escalation, digests, reminders, chat cleanup, media retention |

- Bearer token for the API: `python -m scripts.dev_token usr-principal`.
- `python -m scripts.dev_stack status` shows the database. `down` stops it.
- The database port can change between restarts. `up` rewrites `DATABASE_URL` in `.env`.
- Media goes to `MEDIA_DIR`. The default is `%LOCALAPPDATA%\pulse\media` on Windows and
  `~/.local/share/pulse/media` elsewhere, always outside the repo.

## 2. Switch from polling to webhook

Telegram delivers updates one way at a time. While a webhook is set, `getUpdates` fails, and
`run_polling` refuses to start and says so.

1. Expose the API over **https** at a stable URL whose path ends in `/telegram/webhook`.
2. In `.env`, set `BOT_MODE=webhook` and `WEBHOOK_URL=https://<host>/telegram/webhook`.
3. Stop `run_polling`.
4. Register the webhook (§3).
5. Run `python -m scripts.preflight`. The `webhook` line must say "set and matching".

To go back to polling, run `python -m scripts.webhook delete`, set `BOT_MODE=polling`, then start
`run_polling`.

The dispatcher is the same in both modes. Each update is recorded by `update_id`, so a duplicate
delivered during the switch is ignored.

## 3. Register the webhook with the secret

```
python -m scripts.webhook set            # uses WEBHOOK_URL and TELEGRAM_WEBHOOK_SECRET from .env
python -m scripts.webhook info           # url, whether it matches, pending updates, last delivery error
```

- Telegram sends `TELEGRAM_WEBHOOK_SECRET` in the `X-Telegram-Bot-Api-Secret-Token` header on every
  call. The API answers 403 to anything else.
- `--drop-pending` discards updates Telegram is holding. Use it only when those updates are known
  to be junk: the people who sent them would lose what they sent.
- To rotate the webhook secret: put the new value in `.env`, restart the API, then run
  `python -m scripts.webhook set` again. Between the restart and the `set`, Telegram's calls get
  403 and are retried, so nothing is lost.

## 4. Rotate the bot token

Do this when the token may have leaked: a screenshot, a log, a lost laptop.

1. In @BotFather, open the bot and revoke the token (`/revoke`). The old token stops working at once.
2. Put the new token in `.env` (`TELEGRAM_BOT_TOKEN`) or in the host's secret store.
3. Restart the API, the bot (polling) and the worker.
4. In webhook mode, run `python -m scripts.webhook set` again. A webhook is tied to the token.
5. Run `python -m scripts.preflight`.

Links, PINs and data are not affected. Members do not need to re-link.

## 5. Unlink a lost or stolen phone

1. Find the member: `GET /acs/{n}/field/members` (admin token).
2. Unlink: `DELETE /acs/{n}/field/members/{member_id}/link`. The phone's chat stops being
   recognised immediately, and the PIN goes with the link.
3. If the member is still trusted, issue a new link: `POST /acs/{n}/field/members/{member_id}/link-token`.
   Send the deep link to them through a channel you trust. It expires after 24 hours.
4. They set a new PIN from the bot menu (पिन सेट करें) before their next large entry.

The bot deletes its own money cards after `chat_cleanup_hours` (default 24). Telegram allows this
only within 48 hours of sending (rule from the Bot API documentation; not verified from this network,
see §9). Anything older stays on the old phone. Tell the member to clear the chat if they get the
phone back.

## 6. Reset or unlock a PIN

A member who types a wrong PIN three times is locked out of every money action in the bot. The web
app is not affected. Locks, unlocks and resets are recorded in `field.admin_audit`.

- **Unlock**, keeping the PIN (they mistyped): `POST /acs/{n}/field/members/{member_id}/pin/unlock`.
- **Reset** (they forgot it, or the phone may be compromised):
  `POST /acs/{n}/field/members/{member_id}/pin/reset`. This removes the PIN and lifts the lock; they
  set a new one from the bot menu.

Both need an admin token for that AC. Confirm the person's identity outside Telegram first. Anyone
holding the phone can ask for a reset.

## 7. Restore from backup

What to back up:

- the `field` schema: `pg_dump --schema=field --format=custom -f pulse-field.dump "$DATABASE_URL"`
- `MEDIA_DIR`: files are named by content hash and never change, so a plain file copy or rsync works

To restore:

1. Stop the API, the bot and the worker.
2. Restore the schema into an empty database: `pg_restore --no-owner -d "$DATABASE_URL" pulse-field.dump`.
3. Copy `MEDIA_DIR` back. A missing file shows as 404 on that one item; the report itself is intact.
4. Run `python -m pulse.migrate`. This applies anything newer than the backup and refuses if a
   migration file was edited.
5. Run `python -m scripts.preflight`, then start the worker, the API and the bot.

After a restore:

- Updates Telegram delivered after the backup was taken are lost, unless they are still pending
  (polling mode, or webhook mode with Telegram still retrying).
- Outbox rows still `queued` are sent when the worker starts. Escalation sends only the latest
  overdue round, not every round missed.

## 8. When alerts stop arriving

If Telegram is unreachable or rate-limiting, the bot still saves everything members send.
- Replies that could not go out wait in `field.alert_message` with `kind = 'reply'` and `status = 'queued'`.
- They are sent, in order per chat, once Telegram answers again; the worker must be running.
- Each says the entry was already saved, so members do not send it twice.

To count what is waiting:

```
SELECT count(*) FROM field.alert_message WHERE kind = 'reply' AND status = 'queued';
```

### Checks

Work down this list:

1. `GET /health`.
   - `503`: the database is down. Fix that first.
   - `"status": "degraded"`: the worker has not finished a run for 5 minutes. Check the worker
     process and its log.
2. Look at `queue` in the `/health` response.
   - `ready` growing: the worker is running but falling behind or failing. Look at `failed_24h` and
     at `field.job` rows with `status = 'failed'` (`last_error`).
   - `waiting` high: Telegram has rate-limited us (429). The worker resumes after `retry_after`.
3. `python -m scripts.preflight`. Check the `webhook` line in particular. In webhook mode,
   `info` shows Telegram's last delivery error (for example a certificate problem or a timeout).
4. Look in `field.alert_message` for the recipient: `status` shows `no_link` (member not linked),
   `blocked` (they blocked the bot) or `failed` (see `last_error`).
5. Check `field.alert_rule` for that trigger: `active` must be true, and the recipients must
   resolve to linked members (`fallback` is used when they resolve to nobody).
6. Check that the report was actually filed (`field.report.status <> 'draft'`). Drafts are filed by
   `report_autofile` after `autofile_minutes`. The AC may have an override:
   `GET /acs/{n}/field/settings`.

## 9. Telegram rules this service relies on

These come from the Bot API documentation. They could not be re-checked from this network because
core.telegram.org is blocked, so confirm them before go-live.

- `deleteMessage`: a bot can delete its own messages, and incoming messages in private chats, but
  only within 48 hours. PIN entries are deleted as soon as they are read. Chat cleanup never tries
  anything older than 48 hours.
- `getFile` downloads are limited to 20 MB (`TG_MAX_DOWNLOAD_BYTES`).
- A webhook and `getUpdates` cannot be used at the same time.
- `setWebhook` `secret_token` is sent back in the `X-Telegram-Bot-Api-Secret-Token` header.

## 10. Turn on speech-to-text

Voice notes stay audio-only while `ASR_ENGINE=none`, which is the default.

1. Add the engine as a module, `pulse/asr/<name>.py`, exposing `make()`. See `pulse/asr/base.py`.
2. Install ffmpeg on the worker host. Without it audio goes to the engine unconverted (not 16 kHz
   mono), and each transcript's `converted` column says so.
3. Set `ASR_ENGINE=<name>` in `.env` and restart the worker.
4. Run `python -m scripts.preflight`. The `asr` line must be OK; `fake` is a test engine and gives
   a warning.

Only voice notes stored after the switch are transcribed. Transcripts are masked for phone,
Aadhaar and voter-id numbers before they are stored. A failed transcription leaves the report and
its audio unchanged: see `field.report_transcript.error`.
