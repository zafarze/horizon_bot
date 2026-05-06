# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Telegram bot (aiogram 3.x, asyncio) layered over **Dahua DSS Pro V8.4.0** access-control. Sends real-time alerts, scheduled summaries (08:45 / 13:00 / 18:00 local TZ), and answers admin commands about who is in the school. Single Python service, runs on Windows alongside DSS Main Server. Production wrap: NSSM service `SchoolBot` + watchdog `SchoolBotWatchdog`.

## Commands

```powershell
# venv + install
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# config
copy .env.example .env   # then fill TODO_* values

# run
python main.py

# tests
pytest -v tests/
pytest tests/test_importance.py::test_name   # single test
python tools/check_env.py                     # validate .env without booting

# install as Windows service (Admin shell)
service\install_nssm.bat
```

No linter / formatter is wired up in the repo. Don't introduce one without asking.

## Architecture

```
DSS Pro 192.168.30.20 (HTTPS, self-signed)
        │ pull (page-fetch, ~5s interval, 60s overlap)
        ▼
 dss/client.py  ──── DSSClient (login + 45s keepalive, MD5 auth)
 dss/events.py ──── EventSubscriber.stream() → Event
        │
        ▼
 pipeline/dispatcher.py ──── classify → write SQLite → notify?
        │                     (importance.py: 0 normal / 1 pass / 2 alarm)
        ├──► db.py (aiosqlite, schema auto-created on connect)
        └──► bot/notifier.py ──► Telegram chat_alerts
                                  (rate-limited, retries via events.sent_to_tg flag)

 reports/daily.py ── APScheduler cron → bot/formatters → chat_reports

 bot/handlers.py ── aiogram Router (admin-only via bot/filters.AdminFilter)
                    FSM wizards (search, Excel up/down, add-teacher)
                    DSSPersonClient (dss/persons.py) for live person/group lookups
```

Two **separate** DSSClient instances are deliberately created in `main.py`:
- One inside `dss_loop()` (`client_type="WINPC_V2"`) for the event-fetch loop.
- `health_dss` (`client_type="WEB"`) for handler-driven calls (`/dss_groups`, the add-teacher wizard, group sync).

DSS sessions are keyed by `(user, ip, clientType)` — using the same clientType causes `code=2004 "user has logged in"` and the second login is rejected. Do not collapse them.

### Concurrency model

`main.py::main()` builds an `asyncio.gather` of these tasks and lets them run forever:
- `tg_dispatcher.start_polling(bot)` — Telegram updates.
- `heartbeat_loop()` — touches `logs/heartbeat.txt` every 60s for the watchdog (>10 min stale → restart).
- `dss_loop()` — DSS connect → login → subscribe → dispatch; auto-reconnects with backoff. Catches `DSSAuthFatal` and exits the loop (bot keeps serving Telegram); catches `DSSSessionConflict` and sleeps 75s for the prior session to expire on DSS.
- `auto_sync_loop()` (only if `DSS_AUTO_SYNC_GROUPS` set) — mirrors DSS person-groups → local `person_groups` table, which feeds `TG_CHAT_GROUP_FILTERS`. Skips first 90s (`INITIAL_DELAY`) to avoid colliding with the lingering prior WEB session.
- `drain_loop()` — every 30s flushes events with `sent_to_tg=0` (re-sends after Telegram outages).

### Importance / freshness

`pipeline/dispatcher.py` only notifies on:
- Any importance ≥ 1 event (regardless of age), OR
- Importance 0 events newer than `NORMAL_EVENT_FRESHNESS_SEC` (300s).

The 24h backfill at startup deliberately does **not** flood Telegram — only fresh ones notify. `NOTIFY_IGNORE_DEVICES` substring-matches device names that should be persisted but never notified (e.g. lift readers).

### i18n

Three languages (RU/EN/TG) via `bot/i18n.py`. Each handler receives `lang` from `bot/middleware.LangMiddleware`, which reads `user_lang` (SQLite) with fallback to Telegram client locale. Always pass `lang` through to formatters; never hardcode user-visible strings.

### Names / PII

`bot/formatters.py` masks names in logs (`Иван И.`); full names live only in DB and Telegram messages. Preserve this when adding logging.

## Conventions worth preserving

- `config.py` is the **only** place env vars are read. New settings → add to `AppConfig`, parse in `load_config()`, document in `.env.example`. Use `_required` for hard requirements, `_optional(default)` for the rest. `TODO`-prefixed values are treated as unset.
- DB schema lives as a single `SCHEMA` string in `db.py` and is applied with `CREATE ... IF NOT EXISTS` on every connect — there is no migration framework. Schema changes that aren't additive need a manual migration path.
- aiohttp uses `ssl=False` **only** for DSS (self-signed cert). Don't propagate that elsewhere.
- DSS endpoint paths and response field names were validated against a real V8 instance (see comments in `dss/client.py` / `dss/events.py`). Items still marked `TODO(API_GUIDE)` are awaiting confirmation against the official Open API Reference PDF — flag uncertainty rather than guessing.
- Telegram messages are split via `_split_long` at 4000 chars (not 4096) to leave headroom for parse-mode escapes.
