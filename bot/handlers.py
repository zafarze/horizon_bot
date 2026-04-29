"""aiogram-роутер: inline-меню + slash-команды + переключение языка.

`lang` приходит в каждый хендлер из `LangMiddleware` через `data["lang"]`.
"""
from __future__ import annotations

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
    format_today,
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
from .xlsx import generate_attendance_xlsx, generate_workers_xlsx


class SearchStates(StatesGroup):
    waiting_name = State()
    waiting_door = State()
    waiting_report_from = State()
    waiting_report_to = State()


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
        is_persistent=True,
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


def back_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("menu_back", lang), callback_data="m:menu")]
    ])


def cancel_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("menu_cancel", lang), callback_data="m:menu")]
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
) -> None:
    admin = AdminFilter(admin_ids)
    mw = LangMiddleware(resolver)
    router.message.middleware(mw)
    router.callback_query.middleware(mw)

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

    async def _render_inside(lang: str) -> str:
        rows = await db.list_inside()
        return format_inside_list([dict(r) for r in rows], lang=lang)

    async def _render_today(lang: str) -> str:
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        stats = await db.stats_today(start, end)
        inside = await db.count_inside()
        return format_today(stats, inside, lang=lang)

    async def _render_attendance(lang: str) -> str:
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        rows = await db.attendance_today(start, end)
        return format_attendance(
            [dict(r) for r in rows], work_day_start, work_day_end, lang=lang
        )

    async def _render_late(lang: str) -> str:
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        rows = await db.attendance_today(start, end)
        return format_late(
            [dict(r) for r in rows], work_day_start, work_day_end, lang=lang
        )

    async def _render_absent(lang: str) -> str:
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        lookback_start = today_start - timedelta(days=30)
        rows = await db.absent_today(today_start, today_end, lookback_start)
        return format_absent([dict(r) for r in rows], lang=lang)

    async def _render_workers(lang: str) -> str:
        lookback_start = datetime.now() - timedelta(days=30)
        rows = await db.list_known_persons(lookback_start)
        return format_workers([dict(r) for r in rows], lang=lang)

    async def _send_find(msg: Message, query: str, lang: str, photo_limit: int = 5) -> None:
        rows = await db.find_by_name(query, limit=10)
        dicts = [dict(r) for r in rows]
        photo_rows = (
            await db.find_by_name_with_image(query, limit=photo_limit)
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

    async def _render_door(door: str, lang: str) -> str:
        since = datetime.now() - timedelta(hours=12)
        rows = await db.events_by_door(door, since, limit=30)
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
        await _send_long(msg, await _render_today(lang),
                         parse_mode="HTML", final_markup=back_kb(lang))

    @router.message(btn("late"), admin)
    async def on_btn_late(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        await _send_long(msg, await _render_late(lang),
                         parse_mode="HTML", final_markup=back_kb(lang))

    @router.message(btn("absent"), admin)
    async def on_btn_absent(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        await _send_long(msg, await _render_absent(lang),
                         parse_mode="HTML", final_markup=back_kb(lang))

    @router.message(btn("report"), admin)
    async def on_btn_report(msg: Message, state: FSMContext, lang: str) -> None:
        await state.set_state(SearchStates.waiting_report_from)
        await msg.answer(
            t("report_from_q", lang) + t("date_hint", lang),
            parse_mode="HTML",
        )

    @router.message(btn("workers"), admin)
    async def on_btn_workers(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        lookback_start = datetime.now() - timedelta(days=30)
        rows = await db.list_known_persons(lookback_start)
        if not rows:
            # Файл из 0 строк бесполезен — отвечаем текстом.
            await msg.answer(t("workers_empty", lang), reply_markup=back_kb(lang))
            return
        dicts = [dict(r) for r in rows]
        xlsx_bytes = generate_workers_xlsx(dicts, lang=lang)
        filename = (
            f"{t('workers_filename', lang)}_"
            f"{datetime.now().strftime('%Y%m%d')}.xlsx"
        )
        await msg.bot.send_document(
            chat_id=msg.chat.id,
            document=BufferedInputFile(xlsx_bytes, filename=filename),
            caption=t("workers_caption", lang, n=len(dicts)),
            reply_markup=back_kb(lang),
        )

    @router.message(btn("find"), admin)
    async def on_btn_find(msg: Message, state: FSMContext, lang: str) -> None:
        await state.set_state(SearchStates.waiting_name)
        await msg.answer(t("ask_find_name", lang))

    # --- callback: возврат в меню ---

    @router.callback_query(F.data == "m:menu")
    async def cb_menu(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await state.clear()
        await cb.answer()
        msg = cb.message
        if msg is None:
            return
        try:
            await msg.edit_text(t("menu_text", lang), reply_markup=main_menu_kb(lang),
                                parse_mode="HTML")
        except TelegramAPIError as e:
            logger.warning("cb_menu edit failed: {} - sending fresh menu", e)
            try:
                await msg.answer(t("menu_text", lang), reply_markup=main_menu_kb(lang),
                                 parse_mode="HTML")
            except TelegramAPIError as e2:
                logger.warning("cb_menu answer also failed: {}", e2)

    # --- callback: read-only ---

    @router.callback_query(F.data == "m:inside")
    async def cb_inside(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await cb.message.edit_text(await _render_inside(lang),
                                   reply_markup=back_kb(lang), parse_mode="HTML")
        await cb.answer()

    @router.callback_query(F.data == "m:today")
    async def cb_today(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await cb.message.edit_text(await _render_today(lang),
                                   reply_markup=back_kb(lang), parse_mode="HTML")
        await cb.answer()

    @router.callback_query(F.data == "m:attend")
    async def cb_attend(cb: CallbackQuery, lang: str) -> None:
        if not await _check_admin_cb(cb, lang):
            return
        await cb.answer(t("calculating", lang))
        await cb.message.edit_text(await _render_attendance(lang),
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
        await _send_long(msg, await _render_inside(lang),
                         parse_mode="HTML", final_markup=back_kb(lang))

    @router.message(Command("today"), admin)
    async def on_today_cmd(msg: Message, lang: str) -> None:
        await msg.answer(await _render_today(lang),
                         reply_markup=back_kb(lang), parse_mode="HTML")

    @router.message(Command("attendance"), admin)
    async def on_attend_cmd(msg: Message, lang: str) -> None:
        await _send_long(msg, await _render_attendance(lang),
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
        await _send_long(msg, await _render_door(command.args.strip(), lang),
                         parse_mode="HTML")

    @router.message(Command("health"), admin)
    async def on_health_cmd(msg: Message, lang: str) -> None:
        await msg.answer(await _render_health(lang),
                         reply_markup=back_kb(lang), parse_mode="HTML")

    @router.message(Command("dss_ping"), admin)
    async def on_dssping_cmd(msg: Message, lang: str) -> None:
        await msg.answer(await _render_dssping(lang), reply_markup=back_kb(lang))

    @router.message(Command("photo"), admin)
    async def on_photo_cmd(msg: Message, state: FSMContext, lang: str) -> None:
        await state.clear()
        row = await db.last_event_with_image()
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
        await _send_long(msg, await _render_door(text, lang), parse_mode="HTML")

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

        rows = await db.attendance_range(start_iso, end_iso)
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
        )
