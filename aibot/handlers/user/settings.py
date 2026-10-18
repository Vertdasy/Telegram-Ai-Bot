"""Settings, personal instructions and the info screen."""

import html
import time

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject

from aibot.config import ADMIN_IDS, CUSTOM_PROMPT_MAX, MAX_IMAGES, MODEL
from aibot.database.history import count_history, get_memory_len
from aibot.database.pinned import count_pinned
from aibot.database.settings import get_setting, scalar
from aibot.database.users import get_custom_prompt, get_lang, get_mode, get_prefs, set_user_field
from aibot.i18n import BTN_INFO, BTN_SETTINGS, LANGS, MODES, t
from aibot.keyboards.user import get_main_menu, settings_kb
from aibot.services.limits import fmt_limit, get_bonus, get_limits
from aibot.services.websearch import web_enabled
from aibot.state import bot, user_router


# --- settings ---
@user_router.message(Command("settings"))
@user_router.message(F.text.in_(BTN_SETTINGS))
async def cmd_settings(message: types.Message):
    prefs = await get_prefs(message.from_user.id)
    await message.answer(
        t(prefs.lang, "settings"), parse_mode="HTML", reply_markup=settings_kb(prefs.lang, prefs)
    )


@user_router.callback_query(F.data.startswith("set:"))
async def cb_settings(cb: types.CallbackQuery):
    uid = cb.from_user.id
    parts = cb.data.split(":")
    prefs = await get_prefs(uid)

    if parts[1] == "lang" and len(parts) > 2 and parts[2] in LANGS:
        if parts[2] != prefs.lang:
            await set_user_field(uid, "lang", parts[2])
            # a reply keyboard's button text can't be changed, so send a new one
            await bot.send_message(
                cb.message.chat.id, t(parts[2], "lang_changed"), reply_markup=get_main_menu(parts[2])
            )
    elif parts[1] == "btns":
        await set_user_field(uid, "buttons", 0 if prefs.buttons else 1)
    elif parts[1] == "save":
        await set_user_field(uid, "save_btn", 0 if prefs.save else 1)
    elif parts[1] == "web":
        await set_user_field(uid, "web", 0 if prefs.web else 1)
    elif parts[1] == "think":
        await set_user_field(uid, "think", 0 if prefs.think else 1)

    prefs = await get_prefs(uid)
    try:
        await cb.message.edit_text(
            t(prefs.lang, "settings"), parse_mode="HTML", reply_markup=settings_kb(prefs.lang, prefs)
        )
    except TelegramBadRequest:
        pass
    await cb.answer()


@user_router.message(Command("myprompt"))
async def cmd_myprompt(message: types.Message, command: CommandObject):
    uid = message.from_user.id
    lang = await get_lang(uid)
    arg = (command.args or "").strip()
    if not arg:
        current = await get_custom_prompt(uid)
        await message.answer(
            t(lang, "myprompt_help", current=html.escape(current) if current else t(lang, "none")),
            parse_mode="HTML",
        )
    elif arg.lower() in ("reset", "clear", "off", "сброс"):
        await set_user_field(uid, "custom_prompt", None)
        await message.answer(t(lang, "myprompt_reset"))
    else:
        await set_user_field(uid, "custom_prompt", arg[:CUSTOM_PROMPT_MAX])
        await message.answer(t(lang, "myprompt_saved"))


# --- info ---
@user_router.message(F.text.in_(BTN_INFO))
async def cmd_info(message: types.Message):
    uid = message.from_user.id
    prefs = await get_prefs(uid)
    lang = prefs.lang
    model = await get_setting("model", MODEL)
    mode = await get_mode(uid)
    memory = await get_memory_len()
    used = await count_history(uid)
    pinned = await count_pinned(uid)
    if uid in ADMIN_IDS:
        limits = t(lang, "limits_admin")
    else:
        per_min, per_day = await get_limits()
        if per_day > 0:
            per_day += await get_bonus(uid)
        spent = await scalar(
            "SELECT COUNT(*) FROM requests WHERE user_id=? AND ts>?", (uid, int(time.time()) - 86400)
        )
        limits = t(
            lang, "limits_fmt", pm=fmt_limit(per_min, lang), pd=fmt_limit(per_day, lang), u=spent
        )
    await message.answer(
        t(
            lang,
            "info",
            model=html.escape(model),
            mode=MODES[mode]["title"][lang],
            think=t(lang, "think_deep" if prefs.think else "think_fast"),
            language=LANGS[lang],
            used=used,
            n=memory,
            pairs=memory // 2,
            pinned=pinned,
            k=MAX_IMAGES,
            limits=limits,
            web=t(lang, "on" if (prefs.web and await web_enabled()) else "off"),
            bonus=await get_bonus(uid),
        ),
        parse_mode="HTML",
    )
