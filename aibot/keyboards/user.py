"""Keyboards for regular users."""

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from aibot.database.pinned import get_answer, is_pinned
from aibot.database.users import Prefs
from aibot.i18n import LANGS, MODES, t


def get_main_menu(lang: str):
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text=t(lang, "btn_ask")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_mode")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_clear")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_info")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_settings")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_invite")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_help")))
    builder.adjust(2, 2, 3)
    return builder.as_markup(resize_keyboard=True)


def answer_kb(lang: str, regen: bool, save_state: str | None):
    """Buttons under a reply: Regenerate/Continue (regen) and Save (save_state: 'save' / 'saved' / None)."""
    if not regen and not save_state:
        return None
    b = InlineKeyboardBuilder()
    sizes = []
    if regen:
        b.button(text=t(lang, "btn_regen"), callback_data="re:regen")
        b.button(text=t(lang, "btn_cont"), callback_data="re:cont")
        sizes.append(2)
    if save_state:
        b.button(
            text=t(lang, "btn_saved" if save_state == "saved" else "btn_save"), callback_data="sv:add"
        )
        sizes.append(1)
    b.adjust(*sizes)
    return b.as_markup()


async def build_answer_kb(user_id: int, message_id: int, lang: str, regen: bool, save_on: bool):
    """Keyboard for an already-sent reply, reflecting whether it is saved."""
    state = None
    if save_on:
        ans = await get_answer(user_id, message_id)
        state = "saved" if ans is not None and await is_pinned(user_id, ans) else "save"
    return answer_kb(lang, regen, state)


def stop_kb(lang: str):
    b = InlineKeyboardBuilder()
    b.button(text=t(lang, "btn_stop"), callback_data="st:stop")
    return b.as_markup()


def modes_kb(current: str, lang: str):
    b = InlineKeyboardBuilder()
    for key, m in MODES.items():
        b.button(text=("✅ " if key == current else "") + m["title"][lang], callback_data=f"mode:{key}")
    b.adjust(1)
    return b.as_markup()


def settings_kb(lang: str, prefs: Prefs):
    def state(v: bool) -> str:
        return t(lang, "on" if v else "off")

    b = InlineKeyboardBuilder()
    for code, name in LANGS.items():
        b.button(text=("✅ " if code == lang else "") + name, callback_data=f"set:lang:{code}")
    b.button(text=t(lang, "btn_buttons", state=state(prefs.buttons)), callback_data="set:btns")
    b.button(text=t(lang, "btn_savebtn", state=state(prefs.save)), callback_data="set:save")
    b.button(text=t(lang, "btn_web", state=state(prefs.web)), callback_data="set:web")
    b.button(
        text=t(lang, "btn_think", state=t(lang, "think_deep" if prefs.think else "think_fast")),
        callback_data="set:think",
    )
    b.adjust(2, 1, 1, 1, 1)
    return b.as_markup()


def clear_kb(lang: str, has_pinned: bool):
    b = InlineKeyboardBuilder()
    if has_pinned:
        b.button(text=t(lang, "btn_clear_all"), callback_data="cl:all")
        b.button(text=t(lang, "btn_clear_keep"), callback_data="cl:keep")
        b.button(text=t(lang, "btn_cancel"), callback_data="cl:no")
        b.adjust(1)
    else:
        b.button(text=t(lang, "btn_clear_yes"), callback_data="cl:all")
        b.button(text=t(lang, "btn_cancel"), callback_data="cl:no")
        b.adjust(2)
    return b.as_markup()
