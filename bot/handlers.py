"""aiogram-роутер: inline-меню + slash-команды + переключение языка.

`lang` приходит в каждый хендлер из `LangMiddleware` через `data["lang"]`.
"""
from __future__ import annotations

import io
from datetime import date, datetime, time, timedelta
from time import monotonic

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from loguru import logger

from db import DB
from dss.client import DSSClient
from dss.persons import DSSPersonClient, DSSPersonError
from .filters import AdminFilter
from .formatters import (
    format_absent,
    format_attendance,
    format_door_events,
    format_event,
    format_find_results,
    format_health,
    format_inside_list,
    format_late,
    format_person_profile,
    format_today,
    format_today_summary,
    format_workers,
)
from .i18n import (
    LANG_PICK_LABEL,
    LANGS,
    label,
    labels_for,
    lang_button_label,
    normalize_lang,
    t,
)
from .lang import LangResolver
from .middleware import LangMiddleware
from .report_csv import parse_date_input
from .teachers_xlsx import parse_teachers_xlsx
from .xlsx import (
    generate_absent_xlsx,
    generate_attendance_xlsx,
    generate_late_xlsx,
    generate_teachers_template_xlsx,
    generate_top_late_xlsx,
    generate_trend_xlsx,
    generate_workers_xlsx,
)


class SearchStates(StatesGroup):
    waiting_name = State()
    waiting_door = State()
    waiting_report_from = State()
    waiting_report_to = State()
    waiting_late_from = State()
    waiting_late_to = State()
    waiting_absent_from = State()
    waiting_absent_to = State()
    waiting_teachers_xlsx = State()
    waiting_sync_group = State()
    # Мастер «Добавить учителя»: ФИО → фото → телефон → предмет.
    waiting_new_teacher_name = State()
    waiting_new_teacher_photo = State()
    waiting_new_teacher_phone = State()
    waiting_new_teacher_subject = State()


_TG_MSG_LIMIT = 4000


def _split_long(text: str, max_len: int = _TG_MSG_LIMIT) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut < max_len // 2:
            cut = remaining.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def persistent_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=label("today", lang)),
                KeyboardButton(text=label("late", lang)),
                KeyboardButton(text=label("absent", lang)),
            ],
            [
                KeyboardButton(text=label("find", lang)),
                KeyboardButton(text=label("report", lang)),
                KeyboardButton(text=label("workers", lang)),
            ],
            [KeyboardButton(text=lang_button_label(lang))],
        ],
        resize_keyboard=True,
        # is_persistent=True заставлял Android-клиент Telegram резервировать
        # кнопку «◁» под сворачивание клавиатуры — отсюда жалобы «back не
        # работает с одного раза». is_persistent=False (по умолчанию) этого
        # эффекта не вызывает; клавиатура всё равно остаётся видна, пока
        # пользователь сам её не свернёт грид-иконкой справа от ввода.
    )


def main_menu_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("menu_inside", lang), callback_data="m:inside"),
            InlineKeyboardButton(text=t("menu_today", lang), callback_data="m:today"),
        ],
        [InlineKeyboardButton(text=t("menu_attend", lang), callback_data="m:attend")],
        [
            InlineKeyboardButton(text=t("menu_find", lang), callback_data="m:find"),
            InlineKeyboardButton(text=t("menu_door", lang), callback_data="m:door"),
        ],
    ])


def back_kb(lang: str, source: str = "main") -> InlineKeyboardMarkup:
    """Кнопка «⬅ Меню». source задаёт, к какому родительскому меню вернуться:
    "main" — просто закрыть сообщение (используется по умолчанию, для секций
    без своего под-меню), "mon"/"late"/"absent" — открыть соответствующее
    под-меню заново. Семантика как у других ботов: «назад в родителя»."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=t("menu_back", lang),
            callback_data=f"m:back:{source}",
        )]
    ])


def cancel_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("menu_cancel", lang), callback_data="m:menu")]
    ])


def workers_menu_kb(lang: str) -> InlineKeyboardMarkup:
    """Меню действий по разделу «Работники» — 2 кнопки в ряд."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 Скачать Excel", callback_data="w:dl"),
            InlineKeyboardButton(text="📤 Загрузить Excel", callback_data="w:up"),
        ],
        [
            InlineKeyboardButton(text="📋 Шаблон Excel", callback_data="w:tmpl"),
            InlineKeyboardButton(text="🔄 Синхронизация", callback_data="w:sync"),
        ],
        [
            InlineKeyboardButton(text="➕ Добавить учителя", callback_data="w:add"),
        ],
        [InlineKeyboardButton(text=t("menu_back", lang), callback_data="m:menu")],
    ])


