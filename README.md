# School-bot

Telegram-бот для администрации школы поверх **Dahua DSS Pro V8.4.0**.

## Возможности

- Real-time тревоги в чат админов (forced_open, anti_passback, неизвестное лицо вне рабочих часов и т.п.)
- Сводки по расписанию: 08:45 / 13:00 / 18:00
- Команды: `/inside`, `/find <ФИО>`, `/today`, `/door <название>`, `/health`, `/dss_ping`

## Стек

Python 3.12 · asyncio · aiogram 3.x · aiohttp · aiosqlite · APScheduler · loguru

## Быстрый старт

```bash
# 1. виртуальное окружение
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. конфиг
copy .env.example .env
notepad .env   # заполнить TODO

# 3. запуск
python main.py
```

Тесты:

```bash
pytest -v tests/
```

## Что нужно заполнить в `.env`

| Переменная | Где взять |
|---|---|
| `DSS_USER` / `DSS_PASS` | DSS Pro → Home → 🔧 → User → создать `tg_bot` |
| `TG_TOKEN` | @BotFather |
| `TG_ADMIN_IDS` | @userinfobot или `getUpdates` |
| `TG_CHAT_ALERTS`, `TG_CHAT_REPORTS` | id чатов (отрицательные для групп) |

## TODO, требующие DSS Open API Reference V8.4.0

После получения PDF сверить и поправить:

- [`dss/client.py`](dss/client.py) — точная схема MD5 в `login()`, имя заголовка авторизации, эндпоинт `keepalive`
- [`dss/events.py`](dss/events.py) — пути `subscribe`/`poll`/`unsubscribe`, имена полей в `parse_event`, формат `direction`

Все TODO помечены `TODO(API_GUIDE)`.

## Production на Windows (Этап 8)

```bat
:: От Администратора
service\install_nssm.bat
```

Создаёт два сервиса: `SchoolBot` и `SchoolBotWatchdog`. Heartbeat — `logs/heartbeat.txt`, обновляется раз в минуту. Если файл устаревает > 10 мин — watchdog перезапускает сервис.

## Логи

`logs/bot.log` (10 MB ротация, 14 дней хранения). ФИО маскируются (`Иван И.`); полные имена — только в БД и Telegram.

## Безопасность

- `ssl=False` ставится **только** для DSS (самоподписанный сертификат)
- Все секреты — в `.env`, который в `.gitignore`
- Команды бота вне `/start` доступны только админам из `TG_ADMIN_IDS`
- Telegram rate-limit: ≤20 сообщений/мин в один чат
