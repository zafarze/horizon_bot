"""Локализация бота: RU / EN / TJ.

Источник истины для всех UI-строк. Все хендлеры/форматтеры/отчёты
берут текст отсюда через `t(key, lang, **kwargs)`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

LANGS: tuple[str, ...] = ("ru", "en", "tj")
DEFAULT_LANG = "ru"


def normalize_lang(lang: str | None) -> str:
    if lang in LANGS:
        return lang  # type: ignore[return-value]
    return DEFAULT_LANG


# --- Лейблы кнопок persistent-клавиатуры ---
# Ключи завязаны на хендлеры: при правке текста — править ТОЛЬКО здесь.

BTN_LABELS: dict[str, dict[str, str]] = {
    "today":   {"ru": "📅 Сегодня",     "en": "📅 Today",       "tj": "📅 Имрӯз"},
    "late":    {"ru": "🔴 Опоздал",      "en": "🔴 Late",        "tj": "🔴 Дермонда"},
    "absent":  {"ru": "❌ Не пришли",     "en": "❌ Absent",       "tj": "❌ Наомадагон"},
    "find":    {"ru": "🔍 Найти",        "en": "🔍 Find",         "tj": "🔍 Ҷустуҷӯ"},
    "report":  {"ru": "📊 Мониторинг",   "en": "📊 Monitoring",   "tj": "📊 Мониторинг"},
    "workers": {"ru": "👥 Работники",    "en": "👥 Workers",      "tj": "👥 Кормандон"},
    # Длинная кнопка снизу клавиатуры. Открывает выбор языка inline-кнопками.
    "lang_btn": {"ru": "🌐 Сменить язык",
                 "en": "🌐 Change language",
                 "tj": "🌐 Тағйири забон"},
}


def label(key: str, lang: str) -> str:
    return BTN_LABELS[key][normalize_lang(lang)]


def labels_for(key: str) -> set[str]:
    """Все языковые варианты лейбла одной кнопки — для F.text.in_(...)."""
    return set(BTN_LABELS[key].values())


def lang_button_label(lang: str) -> str:
    """Лейбл кнопки «🌐 Сменить язык» на текущем языке."""
    return label("lang_btn", lang)


def all_lang_button_labels() -> set[str]:
    """Все три перевода кнопки «Сменить язык» — для роутинга F.text.in_()."""
    return labels_for("lang_btn")


# Подписи под inline-кнопками выбора языка — на родном языке каждой.
LANG_PICK_LABEL: dict[str, str] = {
    "ru": "Русский 🇷🇺",
    "en": "English 🇬🇧",
    "tj": "Тоҷикӣ 🇹🇯",
}


# --- Все остальные строки UI ---

T: dict[str, dict[str, str]] = {
    # === Меню ===
    "menu_text": {
        "ru": "<b>🏫 Школьный турникет-бот</b>\n\nИспользуйте кнопки внизу экрана.",
        "en": "<b>🏫 School turnstile bot</b>\n\nUse the buttons at the bottom.",
        "tj": "<b>🏫 Боти турникети мактаб</b>\n\nТугмаҳои поёни экранро истифода баред.",
    },
    "menu_inside":   {"ru": "📊 Сейчас в школе", "en": "📊 In school now",   "tj": "📊 Ҳозир дар мактаб"},
    "menu_today":    {"ru": "📅 Сегодня",         "en": "📅 Today",            "tj": "📅 Имрӯз"},
    "menu_attend":   {"ru": "📋 Табель",          "en": "📋 Attendance",       "tj": "📋 Ҳозирӣ"},
    "menu_find":     {"ru": "🔍 Найти",           "en": "🔍 Find",             "tj": "🔍 Ҷустуҷӯ"},
    "menu_door":     {"ru": "🚪 По двери",        "en": "🚪 By door",          "tj": "🚪 Аз рӯи дар"},
    "menu_back":     {"ru": "🔙 Меню",            "en": "🔙 Menu",             "tj": "🔙 Меню"},
    "menu_cancel":   {"ru": "❌ Отмена",           "en": "❌ Cancel",            "tj": "❌ Бекор"},

    # === Языковая кнопка ===
    "lang_choose_prompt": {
        "ru": "Выберите язык / Забонро интихоб кунед / Select language:",
        "en": "Select language / Выберите язык / Забонро интихоб кунед:",
        "tj": "Забонро интихоб кунед / Выберите язык / Select language:",
    },
    "lang_changed": {
        "ru": "✅ Язык переключён: Русский 🇷🇺",
        "en": "✅ Language switched: English 🇬🇧",
        "tj": "✅ Забон иваз шуд: Тоҷикӣ 🇹🇯",
    },

    # === Доступ ===
    "access_admins_only": {
        "ru": "Доступ только для админов",
        "en": "Admins only",
        "tj": "Танҳо барои админҳо",
    },

    # === FSM запросы ===
    "ask_find_name": {
        "ru": "🔍 Введите имя или фамилию для поиска:",
        "en": "🔍 Enter a first or last name to search:",
        "tj": "🔍 Барои ҷустуҷӯ ном ё насабро ворид кунед:",
    },
    "ask_door_name": {
        "ru": "🚪 Введите имя двери (например <code>КП1</code>):",
        "en": "🚪 Enter a door name (e.g. <code>КП1</code>):",
        "tj": "🚪 Номи дарро ворид кунед (масалан <code>КП1</code>):",
    },
    "empty_query":  {"ru": "Пустой запрос",       "en": "Empty query",         "tj": "Дархости холӣ"},
    "empty_door":   {"ru": "Пустое имя двери",     "en": "Empty door name",     "tj": "Номи дар холӣ"},

    # === Отчёт ===
    "report_from_q": {
        "ru": "📊 <b>Отчёт за период</b>\n\nС какого числа?\n\n",
        "en": "📊 <b>Report for period</b>\n\nFrom which date?\n\n",
        "tj": "📊 <b>Ҳисобот барои давра</b>\n\nАз кадом сана?\n\n",
    },
    "report_to_q": {
        "ru": "✓ С: <b>{d}</b>\n\nДо какого числа?\n\n",
        "en": "✓ From: <b>{d}</b>\n\nUntil which date?\n\n",
        "tj": "✓ Аз: <b>{d}</b>\n\nТо кадом сана?\n\n",
    },
    "date_hint": {
        "ru": "<i>Форматы: <code>01.04</code>, <code>01.04.2026</code>, "
              "<code>сегодня</code>, <code>вчера</code>, <code>неделя</code>, "
              "<code>месяц</code>.\nОтмена — нажмите любую другую кнопку.</i>",
        "en": "<i>Formats: <code>01.04</code>, <code>01.04.2026</code>, "
              "<code>today</code>, <code>yesterday</code>, <code>week</code>, "
              "<code>month</code>.\nCancel — press any other button.</i>",
        "tj": "<i>Форматҳо: <code>01.04</code>, <code>01.04.2026</code>, "
              "<code>имрӯз</code>, <code>дирӯз</code>, <code>ҳафта</code>, "
              "<code>моҳ</code>.\nБекор — тугмаи дигарро пахш кунед.</i>",
    },
    "date_unparsed": {
        "ru": "Не понял дату. Попробуйте ещё раз.\n\n",
        "en": "Couldn't parse the date. Try again.\n\n",
        "tj": "Сана фаҳмида нашуд. Боз кӯшиш кунед.\n\n",
    },
    "report_state_lost": {
        "ru": "Что-то пошло не так. Нажмите 📊 Отчёт ещё раз.",
        "en": "Something went wrong. Press 📊 Report again.",
        "tj": "Хатогӣ рух дод. 📊 Ҳисоботро дубора пахш кунед.",
    },
    "report_preparing": {
        "ru": "⏳ Готовлю отчёт <b>{a}–{b}</b>…",
        "en": "⏳ Preparing report <b>{a}–{b}</b>…",
        "tj": "⏳ Ҳисобот <b>{a}–{b}</b> омода мешавад…",
    },
    "report_no_data": {
        "ru": "За выбранный период данных не найдено.",
        "en": "No data found for the selected period.",
        "tj": "Барои давраи интихобшуда маълумот ёфт нашуд.",
    },
    "report_caption": {
        "ru": "📊 Отчёт {a}–{b}\nЗаписей: {n}",
        "en": "📊 Report {a}–{b}\nRecords: {n}",
        "tj": "📊 Ҳисобот {a}–{b}\nСабтҳо: {n}",
    },

    # === /find slash ===
    "usage_find": {"ru": "Использование: /find Иванов", "en": "Usage: /find Smith",      "tj": "Истифода: /find Алӣ"},
    "usage_door": {"ru": "Использование: /door КП1",    "en": "Usage: /door КП1",          "tj": "Истифода: /door КП1"},
    "find_done":  {"ru": "⤴️ Готово",                    "en": "⤴️ Done",                   "tj": "⤴️ Тайёр"},

    # === /photo ===
    "no_photo_events": {
        "ru": "В БД пока нет событий с фото.",
        "en": "No events with photos in the database yet.",
        "tj": "Дар базаи маълумот то ҳол рӯйдоди акс надорад.",
    },
    "photo_dl_fail": {
        "ru": "Не удалось скачать фото из DSS: {e}\n\nURL:\n{url}",
        "en": "Failed to download photo from DSS: {e}\n\nURL:\n{url}",
        "tj": "Боргирии акс аз DSS муяссар нашуд: {e}\n\nURL:\n{url}",
    },
    "photo_tg_reject": {
        "ru": "Скачали ({n} байт), но Telegram отверг: {e}",
        "en": "Downloaded ({n} bytes) but Telegram rejected: {e}",
        "tj": "Боргирӣ шуд ({n} байт), вале Telegram қабул накард: {e}",
    },

    # === DSS / health ===
    "dss_ok":         {"ru": "DSS: OK ✅",            "en": "DSS: OK ✅",                "tj": "DSS: OK ✅"},
    "dss_no_session": {"ru": "DSS: нет сессии ❌",     "en": "DSS: no session ❌",         "tj": "DSS: сессия нест ❌"},
    "dss_error":      {"ru": "DSS error: {e}",        "en": "DSS error: {e}",            "tj": "Хатои DSS: {e}"},
    "checking":       {"ru": "Проверяю...",            "en": "Checking...",               "tj": "Тафтиш мекунам..."},
    "calculating":    {"ru": "Считаю...",              "en": "Calculating...",            "tj": "Ҳисоб мекунам..."},

    # === Форматтеры — общие ===
    "unknown_person": {"ru": "Неизвестный",            "en": "Unknown",                   "tj": "Номаълум"},
    "dash":           {"ru": "—",                       "en": "—",                          "tj": "—"},
    "dir_in":         {"ru": "вход",                    "en": "in",                         "tj": "даромад"},
    "dir_out":        {"ru": "выход",                   "en": "out",                        "tj": "баромад"},
    "still_at_school":{"ru": "ещё в школе",             "en": "still at school",            "tj": "ҳоло дар мактаб"},

    # Тип события (когда DSS не прислал alarmTypeName)
    "event_pass_granted":   {"ru": "Проход",                "en": "Pass",                       "tj": "Гузаштан"},
    "event_pass_denied":    {"ru": "Отказ в проходе",        "en": "Pass denied",                "tj": "Радди гузариш"},
    "event_door_forced":    {"ru": "Дверь вскрыта",          "en": "Door forced open",           "tj": "Дар маҷбуран кушода"},
    "event_door_held":      {"ru": "Дверь долго открыта",    "en": "Door held open",             "tj": "Дар муддати дароз кушода"},
    "event_antipassback":   {"ru": "Anti-passback",          "en": "Anti-passback",              "tj": "Anti-passback"},
    "event_face_stranger":  {"ru": "Неизвестное лицо",       "en": "Unknown face",                "tj": "Чеҳраи номаълум"},
    "event_generic":        {"ru": "Событие {code}",         "en": "Event {code}",                "tj": "Рӯйдод {code}"},
    "event_fallback":       {"ru": "событие",                "en": "event",                       "tj": "рӯйдод"},

    # === format_event статус ===
    "late_by":   {"ru": "⏱ Опоздал на {v}",          "en": "⏱ Late by {v}",             "tj": "⏱ {v} дер монд"},
    "left_early":{"ru": "⏱ Ушёл раньше на {v}",       "en": "⏱ Left early by {v}",        "tj": "⏱ {v} барвақт рафт"},
    "min_short": {"ru": "{n} мин",                    "en": "{n} min",                    "tj": "{n} дақ"},
    "h_short":   {"ru": "{h} ч",                      "en": "{h} h",                      "tj": "{h} соат"},
    "hm_short":  {"ru": "{h} ч {m} мин",              "en": "{h} h {m} min",              "tj": "{h} соат {m} дақ"},

    # === inside list ===
    "inside_empty": {
        "ru": "В школе сейчас никого нет (по данным турникетов).",
        "en": "Nobody in school right now (per turnstile data).",
        "tj": "Ҳозир дар мактаб ҳеҷ кас нест (тибқи маълумоти турникет).",
    },
    "inside_head":  {"ru": "<b>В школе сейчас: {n} чел.</b>\n",
                     "en": "<b>In school now: {n} people</b>\n",
                     "tj": "<b>Ҳозир дар мактаб: {n} нафар</b>\n"},
    "and_more":     {"ru": "\n…и ещё {n}",            "en": "\n…and {n} more",            "tj": "\n…ва боз {n}"},

    # === find results ===
    "find_empty":   {"ru": "По запросу «{q}» ничего не найдено.",
                     "en": "Nothing found for «{q}».",
                     "tj": "Барои дархости «{q}» чизе ёфт нашуд."},
    "prof_head":     {"ru": "<b>👤 Карточка</b>",       "en": "<b>👤 Profile</b>",      "tj": "<b>👤 Профил</b>"},
    "prof_name":     {"ru": "Имя",            "en": "Name",            "tj": "Ном"},
    "prof_id":       {"ru": "DSS ID",         "en": "DSS ID",          "tj": "DSS ID"},
    "prof_groups":   {"ru": "Отдел",          "en": "Department",      "tj": "Шуъба"},
    "prof_position": {"ru": "Должность",      "en": "Position",        "tj": "Вазифа"},
    "prof_subject":  {"ru": "Предмет",        "en": "Subject",         "tj": "Фан"},
    "prof_phone":    {"ru": "Телефон",        "en": "Phone",           "tj": "Телефон"},
    "prof_no_data":  {"ru": "—",              "en": "—",                "tj": "—"},
    "find_head":    {"ru": "<b>Последние события для «{q}»:</b>\n",
                     "en": "<b>Recent events for «{q}»:</b>\n",
                     "tj": "<b>Рӯйдодҳои охирин барои «{q}»:</b>\n"},

    # === door events ===
    "door_empty":   {"ru": "По двери «{d}» событий не найдено.",
                     "en": "No events found for door «{d}».",
                     "tj": "Барои дари «{d}» рӯйдоде ёфт нашуд."},
    "door_head":    {"ru": "<b>События по двери «{d}»:</b>\n",
                     "en": "<b>Events for door «{d}»:</b>\n",
                     "tj": "<b>Рӯйдодҳои дари «{d}»:</b>\n"},

    # === today ===
    "today_head":   {"ru": "<b>Сводка за сегодня</b>",
                     "en": "<b>Today summary</b>",
                     "tj": "<b>Хулосаи имрӯз</b>"},
    "today_total":  {"ru": "Всего проходов: {n}",      "en": "Total passes: {n}",          "tj": "Ҳамагӣ гузариш: {n}"},
    "today_ins":    {"ru": "Входов: {n}",              "en": "Entries: {n}",                "tj": "Даромад: {n}"},
    "today_outs":   {"ru": "Выходов: {n}",             "en": "Exits: {n}",                  "tj": "Баромад: {n}"},
    "today_inside": {"ru": "Сейчас в школе: {n}",      "en": "In school now: {n}",          "tj": "Ҳозир дар мактаб: {n}"},

    # === attendance ===
    "att_empty":    {"ru": "Сегодня пока никто не проходил.",
                     "en": "No-one has come through today yet.",
                     "tj": "То ҳол имрӯз касе нагузаштааст."},
    "att_head":     {"ru": "<b>📋 Табель за сегодня</b>",
                     "en": "<b>📋 Attendance for today</b>",
                     "tj": "<b>📋 Ҳозирии имрӯз</b>"},
    "att_norm":     {"ru": "<i>Норма: {a}–{b}</i>",
                     "en": "<i>Norm: {a}–{b}</i>",
                     "tj": "<i>Меъёр: {a}–{b}</i>"},
    "att_total":    {"ru": "всего: {n}",               "en": "total: {n}",                 "tj": "ҳамагӣ: {n}"},
    "att_on_time":  {"ru": "вовремя: {n}",             "en": "on time: {n}",                "tj": "сари вақт: {n}"},
    "att_still_in": {"ru": "ещё на работе: {n}",       "en": "still at work: {n}",          "tj": "ҳоло дар кор: {n}"},
    "att_after":    {"ru": "пришли вечером: {n}",      "en": "came after hours: {n}",       "tj": "бегоҳ омаданд: {n}"},
    "att_no_dev":   {"ru": "✅ Отклонений нет.",         "en": "✅ No deviations.",            "tj": "✅ Тафовут нест."},
    "att_late_line":{"ru": "🔴 опоздал на {v}",         "en": "🔴 late by {v}",              "tj": "🔴 {v} дер монд"},
    "att_early_line":{"ru": "🟡 ушёл раньше на {v}",   "en": "🟡 left early by {v}",        "tj": "🟡 {v} барвақт рафт"},
    "att_dev_more": {"ru": "\n\n…и ещё {n} с отклонениями",
                     "en": "\n\n…and {n} more with deviations",
                     "tj": "\n\n…ва боз {n} бо тафовут"},

    # === late ===
    "late_empty_today": {
        "ru": "Сегодня ещё никто не проходил.",
        "en": "No-one has come through today yet.",
        "tj": "То ҳол имрӯз касе нагузаштааст.",
    },
    "late_head":    {"ru": "<b>🔴 Опоздавшие сегодня</b>",
                     "en": "<b>🔴 Late today</b>",
                     "tj": "<b>🔴 Дермондагон имрӯз</b>"},
    "late_norm":    {"ru": "<i>Норма прихода: {a}</i>",
                     "en": "<i>Arrival norm: {a}</i>",
                     "tj": "<i>Меъёри омадан: {a}</i>"},
    "late_total":   {"ru": "Всего: {n}",               "en": "Total: {n}",                  "tj": "Ҳамагӣ: {n}"},
    "late_none":    {"ru": "✅ Никто не опоздал.",       "en": "✅ Nobody was late.",          "tj": "✅ Касе дер намонд."},

    # === late: меню периодов и xlsx ===
    "late_pick_period": {
        "ru": "🔴 <b>Опоздавшие</b>\n\nЗа какой период сформировать отчёт?",
        "en": "🔴 <b>Late list</b>\n\nWhich period?",
        "tj": "🔴 <b>Дермондагон</b>\n\nДавраро интихоб кунед:",
    },
    "late_btn_today":  {"ru": "📅 Сегодня",   "en": "📅 Today",     "tj": "📅 Имрӯз"},
    "late_btn_week":   {"ru": "📆 Неделя",    "en": "📆 Week",       "tj": "📆 Ҳафта"},
    "late_btn_month":  {"ru": "🗓 Месяц",     "en": "🗓 Month",      "tj": "🗓 Моҳ"},
    "late_btn_year":   {"ru": "📊 Год",       "en": "📊 Year",       "tj": "📊 Сол"},
    "late_btn_range":  {"ru": "🎯 Период",    "en": "🎯 Custom",     "tj": "🎯 Давра"},
    "late_h_no":       {"ru": "№",            "en": "No.",           "tj": "№"},
    "late_h_summary":  {"ru": "Итог (мин)",   "en": "Total (min)",   "tj": "Ҷамъ (дақ)"},
    "late_h_days":     {"ru": "Дней",         "en": "Days",          "tj": "Рӯзҳо"},
    "late_summary_min": {
        "ru": "опоздал на {n} мин",
        "en": "late by {n} min",
        "tj": "{n} дақ дер монд",
    },
    "late_xlsx_total": {
        "ru": "ИТОГО",
        "en": "TOTAL",
        "tj": "ҲАМАГӢ",
    },
    "late_xlsx_records": {
        "ru": "{n} записей",
        "en": "{n} records",
        "tj": "{n} сабт",
    },
    "late_xlsx_no_data": {
        "ru": "✅ За выбранный период никто не опоздал.",
        "en": "✅ No-one was late in the selected period.",
        "tj": "✅ Дар давраи интихобшуда касе дер намонд.",
    },
    "late_xlsx_caption": {
        "ru": "🔴 Опоздавшие <b>{a}–{b}</b>: {n} записей",
        "en": "🔴 Late <b>{a}–{b}</b>: {n} records",
        "tj": "🔴 Дермондагон <b>{a}–{b}</b>: {n} сабт",
    },
    "late_xlsx_filename": {
        "ru": "опоздавшие",
        "en": "late",
        "tj": "dermondagon",
    },

    # === absent: меню периодов и pivot-xlsx ===
    "absent_pick_period": {
        "ru": "❌ <b>Не пришли</b>\n\nЗа какой период сформировать отчёт?",
        "en": "❌ <b>Absent</b>\n\nWhich period?",
        "tj": "❌ <b>Наомадагон</b>\n\nДавраро интихоб кунед:",
    },
    "absent_h_days": {"ru": "Дней не было", "en": "Days absent", "tj": "Рӯзҳои набуд"},
    "absent_xlsx_no_data": {
        "ru": "✅ За выбранный период все были.",
        "en": "✅ Nobody was absent in the selected period.",
        "tj": "✅ Дар давраи интихобшуда ҳама буданд.",
    },
    "absent_xlsx_caption": {
        "ru": "❌ Не пришли <b>{a}–{b}</b>: {n} чел.",
        "en": "❌ Absent <b>{a}–{b}</b>: {n} people",
        "tj": "❌ Наомадагон <b>{a}–{b}</b>: {n} нафар",
    },
    "absent_xlsx_filename": {
        "ru": "не_пришли",
        "en": "absent",
        "tj": "naomadagon",
    },

    # === monitoring ===
    "mon_pick":      {"ru": "📊 <b>Мониторинг</b>\n\nВыберите раздел:",
                      "en": "📊 <b>Monitoring</b>\n\nPick a section:",
                      "tj": "📊 <b>Мониторинг</b>\n\nБахшро интихоб кунед:"},
    "mon_btn_today":    {"ru": "📈 Сегодня в цифрах", "en": "📈 Today by numbers", "tj": "📈 Имрӯз дар рақамҳо"},
    "mon_btn_trend":    {"ru": "📊 Тренд (30 дней)",  "en": "📊 Trend (30 days)",  "tj": "📊 Раванд (30 рӯз)"},
    "mon_btn_top_late": {"ru": "🏆 Топ опаздывающих", "en": "🏆 Top late",         "tj": "🏆 Беҳтаринҳои дермонда"},
    "mon_btn_range":    {"ru": "📋 Отчёт за период",  "en": "📋 Custom report",    "tj": "📋 Ҳисобот барои давра"},

    # Сегодня в цифрах
    "mon_today_head":   {"ru": "<b>📈 Сегодня — {d}</b>",
                         "en": "<b>📈 Today — {d}</b>",
                         "tj": "<b>📈 Имрӯз — {d}</b>"},
    "mon_today_total":  {"ru": "👥 Регулярных в базе: {n}",
                         "en": "👥 Regulars in base: {n}",
                         "tj": "👥 Доимӣ дар база: {n}"},
    "mon_today_came":   {"ru": "✅ Пришли: <b>{n}</b> ({pct}%)",
                         "en": "✅ Came: <b>{n}</b> ({pct}%)",
                         "tj": "✅ Омаданд: <b>{n}</b> ({pct}%)"},
    "mon_today_late":   {"ru": "🔴 Опоздали: <b>{n}</b>",
                         "en": "🔴 Late: <b>{n}</b>",
                         "tj": "🔴 Дермонданд: <b>{n}</b>"},
    "mon_today_absent": {"ru": "❌ Не пришли: <b>{n}</b>",
                         "en": "❌ Absent: <b>{n}</b>",
                         "tj": "❌ Наомаданд: <b>{n}</b>"},
    "mon_today_inside": {"ru": "🏫 В школе сейчас: <b>{n}</b>",
                         "en": "🏫 In school now: <b>{n}</b>",
                         "tj": "🏫 Ҳозир дар мактаб: <b>{n}</b>"},
    "mon_today_by_dept":{"ru": "\n<b>По отделам:</b>",
                         "en": "\n<b>By department:</b>",
                         "tj": "\n<b>Аз рӯи шуъбаҳо:</b>"},

    # Trend
    "mon_trend_caption":  {"ru": "📊 Тренд за 30 дней",
                           "en": "📊 30-day trend",
                           "tj": "📊 Раванди 30 рӯза"},
    "mon_trend_filename": {"ru": "тренд_30дней", "en": "trend_30d", "tj": "trend_30"},
    "mon_trend_h_date":   {"ru": "Дата", "en": "Date", "tj": "Сана"},
    "mon_trend_h_came":   {"ru": "Пришли", "en": "Came", "tj": "Омаданд"},
    "mon_trend_h_late":   {"ru": "Опоздали", "en": "Late", "tj": "Дермонданд"},
    "mon_trend_h_absent": {"ru": "Не пришли", "en": "Absent", "tj": "Наомаданд"},
    "mon_trend_chart":    {"ru": "Динамика дисциплины", "en": "Discipline dynamics", "tj": "Раванди интизом"},

    # Top late
    "mon_top_caption":  {"ru": "🏆 Топ-{n} опаздывающих за 30 дней",
                         "en": "🏆 Top-{n} late (30 days)",
                         "tj": "🏆 Беҳтарин-{n} дермондагон (30 рӯз)"},
    "mon_top_filename": {"ru": "топ_опаздывающих", "en": "top_late", "tj": "top_late"},
    "mon_top_h_rank":   {"ru": "№",            "en": "Rank",         "tj": "№"},
    "mon_top_h_name":   {"ru": "ФИО",          "en": "Name",         "tj": "ФИО"},
    "mon_top_h_dept":   {"ru": "Отдел",        "en": "Department",   "tj": "Шуъба"},
    "mon_top_h_pos":    {"ru": "Должность",    "en": "Position",     "tj": "Вазифа"},
    "mon_top_h_days":   {"ru": "Дней опоздал", "en": "Late days",    "tj": "Рӯзҳои дер"},
    "mon_top_h_total":  {"ru": "Всего (мин)",  "en": "Total (min)",  "tj": "Ҷамъ (дақ)"},
    "mon_top_h_avg":    {"ru": "В среднем",    "en": "Average",      "tj": "Миёна"},
    "mon_top_no_data":  {"ru": "✅ За 30 дней никто значимо не опаздывал.",
                         "en": "✅ Nobody was significantly late in 30 days.",
                         "tj": "✅ Дар 30 рӯз касе дермонда нашуд."},

    # === absent ===
    "absent_empty": {"ru": "✅ Все, кто бывают в школе, сегодня уже прошли.",
                     "en": "✅ Everyone who normally attends has come today.",
                     "tj": "✅ Ҳамаи онҳое, ки ба мактаб меоянд, имрӯз омаданд."},
    "absent_head":  {"ru": "<b>❌ Не пришли сегодня: {n}</b>\n",
                     "en": "<b>❌ Absent today: {n}</b>\n",
                     "tj": "<b>❌ Имрӯз наомаданд: {n}</b>\n"},
    "absent_last":  {"ru": "<i>в последний раз: {when}</i>",
                     "en": "<i>last seen: {when}</i>",
                     "tj": "<i>бори охир: {when}</i>"},

    # === workers ===
    "workers_empty":{"ru": "В БД пока нет данных о проходах.",
                     "en": "No pass data in the database yet.",
                     "tj": "Дар базаи маълумот то ҳол сабти гузариш нест."},
    "workers_head": {"ru": "<b>👥 Работники (за 30 дней): {n}</b>\n",
                     "en": "<b>👥 Workers (30 days): {n}</b>\n",
                     "tj": "<b>👥 Кормандон (30 рӯз): {n}</b>\n"},
    "workers_line": {"ru": "<i>{p} проходов · последний: {when}</i>",
                     "en": "<i>{p} passes · last: {when}</i>",
                     "tj": "<i>{p} гузариш · охирин: {when}</i>"},

    # === health ===
    "health_head":  {"ru": "<b>Health</b>", "en": "<b>Health</b>", "tj": "<b>Ҳолат</b>"},
    "health_uptime":{"ru": "Uptime: {v}",   "en": "Uptime: {v}",   "tj": "Кор кардан: {v}"},
    "health_dss":   {"ru": "DSS session: {v}", "en": "DSS session: {v}", "tj": "Сессияи DSS: {v}"},
    "health_dss_ok":{"ru": "OK ✅",          "en": "OK ✅",          "tj": "OK ✅"},
    "health_dss_down":{"ru": "DOWN ❌",      "en": "DOWN ❌",        "tj": "DOWN ❌"},
    "health_last":  {"ru": "Last event id: {v}", "en": "Last event id: {v}", "tj": "ID-и рӯйдоди охирин: {v}"},

    # === daily reports ===
    "morning_head": {"ru": "🌅 <b>Утро (08:45)</b>",
                     "en": "🌅 <b>Morning (08:45)</b>",
                     "tj": "🌅 <b>Субҳ (08:45)</b>"},
    "morning_pass": {"ru": "Прошло: {n}", "en": "Came in: {n}", "tj": "Гузаштанд: {n}"},
    "morning_late": {"ru": "Опоздавших (≥08:30): {n}",
                     "en": "Late (≥08:30): {n}",
                     "tj": "Дермондагон (≥08:30): {n}"},
    "midday":       {"ru": "☀️ <b>День (13:00)</b>\nВ школе сейчас: {n}",
                     "en": "☀️ <b>Midday (13:00)</b>\nIn school now: {n}",
                     "tj": "☀️ <b>Нисфирӯзӣ (13:00)</b>\nҲозир дар мактаб: {n}"},
    "evening_head": {"ru": "🌇 <b>Вечер (18:00)</b>",
                     "en": "🌇 <b>Evening (18:00)</b>",
                     "tj": "🌇 <b>Бегоҳ (18:00)</b>"},
    "evening_out":  {"ru": "Вышло за день: {n}",
                     "en": "Exits today: {n}",
                     "tj": "Баромаданд: {n}"},
    "evening_stuck":{"ru": "Не отметились на выход: {n}",
                     "en": "Did not check out: {n}",
                     "tj": "Баромадаро қайд накарданд: {n}"},

    # === CSV отчёт ===
    "csv_h_date":   {"ru": "Дата",             "en": "Date",                "tj": "Сана"},
    "csv_h_name":   {"ru": "Имя",              "en": "Name",                 "tj": "Ном"},
    "csv_h_in":     {"ru": "Первый вход",       "en": "First entry",          "tj": "Даромади аввал"},
    "csv_h_out":    {"ru": "Последний выход",   "en": "Last exit",            "tj": "Баромади охир"},
    "csv_h_late":   {"ru": "Опоздание (мин)",   "en": "Late (min)",           "tj": "Дермонӣ (дақ)"},
    "csv_h_early":  {"ru": "Ранний уход (мин)", "en": "Early leave (min)",    "tj": "Барвақт рафтан (дақ)"},
    "csv_h_passes": {"ru": "Проходов",          "en": "Passes",                "tj": "Гузаришҳо"},
    "csv_h_status": {"ru": "Статус",            "en": "Status",                "tj": "Ҳолат"},
    "csv_st_only_out":   {"ru": "только выход",            "en": "only exit",            "tj": "танҳо баромад"},
    "csv_st_after_hours":{"ru": "пришёл после рабочего дня", "en": "came after hours",   "tj": "пас аз рӯзи корӣ омад"},
    "csv_st_late":       {"ru": "опоздал",                  "en": "late",                  "tj": "дер монд"},
    "csv_st_no_out":     {"ru": "не отметился на выход",     "en": "did not check out",    "tj": "баромадаро қайд накард"},
    "csv_st_early":      {"ru": "ушёл раньше",              "en": "left early",           "tj": "барвақт рафт"},
    "csv_st_on_time":    {"ru": "вовремя",                  "en": "on time",              "tj": "сари вақт"},

    # === Workers CSV ===
    "csv_w_name":      {"ru": "Имя",              "en": "Name",        "tj": "Ном"},
    "csv_w_passes":    {"ru": "Проходов",         "en": "Passes",       "tj": "Гузаришҳо"},
    "csv_w_last_seen": {"ru": "Последний раз",     "en": "Last seen",    "tj": "Бори охир"},
    "csv_w_last_door": {"ru": "Последняя дверь",   "en": "Last door",    "tj": "Дари охирин"},
    "workers_caption": {
        "ru": "👥 Работники за 30 дней — {n}",
        "en": "👥 Workers (30 days) — {n}",
        "tj": "👥 Кормандон (30 рӯз) — {n}",
    },
    "workers_filename": {"ru": "rabotniki", "en": "workers", "tj": "kormandon"},
}


# --- Локализованный формат даты ---
# strftime('%b') зависит от LC_TIME ОС, поэтому месяцы для EN зашиваем сами.
_MONTH_ABBR_EN = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _coerce_dt(value):
    from datetime import datetime
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def fmt_dt(value, lang: str = DEFAULT_LANG) -> str:
    """Полная дата+время (с секундами). EN: 'Apr 28, 09:15:03'. RU/TJ: '28.04 09:15:03'."""
    dt = _coerce_dt(value)
    if dt is None:
        return "—" if value is None else str(value)
    if normalize_lang(lang) == "en":
        return f"{_MONTH_ABBR_EN[dt.month]} {dt.day}, {dt.strftime('%H:%M:%S')}"
    return dt.strftime("%d.%m %H:%M:%S")


def fmt_dt_short(value, lang: str = DEFAULT_LANG) -> str:
    """Без секунд. EN: 'Apr 28, 09:15'. RU/TJ: '28.04 09:15'."""
    dt = _coerce_dt(value)
    if dt is None:
        return "—" if value is None else str(value)
    if normalize_lang(lang) == "en":
        return f"{_MONTH_ABBR_EN[dt.month]} {dt.day}, {dt.strftime('%H:%M')}"
    return dt.strftime("%d.%m %H:%M")


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    lang = normalize_lang(lang)
    entry = T.get(key)
    if entry is None:
        return key  # видимый сигнал о пропущенном переводе
    s = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    return s.format(**kwargs) if kwargs else s


# --- DSS event-type → ключ для t() ---

EVENT_TYPE_KEY = {
    "600005": "event_pass_granted",
    "AccessControl.PassGranted": "event_pass_granted",
    "AccessControl.PassDenied": "event_pass_denied",
    "AccessControl.DoorForcedOpen": "event_door_forced",
    "AccessControl.DoorHeldOpen": "event_door_held",
    "AccessControl.AntiPassback": "event_antipassback",
    "FaceRecognition.Stranger": "event_face_stranger",
}


def fmt_minutes(n: int, lang: str) -> str:
    if n < 60:
        return t("min_short", lang, n=n)
    h, m = divmod(n, 60)
    if m == 0:
        return t("h_short", lang, h=h)
    return t("hm_short", lang, h=h, m=m)


# --- Парсинг даты с поддержкой ключевых слов на 3 языках ---

_DATE_WORDS = {
    "today":     {"сегодня", "today", "имрӯз", "имруз"},
    "yesterday": {"вчера", "yesterday", "дирӯз", "дируз"},
    "week":      {"неделя", "week", "ҳафта", "хафта"},
    "month":     {"месяц", "month", "моҳ", "мох"},
}


def parse_date_word(s: str) -> date | None:
    s = s.strip().lower()
    today = date.today()
    if s in _DATE_WORDS["today"]:
        return today
    if s in _DATE_WORDS["yesterday"]:
        return today - timedelta(days=1)
    if s in _DATE_WORDS["week"]:
        return today - timedelta(days=7)
    if s in _DATE_WORDS["month"]:
        return today - timedelta(days=30)
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        d = datetime.strptime(s, "%d.%m").date()
        return d.replace(year=today.year)
    except ValueError:
        return None