def skip_kb(lang: str) -> InlineKeyboardMarkup:
    """Клавиатура для шагов мастера, которые можно пропустить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏭ Пропустить", callback_data="wt:skip"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="m:menu"),
        ],
    ])


def lang_pick_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=LANG_PICK_LABEL[code], callback_data=f"lang:set:{code}")
        for code in LANGS
    ]])


# Ленивый фильтр: лейблы читаются на каждое сообщение через labels_for(...),
# а не замораживаются на момент import (#13). Безопасно при будущих
# изменениях BTN_LABELS в рантайме / в тестах.
def btn(key: str):
    return F.text.func(lambda txt, _k=key: bool(txt) and txt in labels_for(_k))


def register_handlers(
    router: Router,
    *,
    db: DB,
    dss: DSSClient,
    resolver: LangResolver,
    admin_ids: list[int],
    started_at: float,
    work_day_start: time,
    work_day_end: time,
    chat_group_filters: dict[int, frozenset[str]] | None = None,
) -> None:
    admin = AdminFilter(admin_ids)
    mw = LangMiddleware(resolver)
    router.message.middleware(mw)
    router.callback_query.middleware(mw)
    filters_map: dict[int, frozenset[str]] = dict(chat_group_filters or {})
    dss_persons = DSSPersonClient(dss)

    def _restrict_for(chat_id: int | None) -> frozenset[str] | None:
        """Для админа с фильтром (Амриддин → Secondary) возвращает множество
        разрешённых групп. Админу без фильтра — None (видит всё)."""
        if chat_id is None:
            return None
        return filters_map.get(chat_id) or None

    async def _send_long(msg: Message, text: str, *, final_markup=None, **kw) -> None:
        chunks = _split_long(text)
        for i, chunk in enumerate(chunks):
            kw_for_chunk = dict(kw)
            if final_markup is not None and i == len(chunks) - 1:
                kw_for_chunk["reply_markup"] = final_markup
            await msg.answer(chunk, **kw_for_chunk)

    async def _check_admin_cb(cb: CallbackQuery, lang: str) -> bool:
        if cb.from_user and cb.from_user.id in admin_ids:
            return True
        await cb.answer(t("access_admins_only", lang), show_alert=True)
        return False

    # --- бизнес-логика ---

    async def _render_inside(lang: str, restrict=None) -> str:
        rows = await db.list_inside(restrict_groups=restrict)
        return format_inside_list([dict(r) for r in rows], lang=lang)

    async def _render_today(lang: str, restrict=None) -> str:
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        stats = await db.stats_today(start, end, restrict_groups=restrict)
        inside = await db.count_inside(restrict_groups=restrict)
        return format_today(stats, inside, lang=lang)

    async def _render_attendance(lang: str, restrict=None) -> str:
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        rows = await db.attendance_today(start, end, restrict_groups=restrict)
        return format_attendance(
            [dict(r) for r in rows], work_day_start, work_day_end, lang=lang
        )

    async def _render_late(lang: str, restrict=None) -> str:
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        rows = await db.attendance_today(start, end, restrict_groups=restrict)
        return format_late(
            [dict(r) for r in rows], work_day_start, work_day_end, lang=lang
        )

    async def _render_absent(lang: str, restrict=None) -> str:
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        lookback_start = today_start - timedelta(days=30)
        rows = await db.absent_today(
            today_start, today_end, lookback_start, restrict_groups=restrict
        )
        return format_absent([dict(r) for r in rows], lang=lang)

    async def _render_workers(lang: str, restrict=None) -> str:
        lookback_start = datetime.now() - timedelta(days=30)
        rows = await db.list_known_persons(lookback_start, restrict_groups=restrict)
        return format_workers([dict(r) for r in rows], lang=lang)

    async def _send_find(msg: Message, query: str, lang: str, photo_limit: int = 5) -> None:
        restrict = _restrict_for(msg.chat.id)
        # Сначала — «карточки» уникальных найденных людей: отдел, должность,
        # предмет, телефон. Даже если по человеку нет записи в teachers/нет
        # групп — пользователь увидит «—» в нужных строках, а не пустоту.
        unique = await db.find_unique_persons_by_name(query, limit=5)
        for u in unique:
            pid = (u["person_id"] or "").strip()
            groups = set(await db.groups_for_person(pid)) if pid else set()
            # Fallback: если локально пусто — спросим DSS напрямую. Полезно
            # для людей, которых авто-синк ещё не подобрал (например, их
            # группа не указана в DSS_AUTO_SYNC_GROUPS).
            if pid and not groups:
                try:
                    dss_groups = await dss_persons.find_person_groups(pid)
                except Exception as e:
                    logger.warning("find_person_groups failed: {}", e)
                    dss_groups = []
                if dss_groups:
                    groups = set(dss_groups)
            tch = await db.find_teacher_by_person_id(pid) if pid else None
            position = tch["position"] if tch else None
            subject = tch["subject"] if tch else None
            phone = tch["phone"] if tch else None
            await msg.answer(
                format_person_profile(
                    u["person_name"], pid, groups,
                    position=position, subject=subject, phone=phone, lang=lang,
                ),
                parse_mode="HTML",
            )
        rows = await db.find_by_name(query, limit=10, restrict_groups=restrict)
        dicts = [dict(r) for r in rows]
        photo_rows = (
            await db.find_by_name_with_image(
                query, limit=photo_limit, restrict_groups=restrict
            )
            if dicts else []
        )
        text_markup = None if photo_rows else back_kb(lang)
        await _send_long(
            msg, format_find_results(query, dicts, lang=lang),
            parse_mode="HTML", final_markup=text_markup,
        )
        if not dicts:
            return
        for r in photo_rows:
            d = dict(r)
            url = d.get("snapshot_url") or ""
            try:
                data = await dss.download_bytes(url)
            except Exception as e:
                logger.warning("find: snapshot download failed: {}", e)
                continue
            if not data:
                continue
            caption = format_event(
                d, work_day_start=work_day_start, work_day_end=work_day_end, lang=lang,
            )
            filename = url.rsplit("/", 1)[-1] or "photo.jpg"
            try:
                await msg.bot.send_photo(
                    chat_id=msg.chat.id,
                    photo=BufferedInputFile(data, filename=filename),
                    caption=caption,
                    parse_mode="HTML",
                )
            except TelegramAPIError as e:
                logger.warning("find: send_photo rejected: {}", e)
        if photo_rows:
            await msg.answer(t("find_done", lang), reply_markup=back_kb(lang))

    async def _render_door(door: str, lang: str, restrict=None) -> str:
        since = datetime.now() - timedelta(hours=12)
        rows = await db.events_by_door(
            door, since, limit=30, restrict_groups=restrict
        )
        return format_door_events(door, [dict(r) for r in rows], lang=lang)

    async def _render_health(lang: str) -> str:
        uptime_sec = int(monotonic() - started_at)
        h, rem = divmod(uptime_sec, 3600)
        m, s = divmod(rem, 60)
        uptime = f"{h}h {m}m {s}s"
        last_id = await db.last_event_id()
        dss_ok = dss.token is not None
        return format_health(uptime, last_id, dss_ok, lang=lang)

    async def _render_dssping(lang: str) -> str:
        try:
            ok = await dss.ping()
            return t("dss_ok", lang) if ok else t("dss_no_session", lang)
        except Exception as e:
            logger.warning("dss_ping fail: {}", e)
            return t("dss_error", lang, e=repr(e))

    # --- /start ---

    @router.message(CommandStart())
    async def on_start(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        await msg.answer(
            t("menu_text", lang),
            reply_markup=persistent_kb(lang),
            parse_mode="HTML",
        )

    # --- Переключатель языка: длинная кнопка → inline-выбор ---

    @router.message(F.text.func(lambda x: bool(x) and x in labels_for("lang_btn")))
    async def on_lang_button(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        await msg.answer(t("lang_choose_prompt", lang), reply_markup=lang_pick_kb())

    @router.callback_query(F.data.startswith("lang:set:"))
    async def cb_set_lang(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if cb.from_user is None or cb.message is None:
            await cb.answer()
            return
        new = normalize_lang(cb.data.split(":", 2)[2])
        await resolver.set(cb.from_user.id, new)
        await state.clear()
        # Удаляем chooser и шлём одно итоговое сообщение
        # (подтверждение + меню + новая клавиатура).
        try:
            await cb.message.delete()
        except TelegramAPIError:
            pass
        await cb.message.answer(
            t("lang_changed", new) + "\n\n" + t("menu_text", new),
            reply_markup=persistent_kb(new),
            parse_mode="HTML",
        )
        await cb.answer()

    # --- Persistent-клавиатура: маршрутизация по тексту любого языка ---

    @router.message(btn("today"), admin)
    async def on_btn_today(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        await _send_long(msg, await _render_today(lang, _restrict_for(msg.chat.id)),
                         parse_mode="HTML", final_markup=back_kb(lang))

    def _late_period_kb(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t("late_btn_today", lang), callback_data="late:today"),
                InlineKeyboardButton(text=t("late_btn_week", lang),  callback_data="late:week"),
            ],
            [
                InlineKeyboardButton(text=t("late_btn_month", lang), callback_data="late:month"),
                InlineKeyboardButton(text=t("late_btn_year", lang),  callback_data="late:year"),
            ],
            [
                InlineKeyboardButton(text=t("late_btn_range", lang), callback_data="late:range"),
            ],
        ])

    @router.message(btn("late"), admin)
    async def on_btn_late(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        await msg.answer(
            t("late_pick_period", lang),
            parse_mode="HTML",
            reply_markup=_late_period_kb(lang),
        )

    def _late_range_for(key: str) -> tuple[date, date]:
        """Возвращает (start_date, end_date) включительно для пресета."""
        today = datetime.now().date()
        if key == "today":
            return today, today
        if key == "week":
            # ISO: понедельник = 0
            return today - timedelta(days=today.weekday()), today
        if key == "month":
            return today.replace(day=1), today
        if key == "year":
            return today.replace(month=1, day=1), today
        # fallback — сегодня
        return today, today

    async def _send_late_xlsx(
        chat_id: int,
        bot,
        lang: str,
        start_date: date,
        end_date: date,
    ) -> None:
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        start_iso = datetime.combine(start_date, time(0, 0)).isoformat()
        end_iso = datetime.combine(end_date + timedelta(days=1), time(0, 0)).isoformat()
        rows = await db.attendance_range(
            start_iso, end_iso, restrict_groups=_restrict_for(chat_id)
        )
        xlsx_bytes = generate_late_xlsx(
            [dict(r) for r in rows], work_day_start,
            start_date, end_date, lang=lang,
        )
        # Если опоздавших нет — generate_late_xlsx вернёт файл только с шапкой.
        # Считаем фактическое число строк, чтобы не слать «пустышку».
        late_count = 0
        ws_min = work_day_start.hour * 60 + work_day_start.minute
        for r in rows:
            fi = r["first_in"]
            if not fi:
                continue
            try:
                dt = datetime.fromisoformat(str(fi))
            except ValueError:
                continue
            if (dt.hour * 60 + dt.minute) > ws_min:
                late_count += 1
        if late_count == 0:
            await bot.send_message(
                chat_id, t("late_xlsx_no_data", lang), parse_mode="HTML",
                reply_markup=back_kb(lang, "late"),
            )
            return
        filename = (
            f"{t('late_xlsx_filename', lang)}_"
            f"{start_date.strftime('%d%m%Y')}_"
            f"{end_date.strftime('%d%m%Y')}.xlsx"
        )
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(xlsx_bytes, filename=filename),
            caption=t(
                "late_xlsx_caption", lang,
                a=start_date.strftime("%d.%m.%Y"),
                b=end_date.strftime("%d.%m.%Y"),
                n=late_count,
            ),
            parse_mode="HTML",
            reply_markup=back_kb(lang, "late"),
        )

    @router.callback_query(F.data.startswith("late:"))
    async def cb_late_period(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        key = cb.data.split(":", 1)[1]
        if key == "range":
            await state.set_state(SearchStates.waiting_late_from)
            await cb.answer()
            await cb.message.answer(
                t("report_from_q", lang) + t("date_hint", lang),
                parse_mode="HTML",
            )
            return
        if key not in {"today", "week", "month", "year"}:
            await cb.answer()
            return
        await cb.answer(t("calculating", lang))
        start_date, end_date = _late_range_for(key)
        await _send_late_xlsx(
            cb.message.chat.id, cb.message.bot, lang, start_date, end_date,
        )

    @router.message(SearchStates.waiting_late_from, admin, F.text)
    async def on_late_from_input(msg: Message, state: FSMContext, lang: str) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        d = parse_date_input(text)
        if d is None:
            await msg.answer(t("date_unparsed", lang) + t("date_hint", lang),
                             parse_mode="HTML")
            return
        await state.set_data({"late_from": d.isoformat()})
        await state.set_state(SearchStates.waiting_late_to)
        await msg.answer(
            t("report_to_q", lang, d=d.strftime("%d.%m.%Y")) + t("date_hint", lang),
            parse_mode="HTML",
        )

    @router.message(SearchStates.waiting_late_to, admin, F.text)
    async def on_late_to_input(msg: Message, state: FSMContext, lang: str) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        end_date = parse_date_input(text)
        if end_date is None:
            await msg.answer(t("date_unparsed", lang) + t("date_hint", lang),
                             parse_mode="HTML")
            return
        data = await state.get_data()
        from_iso = data.get("late_from")
        await state.clear()
        if not from_iso:
            await msg.answer(t("report_state_lost", lang))
            return
        start_date = date.fromisoformat(from_iso)
        await _send_late_xlsx(msg.chat.id, msg.bot, lang, start_date, end_date)

    def _absent_period_kb(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t("late_btn_today", lang), callback_data="abs:today"),
                InlineKeyboardButton(text=t("late_btn_week", lang),  callback_data="abs:week"),
            ],
            [
                InlineKeyboardButton(text=t("late_btn_month", lang), callback_data="abs:month"),
                InlineKeyboardButton(text=t("late_btn_year", lang),  callback_data="abs:year"),
            ],
            [
                InlineKeyboardButton(text=t("late_btn_range", lang), callback_data="abs:range"),
            ],
        ])

    @router.message(btn("absent"), admin)
    async def on_btn_absent(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        await msg.answer(
            t("absent_pick_period", lang),
            parse_mode="HTML",
            reply_markup=_absent_period_kb(lang),
        )

    async def _send_absent_xlsx(
        chat_id: int,
        bot,
        lang: str,
        start_date: date,
        end_date: date,
    ) -> None:
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        # Окно «регулярных» — 30 дней до начала периода. Так новички, появившиеся
        # только внутри периода, не получат фейковых пропусков за дни до их
        # появления.
        lookback_dt = datetime.combine(start_date, time(0, 0)) - timedelta(days=30)
        start_iso = datetime.combine(start_date, time(0, 0)).isoformat()
        end_iso = datetime.combine(end_date + timedelta(days=1), time(0, 0)).isoformat()
        lookback_iso = lookback_dt.isoformat()

        regulars, seen_by_day = await db.absent_range(
            start_iso, end_iso, lookback_iso,
            restrict_groups=_restrict_for(chat_id),
        )
        xlsx_bytes, n_rows = generate_absent_xlsx(
            [dict(r) for r in regulars], seen_by_day,
            start_date, end_date, lang=lang,
        )
        if n_rows == 0:
            await bot.send_message(
                chat_id, t("absent_xlsx_no_data", lang), parse_mode="HTML",
                reply_markup=back_kb(lang, "absent"),
            )
            return
        filename = (
            f"{t('absent_xlsx_filename', lang)}_"
            f"{start_date.strftime('%d%m%Y')}_"
            f"{end_date.strftime('%d%m%Y')}.xlsx"
        )
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(xlsx_bytes, filename=filename),
            caption=t(
                "absent_xlsx_caption", lang,
                a=start_date.strftime("%d.%m.%Y"),
                b=end_date.strftime("%d.%m.%Y"),
                n=n_rows,
            ),
            parse_mode="HTML",
            reply_markup=back_kb(lang, "absent"),
        )

    @router.callback_query(F.data.startswith("abs:"))
    async def cb_absent_period(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        key = cb.data.split(":", 1)[1]
        if key == "range":
            await state.set_state(SearchStates.waiting_absent_from)
            await cb.answer()
            await cb.message.answer(
                t("report_from_q", lang) + t("date_hint", lang),
                parse_mode="HTML",
            )
            return
        if key not in {"today", "week", "month", "year"}:
            await cb.answer()
            return
        await cb.answer(t("calculating", lang))
        start_date, end_date = _late_range_for(key)
        await _send_absent_xlsx(
            cb.message.chat.id, cb.message.bot, lang, start_date, end_date,
        )

    @router.message(SearchStates.waiting_absent_from, admin, F.text)
    async def on_absent_from_input(msg: Message, state: FSMContext, lang: str) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        d = parse_date_input(text)
        if d is None:
            await msg.answer(t("date_unparsed", lang) + t("date_hint", lang),
                             parse_mode="HTML")
            return
        await state.set_data({"absent_from": d.isoformat()})
        await state.set_state(SearchStates.waiting_absent_to)
        await msg.answer(
            t("report_to_q", lang, d=d.strftime("%d.%m.%Y")) + t("date_hint", lang),
            parse_mode="HTML",
        )

    @router.message(SearchStates.waiting_absent_to, admin, F.text)
    async def on_absent_to_input(msg: Message, state: FSMContext, lang: str) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        end_date = parse_date_input(text)
        if end_date is None:
            await msg.answer(t("date_unparsed", lang) + t("date_hint", lang),
                             parse_mode="HTML")
            return
        data = await state.get_data()
        from_iso = data.get("absent_from")
        await state.clear()
        if not from_iso:
            await msg.answer(t("report_state_lost", lang))
            return
        start_date = date.fromisoformat(from_iso)
        await _send_absent_xlsx(msg.chat.id, msg.bot, lang, start_date, end_date)

    def _mon_menu_kb(lang: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("mon_btn_today", lang),    callback_data="mon:today")],
            [InlineKeyboardButton(text=t("mon_btn_trend", lang),    callback_data="mon:trend")],
            [InlineKeyboardButton(text=t("mon_btn_top_late", lang), callback_data="mon:top_late")],
            [InlineKeyboardButton(text=t("mon_btn_range", lang),    callback_data="mon:range")],
        ])

    @router.message(btn("report"), admin)
    async def on_btn_report(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        await msg.answer(
            t("mon_pick", lang),
            parse_mode="HTML",
            reply_markup=_mon_menu_kb(lang),
        )

    async def _send_mon_today(chat_id: int, bot, lang: str) -> None:
        """Текстовая сводка по сегодня + разбивка по отделам владельца."""
        now = datetime.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        lookback = day_start - timedelta(days=30)
        restrict = _restrict_for(chat_id)

        regulars, seen_by_day = await db.absent_range(
            day_start.isoformat(), day_end.isoformat(), lookback.isoformat(),
            restrict_groups=restrict,
        )
        today_iso = day_start.date().isoformat()
        seen_today = seen_by_day.get(today_iso, set())
        regulars_total = len(regulars)
        came = len(seen_today)
        absent = max(0, regulars_total - came)

        # Опоздавшие сегодня
        att = await db.attendance_today(
            day_start, day_end, restrict_groups=restrict,
        )
        ws_min = work_day_start.hour * 60 + work_day_start.minute
        late = 0
        for r in att:
            fi = r["first_in"]
            if not fi:
                continue
            try:
                d = datetime.fromisoformat(str(fi))
            except ValueError:
                continue
            if (d.hour * 60 + d.minute) > ws_min:
                late += 1

        inside = await db.count_inside(restrict_groups=restrict)

        # Разбивка по отделам — только если у админа фильтр с >1 группой,
        # либо вообще нет фильтра (директор). Один-в-один с filter — смысла
        # нет (одна строка с тем же числом).
        by_dept: list[tuple[str, int, int]] = []
        owner = restrict
        if owner is None or len(owner) > 1:
            depts = sorted(owner) if owner else None
            if depts is None:
                # Директор без фильтра — собираем все группы из локальной базы
                rows = await db.list_groups()
                depts = [r["group_name"] for r in rows]
            for dept in depts:
                d_regs, d_seen = await db.absent_range(
                    day_start.isoformat(), day_end.isoformat(),
                    lookback.isoformat(),
                    restrict_groups=frozenset({dept}),
                )
                d_total = len(d_regs)
                if d_total == 0:
                    continue
                d_came = len(d_seen.get(today_iso, set()))
                by_dept.append((dept, d_came, d_total))

        text = format_today_summary(
            today_str=now.strftime("%d.%m.%Y"),
            regulars_count=regulars_total,
            came=came, late=late, absent=absent, inside=inside,
            by_dept=by_dept, lang=lang,
        )
        await bot.send_message(
            chat_id, text, parse_mode="HTML", reply_markup=back_kb(lang, "mon"),
        )

    async def _send_mon_trend(chat_id: int, bot, lang: str) -> None:
        """Excel с LineChart — пришли/опоздали/не пришли по дням за 30 дней."""
        end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=30)
        lookback = start - timedelta(days=30)
        restrict = _restrict_for(chat_id)

        regulars, seen_by_day = await db.absent_range(
            start.isoformat(), (end + timedelta(days=1)).isoformat(),
            lookback.isoformat(),
            restrict_groups=restrict,
        )
        regulars_total = len(regulars)

        # Опоздания: пройдём по attendance_range один раз, агрегируем по дням
        rows = await db.attendance_range(
            start.isoformat(), (end + timedelta(days=1)).isoformat(),
            restrict_groups=restrict,
        )
        ws_min = work_day_start.hour * 60 + work_day_start.minute
        late_by_day: dict[str, int] = {}
        for r in rows:
            fi = r["first_in"]
            if not fi:
                continue
            try:
                d = datetime.fromisoformat(str(fi))
            except ValueError:
                continue
            if (d.hour * 60 + d.minute) > ws_min:
                day_key = (r["day"] or d.date().isoformat())[:10]
                late_by_day[day_key] = late_by_day.get(day_key, 0) + 1

        daily: list[dict] = []
        cur = start.date()
        end_date = end.date()
        while cur <= end_date:
            if cur.weekday() != 6:  # skip Sunday
                key = cur.isoformat()
                came = len(seen_by_day.get(key, set()))
                daily.append({
                    "date": cur.strftime("%d.%m"),
                    "came": came,
                    "late": late_by_day.get(key, 0),
                    "absent": max(0, regulars_total - came),
                })
            cur += timedelta(days=1)

        xlsx_bytes = generate_trend_xlsx(daily, lang=lang)
        filename = (
            f"{t('mon_trend_filename', lang)}_"
            f"{start.strftime('%d%m')}_{end.strftime('%d%m%Y')}.xlsx"
        )
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(xlsx_bytes, filename=filename),
            caption=t("mon_trend_caption", lang),
            reply_markup=back_kb(lang, "mon"),
        )

    async def _send_mon_top_late(chat_id: int, bot, lang: str, top_n: int = 10) -> None:
        """Excel с топ-N хронических опаздывающих за 30 дней."""
        end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=30)
        restrict = _restrict_for(chat_id)
        rows = await db.attendance_range(
            start.isoformat(), (end + timedelta(days=1)).isoformat(),
            restrict_groups=restrict,
        )
        ws_min = work_day_start.hour * 60 + work_day_start.minute

        # person_id → {days_late, total_min, name}
        agg: dict[str, dict] = {}
        for r in rows:
            fi = r["first_in"]
            if not fi:
                continue
            try:
                d = datetime.fromisoformat(str(fi))
            except ValueError:
                continue
            late_m = (d.hour * 60 + d.minute) - ws_min
            if late_m <= 0:
                continue
            pid = str(r["person_id"] or "").strip()
            if not pid:
                continue
            slot = agg.setdefault(pid, {
                "name": r["person_name"] or pid,
                "days_late": 0, "total_min": 0,
            })
            slot["days_late"] += 1
            slot["total_min"] += late_m

        # Топ по сумме минут
        sorted_pids = sorted(
            agg.keys(),
            key=lambda p: (-agg[p]["total_min"], -agg[p]["days_late"]),
        )[:top_n]

        if not sorted_pids:
            await bot.send_message(
                chat_id, t("mon_top_no_data", lang), parse_mode="HTML",
                reply_markup=back_kb(lang, "mon"),
            )
            return

        out_rows: list[dict] = []
        for pid in sorted_pids:
            groups = sorted(await db.groups_for_person(pid))
            tch = await db.find_teacher_by_person_id(pid)
            position = (tch["position"] if tch else None) or ""
            slot = agg[pid]
            out_rows.append({
                "name": slot["name"],
                "dept": ", ".join(groups) if groups else "",
                "position": position,
                "days_late": slot["days_late"],
                "total_min": slot["total_min"],
                "avg_min": slot["total_min"] / slot["days_late"],
            })

        xlsx_bytes = generate_top_late_xlsx(out_rows, lang=lang)
        filename = (
            f"{t('mon_top_filename', lang)}_"
            f"{end.strftime('%d%m%Y')}.xlsx"
        )
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(xlsx_bytes, filename=filename),
            caption=t("mon_top_caption", lang, n=len(out_rows)),
            parse_mode="HTML",
            reply_markup=back_kb(lang, "mon"),
        )

    @router.callback_query(F.data.startswith("mon:"))
    async def cb_mon(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        key = cb.data.split(":", 1)[1]
        if key == "range":
            await state.set_state(SearchStates.waiting_report_from)
            await cb.answer()
            await cb.message.answer(
                t("report_from_q", lang) + t("date_hint", lang),
                parse_mode="HTML",
            )
            return
        if key not in {"today", "trend", "top_late"}:
            await cb.answer()
            return
        await cb.answer(t("calculating", lang))
        chat_id = cb.message.chat.id
        bot = cb.message.bot
        if key == "today":
            await _send_mon_today(chat_id, bot, lang)
        elif key == "trend":
            await _send_mon_trend(chat_id, bot, lang)
        elif key == "top_late":
            await _send_mon_top_late(chat_id, bot, lang)

    @router.message(btn("workers"), admin)
    async def on_btn_workers(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        s = await db.teachers_stats()
        owner_groups = _restrict_for(msg.chat.id) or frozenset()
        owner_line = (
            f"\n👤 Ваша группа: <b>{', '.join(sorted(owner_groups))}</b>"
            if owner_groups else ""
        )
        await msg.answer(
            f"<b>👥 Работники</b>{owner_line}\n"
            f"В базе учителей: {s['total']} (привязано к DSS {s['linked']}, "
            f"не привязано {s['unlinked']}).\n\n"
            "Что делаем?",
            parse_mode="HTML",
            reply_markup=workers_menu_kb(lang),
        )

    @router.message(btn("find"), admin)
    async def on_btn_find(msg: Message, state: FSMContext, lang: str) -> None:
        await state.set_state(SearchStates.waiting_name)
        await msg.answer(t("ask_find_name", lang))

    # --- callback'и под раздел «Работники» ---

    @router.callback_query(F.data == "w:dl")
    async def cb_workers_dl(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await cb.answer(t("calculating", lang))
        chat_id = cb.from_user.id if cb.from_user else None
        lookback_start = datetime.now() - timedelta(days=30)
        rows = await db.list_known_persons(
            lookback_start, restrict_groups=_restrict_for(chat_id)
        )
        if not rows:
            await cb.message.answer(
                t("workers_empty", lang), reply_markup=back_kb(lang)
            )
            return
        dicts = [dict(r) for r in rows]
        xlsx_bytes = generate_workers_xlsx(dicts, lang=lang)
        filename = (
            f"{t('workers_filename', lang)}_"
            f"{datetime.now().strftime('%Y%m%d')}.xlsx"
        )
        await cb.message.bot.send_document(
            chat_id=cb.message.chat.id,
            document=BufferedInputFile(xlsx_bytes, filename=filename),
            caption=t("workers_caption", lang, n=len(dicts)),
            reply_markup=back_kb(lang),
        )

    @router.callback_query(F.data == "w:up")
    async def cb_workers_up(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await state.set_state(SearchStates.waiting_teachers_xlsx)
        await cb.answer()
        owner_groups = _restrict_for(cb.from_user.id if cb.from_user else None) or frozenset()
        owner_hint = (
            f"\n💡 Все учителя из файла будут добавлены в вашу группу: "
            f"<b>{', '.join(sorted(owner_groups))}</b>"
            if owner_groups else ""
        )
        await cb.message.answer(
            "📤 Пришлите файл .xlsx со столбцами:\n"
            "<code>A: Subject · B: Teacher's Name (en) · C: Имя учителя (ru) · "
            "D: Номи омӯзгор (tg) · E: Phone Number · F: Position</code>\n\n"
            "Первая строка — заголовки. Колонка <b>F (Position)</b> опциональна "
            "(должность: «Methodist», «Secretary», «Librarian» и т.п.).\n"
            "Шаблон — кнопка <b>📋 Шаблон Excel</b> в меню «Работники»."
            + owner_hint,
            parse_mode="HTML",
            reply_markup=cancel_kb(lang),
        )

    @router.callback_query(F.data == "w:tmpl")
    async def cb_workers_template(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await cb.answer()
        xlsx_bytes = generate_teachers_template_xlsx(lang=lang)
        await cb.message.bot.send_document(
            chat_id=cb.message.chat.id,
            document=BufferedInputFile(
                xlsx_bytes, filename="template_teachers.xlsx"
            ),
            caption=(
                "📋 Шаблон для импорта учителей.\n"
                "Заполните своими данными и пришлите боту через "
                "«📤 Загрузить Excel».\n\n"
                "<b>Колонки:</b>\n"
                "A — Subject (предмет, напр. «забони англисӣ»)\n"
                "B — Teacher's Name (en)\n"
                "C — Имя учителя (ru)\n"
                "D — Номи омӯзгор (tg)\n"
                "E — Phone Number (+992…)\n"
                "F — Position (должность; опционально)"
            ),
            parse_mode="HTML",
            reply_markup=back_kb(lang),
        )

    @router.callback_query(F.data == "w:sync")
    async def cb_workers_sync(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        owner_groups = _restrict_for(cb.from_user.id if cb.from_user else None)
        if owner_groups:
            # Владелец группы — синхронизируем сразу в его группы, без вопросов.
            added_total = 0
            for g in owner_groups:
                added_total += await db.sync_linked_teachers_to_group(g)
            await cb.answer("Готово")
            await cb.message.answer(
                f"🔄 Синхронизация в <b>{', '.join(sorted(owner_groups))}</b>: "
                f"добавлено новых записей — {added_total}.",
                parse_mode="HTML",
                reply_markup=back_kb(lang),
            )
            return
        # Глобальный админ — спросим имя группы.
        await state.set_state(SearchStates.waiting_sync_group)
        await cb.answer()
        await cb.message.answer(
            "🔄 В какую группу синхронизировать всех привязанных учителей?\n"
            "Например: <code>Secondary</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb(lang),
        )

    @router.callback_query(F.data == "w:add")
    async def cb_workers_add(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        owner_groups = _restrict_for(cb.from_user.id if cb.from_user else None)
        await state.set_state(SearchStates.waiting_new_teacher_name)
        await state.update_data(
            new_teacher={},
            owner_groups=sorted(owner_groups) if owner_groups else [],
        )
        await cb.answer()
        owner_hint = (
            f"\n\nПо завершении учитель попадёт в группу "
            f"<b>{', '.join(sorted(owner_groups))}</b> "
            f"(если найдётся person_id в DSS)."
            if owner_groups else ""
        )
        await cb.message.answer(
            "➕ <b>Добавление учителя</b>\n"
            "Шаг 1/4: пришлите <b>ФИО</b> учителя одним сообщением."
            f"{owner_hint}",
            parse_mode="HTML",
            reply_markup=cancel_kb(lang),
        )

    @router.message(SearchStates.waiting_sync_group, admin, F.text)
    async def on_sync_group_input(
        msg: Message, state: FSMContext, lang: str
    ) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        await state.clear()
        if not text:
            await msg.answer("Пусто. Отменено.", reply_markup=back_kb(lang))
            return
        added = await db.sync_linked_teachers_to_group(text)
        rows = await db.persons_in_group(text)
        await msg.answer(
            f"✅ Группа <b>{text}</b>: добавлено {added} новых, "
            f"всего {len(rows)} чел.",
            parse_mode="HTML",
            reply_markup=back_kb(lang),
        )

    # --- мастер «Добавить учителя»: ФИО → фото → телефон → предмет ---

    async def _ask_photo(target: Message, lang: str) -> None:
        await target.answer(
            "Шаг 2/4: пришлите <b>фото</b> учителя (одно изображение).\n"
            "Если фото нет — нажмите «Пропустить».",
            parse_mode="HTML",
            reply_markup=skip_kb(lang),
        )

    async def _ask_phone(target: Message, lang: str) -> None:
        await target.answer(
            "Шаг 3/4: пришлите <b>номер телефона</b>.\n"
            "Пример: <code>+992 50 186 3933</code>",
            parse_mode="HTML",
            reply_markup=skip_kb(lang),
        )

    async def _ask_subject(target: Message, lang: str) -> None:
        await target.answer(
            "Шаг 4/4: пришлите <b>предмет</b>.\n"
            "Пример: <code>English</code>, <code>Math</code>",
            parse_mode="HTML",
            reply_markup=skip_kb(lang),
        )

    def _split_name(full: str) -> tuple[str, str]:
        """ФИО → (firstName, lastName). У школы DSS показывает 'Khojabekova Fotima'
        — порядок «фамилия имя». Первый токен → lastName, остальное → firstName.
        Один токен → всё в firstName, lastName пустой."""
        parts = full.strip().split()
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return " ".join(parts[1:]), parts[0]

    async def _try_create_dss_person(
        bot, photo_file_id: str | None, name: str, phone: str | None,
        org_name: str, person_id: str,
    ) -> tuple[bool, str]:
        """Пытается создать персону в DSS. Возвращает (ok, message_for_user)."""
        org_code = await dss_persons.get_org_code(org_name)
        if not org_code:
            return False, (
                f"DSS-группа «{org_name}» не найдена в DSS. "
                f"Проверьте /dss_groups."
            )
        photo_bytes: bytes | None = None
        if photo_file_id:
            try:
                buf = io.BytesIO()
                await bot.download(photo_file_id, destination=buf)
                photo_bytes = buf.getvalue() or None
            except Exception as e:  # noqa: BLE001
                logger.warning("DSS create_person: failed to download TG photo: {}", e)
        first, last = _split_name(name)
        try:
            await dss_persons.create_person(
                person_id=person_id,
                first_name=first,
                last_name=last,
                org_code=org_code,
                photo_bytes=photo_bytes,
                phone=phone,
            )
        except DSSPersonError as e:
            return False, f"DSS отказал: {e}"
        except Exception as e:  # noqa: BLE001
            logger.exception("DSS create_person crashed")
            return False, f"DSS ошибка: {e!r}"
        return True, "Создан в DSS"

    async def _finish_new_teacher(
        msg_or_cb_msg: Message, state: FSMContext, chat_id: int, lang: str,
    ) -> None:
        data = await state.get_data()
        nt: dict = data.get("new_teacher") or {}
        await state.clear()

        name = (nt.get("name") or "").strip()
        if not name:
            await msg_or_cb_msg.answer(
                "Имя пустое — добавление отменено.",
                reply_markup=back_kb(lang),
            )
            return

        phone_raw = nt.get("phone") or ""
        phone = "".join(c for c in phone_raw if c.isdigit() or c == "+") or None
        subject = (nt.get("subject") or "").strip() or None
        photo_id = nt.get("photo_file_id") or None

        tid, was_created = await db.upsert_teacher(
            phone=phone,
            name_en=name,
            name_ru=name,
            name_tg=name,
            subject=subject,
            photo_file_id=photo_id,
        )

        owner_groups = _restrict_for(chat_id) or frozenset()

        # Автопривязка по имени к events (если человек уже проходил через турникет
        # в прошлом и DSS-админ ранее его уже создал в DSS).
        row = await db.get_teacher(tid)
        current_pid = (row["person_id"] if row else None) or ""
        auto_linked = False
        if not current_pid:
            pid = await _auto_match_teacher({
                "name_en": name, "name_ru": name, "name_tg": name,
            })
            if pid:
                await db.link_teacher(tid, pid)
                current_pid = pid
                auto_linked = True

        # Если ещё не привязан и есть фото + владельческая группа — создаём
        # реального человека в DSS. Если фото нет — нет смысла, на турникете
        # его всё равно не узнают; остаёмся в локальной БД и просим линковку
        # вручную после первого прохода.
        dss_status: str | None = None
        if (
            not current_pid
            and photo_id
            and owner_groups
            and msg_or_cb_msg.bot is not None
        ):
            generated_pid = f"bot{tid:06d}"
            ok, dss_status = await _try_create_dss_person(
                msg_or_cb_msg.bot, photo_id, name, phone,
                next(iter(owner_groups)), generated_pid,
            )
            if ok:
                await db.link_teacher(tid, generated_pid)
                current_pid = generated_pid

        added_to: list[str] = []
        if current_pid and owner_groups:
            for g in owner_groups:
                if await db.add_person_to_group(current_pid, g):
                    added_to.append(g)

        verb = "Создан" if was_created else "Обновлён"
        parts = [f"✅ {verb}: <b>{name}</b> [{tid}]"]
        if subject:
            parts.append(f"· {subject}")
        if phone:
            parts.append(f"· {phone}")
        head = " ".join(parts)

        if auto_linked:
            link_line = (
                f"\n🔗 Привязан к существующей DSS-персоне "
                f"<code>{current_pid}</code> (по имени из истории)."
            )
        elif current_pid and dss_status:
            link_line = (
                f"\n🆕 {dss_status}. person_id <code>{current_pid}</code>"
            )
        elif dss_status:
            link_line = f"\n⚠️ {dss_status}\nЛокально сохранено, в DSS не создано."
        elif photo_id and not owner_groups:
            link_line = (
                "\n⚠️ Фото есть, но у вашего чата нет привязанной группы — "
                "DSS-регистрация пропущена."
            )
        elif not photo_id and current_pid:
            link_line = ""
        else:
            link_line = (
                f"\n⚠️ В DSS не зарегистрирован (нет фото). После первого "
                f"прохода через турникет используйте "
                f"<code>/teacher_link {tid} &lt;person_id&gt;</code>."
            )
        group_line = (
            f"\n➕ Добавлен в группу: <b>{', '.join(added_to)}</b>"
            if added_to else ""
        )
        await msg_or_cb_msg.answer(
            head + link_line + group_line,
            parse_mode="HTML",
            reply_markup=back_kb(lang),
        )

    @router.message(SearchStates.waiting_new_teacher_name, admin, F.text)
    async def on_new_teacher_name(
        msg: Message, state: FSMContext, lang: str
    ) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        if not text:
            await msg.answer(
                "Пустое имя. Пришлите ФИО учителя.",
                reply_markup=cancel_kb(lang),
            )
            return
        data = await state.get_data()
        nt = dict(data.get("new_teacher") or {})
        nt["name"] = text
        await state.update_data(new_teacher=nt)
        await state.set_state(SearchStates.waiting_new_teacher_photo)
        await _ask_photo(msg, lang)

    @router.message(SearchStates.waiting_new_teacher_photo, admin, F.photo)
    async def on_new_teacher_photo(
        msg: Message, state: FSMContext, lang: str
    ) -> None:
        # Берём самое крупное превью — это исходник.
        file_id = msg.photo[-1].file_id
        data = await state.get_data()
        nt = dict(data.get("new_teacher") or {})
        nt["photo_file_id"] = file_id
        await state.update_data(new_teacher=nt)
        await state.set_state(SearchStates.waiting_new_teacher_phone)
        await _ask_phone(msg, lang)

    @router.message(
        SearchStates.waiting_new_teacher_photo, admin,
        F.text.func(lambda t: not (t and t.startswith("/"))),
    )
    async def on_new_teacher_photo_other(
        msg: Message, state: FSMContext, lang: str
    ) -> None:
        # Не картинка — мягко напомним и оставим в этом же шаге.
        await msg.answer(
            "Это не фото. Пришлите изображение или нажмите «Пропустить».",
            reply_markup=skip_kb(lang),
        )

    @router.message(SearchStates.waiting_new_teacher_phone, admin, F.text)
    async def on_new_teacher_phone(
        msg: Message, state: FSMContext, lang: str
    ) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        data = await state.get_data()
        nt = dict(data.get("new_teacher") or {})
        nt["phone"] = text
        await state.update_data(new_teacher=nt)
        await state.set_state(SearchStates.waiting_new_teacher_subject)
        await _ask_subject(msg, lang)

    @router.message(SearchStates.waiting_new_teacher_subject, admin, F.text)
    async def on_new_teacher_subject(
        msg: Message, state: FSMContext, lang: str
    ) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        data = await state.get_data()
        nt = dict(data.get("new_teacher") or {})
        nt["subject"] = text
        await state.update_data(new_teacher=nt)
        await _finish_new_teacher(msg, state, msg.chat.id, lang)

    @router.callback_query(F.data == "wt:skip")
    async def cb_new_teacher_skip(
        cb: CallbackQuery, state: FSMContext, lang: str
    ) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        cur = await state.get_state()
        await cb.answer()
        msg = cb.message
        if cur == SearchStates.waiting_new_teacher_photo.state:
            await state.set_state(SearchStates.waiting_new_teacher_phone)
            await _ask_phone(msg, lang)
        elif cur == SearchStates.waiting_new_teacher_phone.state:
            await state.set_state(SearchStates.waiting_new_teacher_subject)
            await _ask_subject(msg, lang)
        elif cur == SearchStates.waiting_new_teacher_subject.state:
            chat_id = cb.from_user.id if cb.from_user else msg.chat.id
            await _finish_new_teacher(msg, state, chat_id, lang)
        else:
            # Skip нажат вне мастера — игнорируем.
            await msg.answer(
                "Нечего пропускать.", reply_markup=back_kb(lang)
            )

    # --- callback: возврат в меню ---

    @router.callback_query(F.data.startswith("m:back:"))
    async def cb_back(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        """Шаг назад в родительское под-меню (или закрытие, если родителя нет).
        Удаляет текущее сообщение и шлёт свежее меню родителя."""
        if not await _check_admin_cb(cb, lang):
            return
        await state.clear()
        await cb.answer()
        msg = cb.message
        if msg is None:
            return
        source = cb.data.split(":", 2)[2] if cb.data else "main"
        try:
            await msg.delete()
        except TelegramAPIError as e:
            logger.debug("cb_back delete: {}", e)
        chat_id = msg.chat.id
        bot = msg.bot
        if source == "mon":
            await bot.send_message(
                chat_id, t("mon_pick", lang),
                parse_mode="HTML", reply_markup=_mon_menu_kb(lang),
            )
        elif source == "late":
            await bot.send_message(
                chat_id, t("late_pick_period", lang),
                parse_mode="HTML", reply_markup=_late_period_kb(lang),
            )
        elif source == "absent":
            await bot.send_message(
                chat_id, t("absent_pick_period", lang),
                parse_mode="HTML", reply_markup=_absent_period_kb(lang),
            )
        # source == "main" — ничего не шлём, нижняя клавиатура и так на месте.

    @router.callback_query(F.data == "m:menu")
    async def cb_menu(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await state.clear()
        await cb.answer()
        msg = cb.message
        if msg is None:
            return
        # Inline-меню убрано: используем только постоянную reply-клавиатуру.
        # Удаляем сообщение целиком — визуально это и есть «шаг назад»: чат
        # очищается от под-меню, остаётся постоянная клавиатура снизу.
        # Если удалить нельзя (старше 48 часов / не наше) — снимаем хотя бы
        # inline-кнопки.
        try:
            await msg.delete()
        except TelegramAPIError as e:
            logger.debug("cb_menu delete failed, fallback to unmarkup: {}", e)
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except TelegramAPIError as e2:
                logger.debug("cb_menu edit_reply_markup also failed: {}", e2)

    # --- callback: read-only ---

    @router.callback_query(F.data == "m:inside")
    async def cb_inside(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        restrict = _restrict_for(cb.from_user.id if cb.from_user else None)
        await cb.message.edit_text(await _render_inside(lang, restrict),
                                   reply_markup=back_kb(lang), parse_mode="HTML")
        await cb.answer()

    @router.callback_query(F.data == "m:today")
    async def cb_today(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        restrict = _restrict_for(cb.from_user.id if cb.from_user else None)
        await cb.message.edit_text(await _render_today(lang, restrict),
                                   reply_markup=back_kb(lang), parse_mode="HTML")
        await cb.answer()

    @router.callback_query(F.data == "m:attend")
    async def cb_attend(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await cb.answer(t("calculating", lang))
        restrict = _restrict_for(cb.from_user.id if cb.from_user else None)
        await cb.message.edit_text(await _render_attendance(lang, restrict),
                                   reply_markup=back_kb(lang), parse_mode="HTML")

    @router.callback_query(F.data == "m:health")
    async def cb_health(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await cb.message.edit_text(await _render_health(lang),
                                   reply_markup=back_kb(lang), parse_mode="HTML")
        await cb.answer()

    @router.callback_query(F.data == "m:dssping")
    async def cb_dssping(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await cb.answer(t("checking", lang))
        text = await _render_dssping(lang)
        await cb.message.edit_text(text, reply_markup=back_kb(lang))

    # --- callback: запрос ввода через FSM ---

    @router.callback_query(F.data == "m:find")
    async def cb_find(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await state.set_state(SearchStates.waiting_name)
        await cb.message.edit_text(t("ask_find_name", lang),
                                   reply_markup=cancel_kb(lang), parse_mode="HTML")
        await cb.answer()

    @router.callback_query(F.data == "m:door")
    async def cb_door(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await state.set_state(SearchStates.waiting_door)
        await cb.message.edit_text(t("ask_door_name", lang),
                                   reply_markup=cancel_kb(lang), parse_mode="HTML")
        await cb.answer()

    # --- старые slash-команды ---

    @router.message(Command("inside"), admin)
    async def on_inside_cmd(msg: Message, lang: str) -> None:
        await _send_long(msg, await _render_inside(lang, _restrict_for(msg.chat.id)),
                         parse_mode="HTML", final_markup=back_kb(lang))

    @router.message(Command("today"), admin)
    async def on_today_cmd(msg: Message, lang: str) -> None:
        await msg.answer(await _render_today(lang, _restrict_for(msg.chat.id)),
                         reply_markup=back_kb(lang), parse_mode="HTML")

    @router.message(Command("attendance"), admin)
    async def on_attend_cmd(msg: Message, lang: str) -> None:
        await _send_long(msg, await _render_attendance(lang, _restrict_for(msg.chat.id)),
                         parse_mode="HTML", final_markup=back_kb(lang))

    @router.message(Command("find"), admin)
    async def on_find_cmd(msg: Message, command: CommandObject, lang: str) -> None:
        if not command.args:
            await msg.answer(t("usage_find", lang))
            return
        await _send_find(msg, command.args.strip(), lang)

    @router.message(Command("door"), admin)
    async def on_door_cmd(msg: Message, command: CommandObject, lang: str) -> None:
        if not command.args:
            await msg.answer(t("usage_door", lang))
            return
        await _send_long(
            msg,
            await _render_door(command.args.strip(), lang, _restrict_for(msg.chat.id)),
            parse_mode="HTML",
        )

    @router.message(Command("health"), admin)
    async def on_health_cmd(msg: Message, lang: str) -> None:
        await msg.answer(await _render_health(lang),
                         reply_markup=back_kb(lang), parse_mode="HTML")

    @router.message(Command("dss_ping"), admin)
    async def on_dssping_cmd(msg: Message, lang: str) -> None:
        await msg.answer(await _render_dssping(lang), reply_markup=back_kb(lang))

    # --- управление группами доступа к уведомлениям ---

    async def _resolve_person(query: str) -> tuple[str | None, str, list]:
        """Возвращает (person_id, person_name, candidates).

        Порядок поиска:
        1. Если query — только цифры с длиной >=7, считаем телефоном:
           ищем в teachers по нормализованному phone; если найден и
           person_id привязан — возвращаем его.
        2. Если query — короткие цифры (как DSS personId, '33'..'99999999'),
           возвращаем как person_id напрямую.
        3. Иначе ищем в teachers по name_en/ru/tg. Если ровно один с
           привязкой — возвращаем; несколько — отдаём кандидатов.
        4. Fallback: поиск в events.person_name (исторический путь).
        """
        q = query.strip()
        if not q:
            return None, "", []

        digits = "".join(ch for ch in q if ch.isdigit())

        # 1) Телефон.
        if len(digits) >= 7 and (q.startswith("+") or len(digits) >= 9):
            t_rows = await db.find_teacher(q)
            linked = [r for r in t_rows if (r["person_id"] or "")]
            if len(linked) == 1:
                r = linked[0]
                return str(r["person_id"]), str(r["name_en"] or r["name_ru"] or ""), []
            if len(t_rows) == 1 and not (t_rows[0]["person_id"] or ""):
                return None, "", [{
                    "person_id": "—",
                    "person_name": (
                        f"{t_rows[0]['name_en'] or '?'} "
                        f"(учитель найден, но не привязан к DSS — "
                        f"/teacher_link {t_rows[0]['id']} <person_id>)"
                    ),
                }]

        # 2) Короткое число — личный DSS person_id.
        if q.isdigit():
            cur = await db.conn.execute(
                """SELECT person_id, MAX(person_name) AS person_name
                   FROM events WHERE person_id = ? GROUP BY person_id""",
                (q,),
            )
            row = await cur.fetchone()
            if row:
                return str(row["person_id"]), str(row["person_name"] or ""), []
            return q, "", []

        # 3) Поиск по teachers (любой язык).
        t_rows = await db.find_teacher(q)
        linked = [r for r in t_rows if (r["person_id"] or "")]
        if len(linked) == 1:
            r = linked[0]
            return str(r["person_id"]), str(r["name_en"] or r["name_ru"] or ""), []
        if len(linked) > 1:
            return None, "", [
                {"person_id": r["person_id"], "person_name": r["name_en"] or r["name_ru"] or "?"}
                for r in linked
            ]
        # учитель найден, но не привязан — подсказка
        if len(t_rows) == 1:
            r = t_rows[0]
            return None, "", [{
                "person_id": "—",
                "person_name": (
                    f"{r['name_en'] or r['name_ru'] or '?'} "
                    f"(учитель найден, но не привязан к DSS — "
                    f"/teacher_link {r['id']} <person_id>)"
                ),
            }]

        # 4) Fallback: events.
        rows = await db.find_unique_persons_by_name(q, limit=10)
        if not rows:
            return None, "", []
        if len(rows) == 1:
            r = rows[0]
            return str(r["person_id"]), str(r["person_name"] or ""), []
        return None, "", [dict(r) for r in rows]

    @router.message(Command("groups"), admin)
    async def on_groups_cmd(msg: Message) -> None:
        rows = await db.list_groups()
        if not rows:
            await msg.answer(
                "Группы пока не созданы. Добавьте человека:\n"
                "<code>/group_add Secondary Иванов</code>",
                parse_mode="HTML",
            )
            return
        lines = ["<b>Группы:</b>"]
        for r in rows:
            lines.append(f"• <b>{r['group_name']}</b> — {r['n']} чел.")
        await msg.answer("\n".join(lines), parse_mode="HTML")

    @router.message(Command("group_list"), admin)
    async def on_group_list_cmd(
        msg: Message, command: CommandObject
    ) -> None:
        if not command.args:
            await msg.answer("Использование: <code>/group_list Secondary</code>",
                             parse_mode="HTML")
            return
        group = command.args.strip()
        rows = await db.persons_in_group(group)
        if not rows:
            await msg.answer(f"В группе <b>{group}</b> никого нет.",
                             parse_mode="HTML")
            return
        lines = [f"<b>{group}</b> ({len(rows)} чел.):"]
        for r in rows:
            name = r["person_name"] or "—"
            lines.append(f"• {name} <code>[{r['person_id']}]</code>")
        await _send_long(msg, "\n".join(lines), parse_mode="HTML")

    @router.message(Command("group_add"), admin)
    async def on_group_add_cmd(
        msg: Message, command: CommandObject
    ) -> None:
        if not command.args or len(command.args.split(maxsplit=1)) < 2:
            await msg.answer(
                "Использование: <code>/group_add &lt;группа&gt; &lt;имя или person_id&gt;</code>\n"
                "Пример: <code>/group_add Secondary Khojabekova</code>",
                parse_mode="HTML",
            )
            return
        group, query = command.args.split(maxsplit=1)
        pid, name, candidates = await _resolve_person(query)
        if candidates:
            lines = ["Несколько совпадений — уточните по person_id:"]
            for c in candidates:
                lines.append(
                    f"• {c['person_name']} <code>[{c['person_id']}]</code>"
                )
            await msg.answer("\n".join(lines), parse_mode="HTML")
            return
        if not pid:
            await msg.answer(f"Не нашёл человека по запросу <b>{query}</b>.",
                             parse_mode="HTML")
            return
        added = await db.add_person_to_group(pid, group)
        label = name or pid
        if added:
            await msg.answer(
                f"✅ Добавлен в <b>{group}</b>: {label} <code>[{pid}]</code>",
                parse_mode="HTML",
            )
        else:
            await msg.answer(
                f"ℹ️ Уже в <b>{group}</b>: {label} <code>[{pid}]</code>",
                parse_mode="HTML",
            )

    @router.message(Command("group_remove"), admin)
    async def on_group_remove_cmd(
        msg: Message, command: CommandObject
    ) -> None:
        if not command.args or len(command.args.split(maxsplit=1)) < 2:
            await msg.answer(
                "Использование: <code>/group_remove &lt;группа&gt; &lt;имя или person_id&gt;</code>",
                parse_mode="HTML",
            )
            return
        group, query = command.args.split(maxsplit=1)
        pid, name, candidates = await _resolve_person(query)
        if candidates:
            lines = ["Несколько совпадений — уточните по person_id:"]
            for c in candidates:
                lines.append(
                    f"• {c['person_name']} <code>[{c['person_id']}]</code>"
                )
            await msg.answer("\n".join(lines), parse_mode="HTML")
            return
        if not pid:
            await msg.answer(f"Не нашёл человека по запросу <b>{query}</b>.",
                             parse_mode="HTML")
            return
        removed = await db.remove_person_from_group(pid, group)
        label = name or pid
        if removed:
            await msg.answer(
                f"🗑 Удалён из <b>{group}</b>: {label} <code>[{pid}]</code>",
                parse_mode="HTML",
            )
        else:
            await msg.answer(
                f"ℹ️ Не состоял в <b>{group}</b>: {label} <code>[{pid}]</code>",
                parse_mode="HTML",
            )

    # --- импорт учителей из Excel и привязка к DSS personId ---

    async def _auto_match_teacher(teacher: dict) -> str | None:
        """Пытается найти person_id в events по name_en / name_ru / name_tg.
        Берём пересечение совпадений (если хотя бы по одному имени уникально —
        этот id и возвращаем). Если итогом >1 кандидата — None."""
        candidates: set[str] = set()
        seen_any = False
        for key in ("name_en", "name_ru", "name_tg"):
            name = (teacher.get(key) or "").strip()
            if not name:
                continue
            rows = await db.find_unique_persons_by_name(name, limit=10)
            ids = {str(r["person_id"]) for r in rows if r["person_id"]}
            if not ids:
                continue
            if not seen_any:
                candidates = ids
                seen_any = True
            else:
                # пересечение оставляет только тех, кто матчится во всех языках,
                # где вообще что-то нашлось
                inter = candidates & ids
                if inter:
                    candidates = inter
        if len(candidates) == 1:
            return next(iter(candidates))
        return None

    @router.message(Command("teachers"), admin)
    async def on_teachers_cmd(msg: Message, state: FSMContext) -> None:
        await state.clear()
        s = await db.teachers_stats()
        if s["total"] == 0:
            await msg.answer(
                "Учителей в базе нет. Импортируйте через "
                "<code>/teachers_import</code> и пришлите .xlsx.",
                parse_mode="HTML",
            )
            return
        await msg.answer(
            f"<b>Учителя:</b> всего {s['total']}, "
            f"привязано к DSS {s['linked']}, "
            f"не привязано {s['unlinked']}.\n\n"
            "Команды:\n"
            "• <code>/teachers_import</code> — загрузить .xlsx\n"
            "• <code>/teachers_unlinked</code> — список без привязки\n"
            "• <code>/teacher_link &lt;id&gt; &lt;person_id&gt;</code>\n"
            "• <code>/teacher_unlink &lt;id&gt;</code>",
            parse_mode="HTML",
        )

    @router.message(Command("teachers_import"), admin)
    async def on_teachers_import_cmd(msg: Message, state: FSMContext) -> None:
        await state.set_state(SearchStates.waiting_teachers_xlsx)
        await msg.answer(
            "Пришлите файл .xlsx со столбцами:\n"
            "A: Subject · B: Teacher's Name (en) · C: Имя учителя (ru) · "
            "D: Номи омӯзгор (tg) · E: Phone Number · F: Position\n\n"
            "Первая строка — заголовки. Колонка F (Position) опциональна.\n"
            "Шаблон можно скачать в меню «Работники» → «📋 Шаблон Excel».",
        )

    @router.message(SearchStates.waiting_teachers_xlsx, admin, F.document)
    async def on_teachers_xlsx_received(
        msg: Message, state: FSMContext
    ) -> None:
        await state.clear()
        doc = msg.document
        fname = (doc.file_name or "").lower()
        if not fname.endswith(".xlsx"):
            await msg.answer("Нужен файл .xlsx. Попробуйте ещё раз: /teachers_import")
            return
        try:
            buf = io.BytesIO()
            await msg.bot.download(doc, destination=buf)
            data = buf.getvalue()
            teachers = parse_teachers_xlsx(data)
        except ValueError as e:
            await msg.answer(f"Не получилось разобрать файл: {e}")
            return
        except Exception as e:
            logger.exception("teachers xlsx parse fail")
            await msg.answer(f"Ошибка чтения файла: {e!r}")
            return

        owner_groups = _restrict_for(msg.chat.id) or frozenset()
        created = 0
        updated = 0
        auto_linked = 0
        added_to_groups = 0
        for t_rec in teachers:
            tid, was_created = await db.upsert_teacher(
                phone=t_rec.get("phone"),
                name_en=t_rec.get("name_en"),
                name_ru=t_rec.get("name_ru"),
                name_tg=t_rec.get("name_tg"),
                subject=t_rec.get("subject"),
                position=t_rec.get("position"),
            )
            if was_created:
                created += 1
            else:
                updated += 1
            row = await db.get_teacher(tid)
            current_pid = (row["person_id"] if row else None) or ""
            if not current_pid:
                pid = await _auto_match_teacher(t_rec)
                if pid:
                    await db.link_teacher(tid, pid)
                    auto_linked += 1
                    current_pid = pid
            # Если импортирует владелец группы — каждого с person_id сразу
            # подкладываем в его группы.
            if current_pid and owner_groups:
                for g in owner_groups:
                    if await db.add_person_to_group(current_pid, g):
                        added_to_groups += 1

        s = await db.teachers_stats()
        owner_note = ""
        if owner_groups:
            owner_note = (
                f"\n• Добавлено в группу <b>{', '.join(sorted(owner_groups))}</b>: "
                f"{added_to_groups}"
            )
        await msg.answer(
            f"✅ Импорт завершён.\n"
            f"• Из файла: {len(teachers)}\n"
            f"• Создано: {created}, обновлено: {updated}\n"
            f"• Авто-привязано к DSS: {auto_linked}{owner_note}\n"
            f"• Всего в базе: {s['total']} (привязано {s['linked']}, "
            f"не привязано {s['unlinked']})\n\n"
            f"Не привязанных смотрите: /teachers_unlinked",
            parse_mode="HTML",
        )

    @router.message(
        SearchStates.waiting_teachers_xlsx, admin,
        # Не перехватываем slash-команды — иначе /teacher_link и т.п. не сработают,
        # пока админ застрял в состоянии ожидания файла. None-текст (документы
        # без caption, фото) лямбда тоже пропускает — `not (None and ...)` = True.
        F.text.func(lambda t: not (t and t.startswith("/"))),
    )
    async def on_teachers_xlsx_other(msg: Message, state: FSMContext) -> None:
        await state.clear()
        await msg.answer(
            "Жду .xlsx. Импорт отменён. Запустите снова: /teachers_import"
        )

    @router.message(Command("teachers_unlinked"), admin)
    async def on_teachers_unlinked_cmd(msg: Message) -> None:
        rows = await db.teachers_unlinked(limit=200)
        if not rows:
            await msg.answer("Все учителя из базы привязаны к DSS. ✅")
            return
        lines = [f"<b>Не привязаны</b> ({len(rows)}):"]
        for r in rows:
            name = r["name_en"] or r["name_ru"] or r["name_tg"] or "?"
            subj = r["subject"] or ""
            phone = r["phone"] or ""
            extra = " · ".join(x for x in (subj, phone) if x)
            tail = f" — <i>{extra}</i>" if extra else ""
            lines.append(
                f"• [{r['id']}] {name}{tail}\n"
                f"   <code>/teacher_link {r['id']} &lt;person_id&gt;</code>"
            )
        lines.append(
            "\n💡 person_id ученика/учителя видно в логах событий "
            "или через <code>/find &lt;имя&gt;</code>."
        )
        await _send_long(msg, "\n".join(lines), parse_mode="HTML")

    @router.message(Command("teacher_link"), admin)
    async def on_teacher_link_cmd(
        msg: Message, command: CommandObject
    ) -> None:
        parts = (command.args or "").split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            await msg.answer(
                "Использование: <code>/teacher_link &lt;teacher_id&gt; &lt;person_id&gt;</code>",
                parse_mode="HTML",
            )
            return
        tid, pid = int(parts[0]), parts[1]
        teacher = await db.get_teacher(tid)
        if teacher is None:
            await msg.answer(f"Учитель [{tid}] не найден.")
            return
        await db.link_teacher(tid, pid)
        name = teacher["name_en"] or teacher["name_ru"] or "?"
        # Если этот админ — «владелец» одной или нескольких групп (есть фильтр),
        # автоматически добавляем привязанного учителя в его группы. Логика:
        # человек без фильтра видит всё → ему нечего «приписывать», он работает
        # как глобальный админ. Амриддин с фильтром {Secondary} → новый учитель
        # сразу в Secondary без отдельной команды.
        owner_groups = _restrict_for(msg.chat.id) or frozenset()
        added_to: list[str] = []
        for g in owner_groups:
            if await db.add_person_to_group(pid, g):
                added_to.append(g)
        suffix = ""
        if added_to:
            suffix = f"\n➕ Добавлен в группу: <b>{', '.join(added_to)}</b>"
        elif owner_groups:
            suffix = f"\nℹ️ Уже в группе(ах): {', '.join(sorted(owner_groups))}"
        await msg.answer(
            f"🔗 [{tid}] <b>{name}</b> ↔ DSS person_id <code>{pid}</code>{suffix}",
            parse_mode="HTML",
        )

    @router.message(Command("dss_groups"), admin)
    async def on_dss_groups_cmd(msg: Message) -> None:
        """Покажет список person-групп прямо из DSS — чтобы сверить имена
        с локальными (используются для авто-добавления при /teachers_import
        и мастере «Добавить учителя»)."""
        try:
            groups = await dss_persons.list_groups(force=True)
        except Exception as e:  # noqa: BLE001
            await msg.answer(f"DSS не отвечает: {e!r}")
            return
        if not groups:
            await msg.answer("DSS вернул пустой список групп.")
            return
        lines = ["<b>DSS person-группы:</b>"]
        for g in groups:
            lines.append(f"• <b>{g['name']}</b> — orgCode <code>{g['orgCode']}</code>")
        lines.append(
            "\n💡 Имя локальной группы (Secondary и т.п.) должно совпадать "
            "с именем DSS — иначе авто-регистрация в DSS не сработает."
        )
        await _send_long(msg, "\n".join(lines), parse_mode="HTML")

    @router.message(Command("group_sync"), admin)
    async def on_group_sync_cmd(
        msg: Message, command: CommandObject
    ) -> None:
        """Массовая привязка: всех учителей с person_id из таблицы teachers
        кладёт в указанную группу. Идемпотентно."""
        if not command.args:
            await msg.answer(
                "Использование: <code>/group_sync &lt;группа&gt;</code>\n"
                "Пример: <code>/group_sync Secondary</code>\n"
                "Добавит всех привязанных учителей из <code>/teachers</code> в эту группу.",
                parse_mode="HTML",
            )
            return
        group = command.args.strip()
        added = await db.sync_linked_teachers_to_group(group)
        s = await db.teachers_stats()
        in_group_rows = await db.persons_in_group(group)
        await msg.answer(
            f"✅ Группа <b>{group}</b>: добавлено {added} новых, "
            f"всего в группе {len(in_group_rows)} чел.\n"
            f"(привязанных к DSS учителей в базе: {s['linked']})",
            parse_mode="HTML",
        )

    @router.message(Command("teacher_unlink"), admin)
    async def on_teacher_unlink_cmd(
        msg: Message, command: CommandObject
    ) -> None:
        if not command.args or not command.args.strip().isdigit():
            await msg.answer(
                "Использование: <code>/teacher_unlink &lt;teacher_id&gt;</code>",
                parse_mode="HTML",
            )
            return
        tid = int(command.args.strip())
        teacher = await db.get_teacher(tid)
        if teacher is None:
            await msg.answer(f"Учитель [{tid}] не найден.")
            return
        await db.unlink_teacher(tid)
        await msg.answer(f"🔓 [{tid}] отвязан от DSS.")

    @router.message(Command("photo"), admin)
    async def on_photo_cmd(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        row = await db.last_event_with_image(
            restrict_groups=_restrict_for(msg.chat.id)
        )
        if row is None:
            await msg.answer(t("no_photo_events", lang))
            return
        d = dict(row)
        url = d.get("snapshot_url") or ""
        caption = format_event(d, lang=lang)
        try:
            data = await dss.download_bytes(url)
        except Exception as e:
            await msg.answer(t("photo_dl_fail", lang, e=repr(e), url=url),
                             parse_mode=None)
            return
        filename = url.rsplit("/", 1)[-1] or "photo.jpg"
        try:
            await msg.bot.send_photo(
                chat_id=msg.chat.id,
                photo=BufferedInputFile(data, filename=filename),
                caption=caption, parse_mode="HTML",
            )
        except TelegramAPIError as e:
            await msg.answer(t("photo_tg_reject", lang, n=len(data), e=repr(e)),
                             parse_mode=None)

    # --- FSM-входы (после slash-команд) ---

    @router.message(SearchStates.waiting_name, admin, F.text)
    async def on_find_input(msg: Message, state: FSMContext, lang: str) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        await state.clear()
        if not text:
            await msg.answer(t("empty_query", lang), reply_markup=back_kb(lang))
            return
        await _send_find(msg, text, lang)

    @router.message(SearchStates.waiting_door, admin, F.text)
    async def on_door_input(msg: Message, state: FSMContext, lang: str) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        await state.clear()
        if not text:
            await msg.answer(t("empty_door", lang), reply_markup=back_kb(lang))
            return
        await _send_long(
            msg,
            await _render_door(text, lang, _restrict_for(msg.chat.id)),
            parse_mode="HTML",
        )

    @router.message(SearchStates.waiting_report_from, admin, F.text)
    async def on_report_from_input(msg: Message, state: FSMContext, lang: str) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        d = parse_date_input(text)
        if d is None:
            await msg.answer(t("date_unparsed", lang) + t("date_hint", lang),
                             parse_mode="HTML")
            return
        await state.set_data({"report_from": d.isoformat()})
        await state.set_state(SearchStates.waiting_report_to)
        await msg.answer(
            t("report_to_q", lang, d=d.strftime("%d.%m.%Y")) + t("date_hint", lang),
            parse_mode="HTML",
        )

    @router.message(SearchStates.waiting_report_to, admin, F.text)
    async def on_report_to_input(msg: Message, state: FSMContext, lang: str) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            return
        end_date = parse_date_input(text)
        if end_date is None:
            await msg.answer(t("date_unparsed", lang) + t("date_hint", lang),
                             parse_mode="HTML")
            return
        data = await state.get_data()
        from_iso = data.get("report_from")
        await state.clear()
        if not from_iso:
            await msg.answer(t("report_state_lost", lang))
            return
        start_date = date.fromisoformat(from_iso)
        if end_date < start_date:
            start_date, end_date = end_date, start_date

        start_iso = datetime.combine(start_date, time(0, 0)).isoformat()
        end_iso = datetime.combine(end_date + timedelta(days=1), time(0, 0)).isoformat()

        await msg.answer(
            t("report_preparing", lang,
              a=start_date.strftime("%d.%m.%Y"),
              b=end_date.strftime("%d.%m.%Y")),
            parse_mode="HTML",
        )

        rows = await db.attendance_range(
            start_iso, end_iso, restrict_groups=_restrict_for(msg.chat.id)
        )
        if not rows:
            await msg.answer(t("report_no_data", lang))
            return

        xlsx_bytes = generate_attendance_xlsx(
            [dict(r) for r in rows], work_day_start, work_day_end, lang=lang
        )
        filename = (
            f"report_{start_date.strftime('%d%m')}_"
            f"{end_date.strftime('%d%m')}.xlsx"
        )
        await msg.bot.send_document(
            chat_id=msg.chat.id,
            document=BufferedInputFile(xlsx_bytes, filename=filename),
            caption=t("report_caption", lang,
                      a=start_date.strftime("%d.%m.%Y"),
                      b=end_date.strftime("%d.%m.%Y"),
                      n=len(rows)),
            reply_markup=back_kb(lang, "mon"),
        )
