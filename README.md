# WorkoutJournal Telegram Mini App

Standalone Python 3.11+ Telegram Mini App for Bothost. The server uses only the
Python standard library; there is no dependency on the Windows desktop app or
its Python bytecode.

## Bothost settings

- Platform: Telegram
- Runtime: Python 3.11 (or newer)
- Use domain: enabled
- Internal web port: `3000`
- Main file: `server.py`
- Custom Dockerfile: disabled
- Bot token: enter it in Bothost's protected **Bot Token** field. Bothost exposes
  it to the process as `TELEGRAM_BOT_TOKEN`.
- Environment variable: `WORKOUT_DATA_DIR=/app/data`
- Add `BACKUP_CHAT_ID=<administrator Telegram ID>` (set the administrator's numeric Telegram ID in Bothost; do not commit it to GitHub).
- Optional: `BACKUP_INTERVAL_HOURS=24` controls the backup interval in hours.

Bothost provides the public domain through `DOMAIN`. The server uses it to set
the global Mini App menu button and to send a launch button on `/start`. If the
platform does not provide `DOMAIN`, set `APP_URL` to the generated HTTPS URL in
the bot's environment settings and restart the bot.

## Data and privacy

Each Telegram account receives its own SQLite database and settings file under
`/app/data/telegram_users/<telegram-id>/`. The backend validates Telegram's
signed `initData` on every API request. The data directory is persistent on
Bothost and must not be committed to Git.

Workout programs, editable profile weight, weight-trend periods, and previous
session summaries are stored per account. The Telegram menu uses its default
button; `/start` sends the Mini App launch button in chat.

The app's initial deployment starts with an empty database. Existing local
Windows workout history is not included in this repository.

## Backups

When `BACKUP_CHAT_ID` is set, the server sends a ZIP backup of `/app/data` to
that private Telegram chat at startup and every 24 hours. Failed sends retry
after 15 minutes. The administrator can request a copy at any time by sending
`/backup` to the bot. The temporary archive is deleted from the container
after sending. Backups contain user profiles and workout records, so set the
chat ID to a private administrator account only. GitHub never stores database
files or backup archives.

## Local run

Set `TELEGRAM_BOT_TOKEN` and `APP_URL` in the environment, then run:

```sh
python server.py
```

The server listens on `0.0.0.0:$PORT` (`3000` by default). Do not enable
`TELEGRAM_MINIAPP_DEV` on a public deployment.
