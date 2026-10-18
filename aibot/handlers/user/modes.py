"""Mode selection."""

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command

from aibot.database.history import clear_history
from aibot.database.pinned import count_pinned
from aibot.database.users import get_lang, get_mode, set_user_field
from aibot.i18n import BTN_MODE, MODES, modes_list, t
from aibot.keyboards.user import modes_kb
from aibot.state import user_router


# --- modes ---
@user_router.message(Command("mode"))
@user_router.message(F.text.in_(BTN_MODE))
async def cmd_mode(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    current = await get_mode(uid)
    await message.answer(
        t(lang, "mode_choose", list=modes_list(lang, current)),
        parse_mode="HTML",
        reply_markup=modes_kb(current, lang),
    )


@user_router.callback_query(F.data.startswith("mode:"))
async def cb_mode(cb: types.CallbackQuery):
    key = cb.data.split(":", 1)[1]
    if key not in MODES:
        await cb.answer()
        return
    uid = cb.from_user.id
    lang = await get_lang(uid)
    if key == await get_mode(uid):
        await cb.answer(t(lang, "mode_same"))
        return
    await set_user_field(uid, "mode", key)
    # reset the previous dialogue so it doesn't interfere with the new mode; saved messages remain
    await clear_history(uid, pinned=False, answers=False)
    p = await count_pinned(uid)
    try:
        await cb.message.edit_text(
            t(
                lang,
                "mode_set",
                mode=MODES[key]["title"][lang],
                desc=MODES[key]["desc"][lang],
                pinned=t(lang, "mode_pinned", n=p) if p else "",
            ),
            parse_mode="HTML",
            reply_markup=modes_kb(key, lang),
        )
    except TelegramBadRequest:
        pass
    await cb.answer()
