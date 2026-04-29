# ТЗ: Telegram-бот для администрации школы на базе Dahua DSS

## Контекст

- Школа, ~500–1500 человек/день, 1 здание
- 2 КПП с турникетами + лифт с распознаванием лица
- **Dahua DSS Pro V8.4.0** (DSS7016D-DR-S2)
- Main Server: **192.168.30.20** (Running)
- 17 дверей: КП1/КП2 Вход/Выход турникет 1–4, Face4Elevator
- Разработчик: опыт Python есть, ОС Windows, бот на этом же ПК

## Цель

Telegram-бот, который:
1. В реальном времени шлёт админам **только важные** события
2. По расписанию шлёт **сводки** (8:45 / 13:00 / 18:00)
3. Отвечает на **/команды** (кто в школе, найти человека, события по двери)

## Архитектура

```
DSS 192.168.30.20 (HTTPS API)
       │  long-polling
       ▼
Python-сервис (Windows ПК)
   ├── DSSClient: login, keepalive, подписка
   ├── SQLite: все события
   ├── Importance-фильтр
   └── aiogram bot
       ├── чат "Тревоги"  ← real-time
       ├── чат "Сводки"   ← cron
       └── /команды
```

## Стек

Python 3.12 / asyncio / aiogram 3.x / aiohttp / aiosqlite / apscheduler / python-dotenv / loguru

## Структура

```
school-bot/
├── .env / .env.example / .gitignore / requirements.txt / README.md
├── config.py
├── main.py
├── db.py
├── dss/      __init__.py, client.py, events.py, models.py
├── bot/      __init__.py, handlers.py, filters.py, formatters.py
├── pipeline/ __init__.py, importance.py, dispatcher.py
├── reports/  __init__.py, daily.py
└── tests/    test_importance.py
```

## .env

```
DSS_HOST=https://192.168.30.20
DSS_PORT=443
DSS_USER=tg_bot
DSS_PASS=...

TG_TOKEN=...
TG_ADMIN_IDS=123,456
TG_CHAT_ALERTS=-100...
TG_CHAT_REPORTS=-100...

DB_PATH=./bot.db
LOG_LEVEL=INFO
TZ=Asia/Dushanbe
```

## БД

```sql
CREATE TABLE events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dss_event_id  TEXT UNIQUE,
    event_type    TEXT,
    event_name    TEXT,
    person_id     TEXT,
    person_name   TEXT,
    door_id       TEXT,
    door_name     TEXT,
    direction     TEXT,
    occurred_at   DATETIME,
    raw_json      TEXT,
    importance    INTEGER DEFAULT 0,
    sent_to_tg    INTEGER DEFAULT 0
);
CREATE INDEX idx_events_occurred ON events(occurred_at);
CREATE INDEX idx_events_person   ON events(person_name);

CREATE TABLE persons_inside (
    person_id   TEXT PRIMARY KEY,
    person_name TEXT,
    entered_at  DATETIME,
    last_door   TEXT
);
```

## Этапы (строго по порядку, после каждого — коммит)

### Этап 1. Каркас
- Папки, requirements.txt, .env.example, .gitignore
- main.py: aiogram с `/start` echo, loguru file+console
- **Проверка:** бот отвечает в личку

### Этап 2. DSSClient — login
- `dss/client.py`: `login()`, `keepalive()`, `request()`
- DSS auth, двухэтапная:
  1. `POST https://192.168.30.20/brms/api/v1.0/accounts/authorize`
     body: `{"userName":"tg_bot", "ipAddress":"...", "clientType":"WINPC_V2"}`
     ответ: `realm`, `randomKey`, `publickey`, `encryptType`
  2. signature = MD5-цепочка по схеме DSS, второй запрос
- Точная формула — из **DSS Open API Reference** (PDF от дилера, ещё не получен)
- Пока документации нет — **TODO + заглушка**, переходим дальше
- `keepalive` каждые 5 мин
- `/dss_ping` → проверка сессии

### Этап 3. Подписка на события
- Long-polling: `/obms/api/v1.1/event/subscription/messages` или аналог
- Парсим → `Event` dataclass → SQLite
- В Telegram пока не шлём
- Запустить на сутки, изучить формат

### Этап 4. Importance-фильтр
`pipeline/importance.py`:
- **Тревога (2):** forced_open, door_held_open, anti_passback, неизвестное лицо вне рабочих часов
- **Важное (1):** проход вне расписания, опоздание (>8:30 для младших), проход после 19:00
- **Обычное (0):** штатный вход/выход
- Юнит-тесты

### Этап 5. Telegram-уведомления
- importance ≥ 1 → `TG_CHAT_ALERTS`, с фото из snapshot если есть
- Шаблоны в `bot/formatters.py`
- Дедупликация через `sent_to_tg`
- Rate limit ≤20/мин в чат

### Этап 6. Сводки (apscheduler)
- 08:45 — пришло X из Y, опоздавших Z
- 13:00 — в школе X
- 18:00 — ушло X, не отметились Y

### Этап 7. /команды (только TG_ADMIN_IDS)
- `/inside` — кто сейчас в школе
- `/find <ФИО>` — последние 10 событий
- `/today` — сводка за сегодня
- `/door <название>` — события по двери
- `/health` — uptime, последний event_id

### Этап 8. 24/7 на Windows
- nssm → Windows-сервис, автостарт
- Watchdog: проверка живости каждые 5 мин
- Auto-restart при падении
- В Windows: «не уходить в сон»

## Жёсткие требования

- Секреты только в `.env`, `.gitignore` обязательно с `.env`, `bot.db`, `*.log`
- Всё async, никаких `requests`/`time.sleep`
- `ssl=False` **только для DSS**
- Логи без полных ФИО (маскировать «Иван П.»); полные имена только в БД и в Telegram
- DSS-цикл: при исключении не падаем, ждём 5 сек, переподключаемся
- Каждый `bot.send_message` в try/except
- ≤20 сообщений/мин в один чат

## Что я (разработчик) передаю Claude Code

1. ✅ IP DSS: `192.168.30.20`
2. 🔲 Логин/пароль `tg_bot` (создать в DSS → Home → 🔧 → User)
3. 🔲 `TG_TOKEN`, `TG_CHAT_ALERTS`, `TG_CHAT_REPORTS`, `TG_ADMIN_IDS`
4. 🔲 **DSS Open API Reference V8.4.0** PDF (для login и event subscription)

## Команда для Claude Code в VS Code

> Прочитай TZ.md и начни с **Этапа 1**. После каждого этапа жди от меня «ок, дальше».
> Если для этапа нужны данные из .env, которых ещё нет — поставь TODO и продолжи.
> На Этапе 2 (DSSClient) IP уже известен: `192.168.30.20`, остальное из .env.
