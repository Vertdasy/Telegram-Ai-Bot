"""Dialog clearing, saved messages and export."""

import html
import time

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from aibot.config import PINNED_MAX
from aibot.database.history import clear_history, count_history, get_memory_len, load_history
from aibot.database.pinned import add_pinned, count_pinned, get_answer, list_pinned
from aibot.database.users import get_lang, get_prefs
from aibot.i18n import BTN_CLEAR, t
from aibot.keyboards.user import answer_kb, clear_kb
from aibot.state import bot, db, last_kb_msg, user_router
from aibot.utils.text import fmt_ts


# --- clearing the conversation (with confirmation) ---
@user_router.message(Command("clear", "reset"))
@user_router.message(F.text.in_(BTN_CLEAR))
async def cmd_clear(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    n = await count_history(uid)
    p = await count_pinned(uid)
    if n == 0 and p == 0:
        await message.answer(t(lang, "clear_empty"))
        return
    key = "clear_confirm_pinned" if p else "clear_confirm"
    await message.answer(
        t(lang, key, n=n, p=p), parse_mode="HTML", reply_markup=clear_kb(lang, p > 0)
    )


@user_router.callback_query(F.data.startswith("cl:"))
async def cb_clear(cb: types.CallbackQuery):
    uid = cb.from_user.id
    lang = await get_lang(uid)
    action = cb.data.split(":", 1)[1]
    if action == "no":
        text = t(lang, "clear_cancelled")
    elif action == "keep":
        p = await count_pinned(uid)
        await clear_history(uid, pinned=False)
        text = t(lang, "cleared_keep", p=p) if p else t(lang, "cleared")
    elif action == "all":
        await clear_history(uid)
        text = t(lang, "cleared")
    else:
        await cb.answer()
        return
    last_kb_msg.pop(uid, None)
    try:
        await cb.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await cb.answer()


# --- saved messages ---
def _preview(text: str, n: int = 140) -> str:
    s = " ".join(text.split())
    return s if len(s) <= n else s[: n - 1] + "…"


async def render_saved(uid: int, lang: str):
    rows = await list_pinned(uid)
    if not rows:
        return t(lang, "saved_empty"), None
    items = "\n\n".join(
        f"<b>{i}.</b> {html.escape(_preview(r['content']))}" for i, r in enumerate(rows, 1)
    )
    text = t(lang, "saved_title", n=len(rows), max=PINNED_MAX, items=items)
    b = InlineKeyboardBuilder()
    for i, r in enumerate(rows, 1):
        b.button(text=f"🗑 {i}", callback_data=f"sv:del:{r['id']}")
    b.button(text=t(lang, "btn_del_all"), callback_data="sv:clr")
    k = len(rows)
    sizes = [5] * (k // 5) + ([k % 5] if k % 5 else []) + [1]
    b.adjust(*sizes)
    return text, b.as_markup()


@user_router.message(Command("saved"))
async def cmd_saved(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    text, kb = await render_saved(uid, lang)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@user_router.callback_query(F.data.startswith("sv:"))
async def cb_save(cb: types.CallbackQuery):
    uid = cb.from_user.id
    prefs = await get_prefs(uid)
    lang = prefs.lang
    parts = cb.data.split(":")
    action = parts[1]

    if action == "add":
        content = await get_answer(uid, cb.message.message_id)
        if content is None:
            await cb.answer(t(lang, "save_gone"), show_alert=True)
            return
        status = await add_pinned(uid, content)
        if status == "limit":
            await cb.answer(t(lang, "save_limit", n=PINNED_MAX), show_alert=True)
            return
        regen = prefs.buttons and last_kb_msg.get(uid) == (cb.message.chat.id, cb.message.message_id)
        try:
            await cb.message.edit_reply_markup(reply_markup=answer_kb(lang, regen, "saved"))
        except TelegramBadRequest:
            pass
        await cb.answer(t(lang, "save_ok" if status == "ok" else "save_dup"))
        return

    if action == "del" and len(parts) > 2 and parts[2].isdigit():
        await db.execute("DELETE FROM pinned WHERE id=? AND user_id=?", (int(parts[2]), uid))
        await db.commit()
    elif action == "clr":
        await db.execute("DELETE FROM pinned WHERE user_id=?", (uid,))
        await db.commit()
    else:
        await cb.answer()
        return
    text, kb = await render_saved(uid, lang)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass
    await cb.answer(t(lang, "saved_deleted"))


# --- conversation export ---
@user_router.message(Command("export"))
async def cmd_export(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    hist = await load_history(uid)
    pins = await list_pinned(uid)
    if not hist and not pins:
        await message.answer(t(lang, "export_empty"))
        return
    lines: list[str] = []
    for m in hist:
        who = t(lang, "export_you" if m["role"] == "user" else "export_ai")
        lines.append(f"{who}:\n{m['content']}\n")
    if pins:
        lines.append(f"— {t(lang, 'export_saved')} —\n")
        for i, r in enumerate(pins, 1):
            lines.append(f"[{i}] {r['content']}\n")
    data = "\n".join(lines).encode("utf-8")
    name = f"dialog-{fmt_ts(int(time.time()), '%Y%m%d-%H%M')}.txt"
    await bot.send_document(
        message.chat.id,
        BufferedInputFile(data, filename=name),
        caption=t(lang, "export_caption", n=len(hist), m=await get_memory_len()),
    )
