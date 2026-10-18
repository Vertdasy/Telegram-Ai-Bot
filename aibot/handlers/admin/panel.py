"""Admin panel: /admin command and inline-button callbacks."""

import asyncio
import html
import logging
import os
import tempfile
import time

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile

from aibot.config import (
    ADMIN_IDS, EFFORT_FAST, EFFORT_THINK, EFFORTS, FALLBACK_MODEL, MEMORY_MAX, MEMORY_MIN, MODEL,
    PROMPT_MAX,
)
from aibot.database.history import apply_memory_len, get_history_chars, get_memory_len
from aibot.database.settings import del_setting, get_setting, set_setting
from aibot.database.users import get_lang, set_user_field
from aibot.handlers.admin.broadcast import do_broadcast
from aibot.handlers.admin.states import Admin
from aibot.handlers.admin.views import (
    errors_text, memory_text, prompt_menu_text, prompt_mode_text, ref_text, run_speedtest, sources_text,
    speed_text, stats_text, user_card, users_text, web_text,
)
from aibot.i18n import LANGS, MODES, t
from aibot.keyboards.admin import (
    admin_kb, effort_kb, fallback_kb, limits_kb, memory_kb, model_kb, prompt_kb, prompt_mode_kb, ref_kb,
    speed_kb, src_kb, web_kb,
)
from aibot.services.limits import fmt_limit, get_limits
from aibot.services.prompts import prompt_key
from aibot.services.referral import get_ref_cfg
from aibot.services.websearch import web_enabled
from aibot.state import admin_router, bot, db, STATE
from aibot.utils.tasks import spawn
from aibot.utils.text import fmt_ts


async def show(cb: types.CallbackQuery, text: str, kb=None):
    """Edits the panel message in place (the admin menu is the default keyboard)."""
    if kb is None:
        kb = admin_kb(await get_lang(cb.from_user.id))
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass
    await cb.answer()


@admin_router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "adm_title"), parse_mode="HTML", reply_markup=admin_kb(lang))


@admin_router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "adm_cancelled"), reply_markup=admin_kb(lang))


async def ask(cb: types.CallbackQuery, state: FSMContext, action: str, text: str, **data):
    """Switches to the 'waiting for input' state and shows the question."""
    await state.set_state(Admin.waiting)
    await state.update_data(action=action, **data)
    await show(cb, text)


@admin_router.callback_query(F.data.startswith("adm:"))
async def admin_callbacks(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split(":", 2)
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else ""
    lang = await get_lang(cb.from_user.id)

    if action == "menu":
        await state.clear()
        await show(cb, t(lang, "adm_title"))

    elif action == "lang":
        codes = list(LANGS)
        new = codes[(codes.index(lang) + 1) % len(codes)] if lang in codes else codes[0]
        await set_user_field(cb.from_user.id, "lang", new)
        await show(cb, t(new, "lang_changed") + "\n\n" + t(new, "adm_title"), admin_kb(new))

    elif action == "stats":
        await show(cb, await stats_text(lang))

    elif action == "users":
        await show(cb, await users_text(lang))

    elif action == "errors":
        await show(cb, await errors_text(lang))

    elif action == "broadcast":
        await ask(cb, state, "broadcast", t(lang, "adm_ask_broadcast"))

    elif action in ("ban", "unban"):
        await ask(cb, state, action, t(lang, f"adm_ask_{action}"))

    elif action in ("ban_id", "unban_id"):
        if not arg.isdigit():
            await cb.answer()
            return
        uid = int(arg)
        if action == "ban_id" and uid in ADMIN_IDS:
            await cb.answer(t(lang, "adm_cannot_ban_admin"), show_alert=True)
            return
        await db.execute("UPDATE users SET banned=? WHERE id=?", (1 if action == "ban_id" else 0, uid))
        await db.commit()
        text, kb = await user_card(lang, uid)
        await show(cb, text or t(lang, "adm_user_missing"), kb)

    elif action == "userinfo":
        await ask(cb, state, "userinfo", t(lang, "adm_ask_userinfo"))

    elif action == "model":
        current = await get_setting("model", MODEL)
        await show(cb, t(lang, "adm_model", model=html.escape(current)), model_kb(lang, current))

    elif action == "m":
        await set_setting("model", arg)
        await show(cb, t(lang, "adm_model_set", model=html.escape(arg)), model_kb(lang, arg))

    elif action == "model_custom":
        await ask(cb, state, "model", t(lang, "adm_ask_model"))

    elif action == "prompt":
        await state.clear()
        await show(cb, await prompt_menu_text(lang), prompt_kb(lang))

    elif action == "pm":
        if arg not in MODES:
            await cb.answer()
            return
        await state.clear()
        await show(cb, await prompt_mode_text(lang, arg), prompt_mode_kb(lang, arg))

    elif action == "pe":
        mode, _, code = arg.partition(":")
        if mode not in MODES or code not in LANGS:
            await cb.answer()
            return
        await ask(
            cb, state, "prompt",
            t(lang, "adm_ask_prompt", title=MODES[mode]["title"][lang], code=code.upper(), max=PROMPT_MAX),
            pmode=mode, plang=code,
        )

    elif action == "pr":
        if arg not in MODES:
            await cb.answer()
            return
        for code in LANGS:
            await del_setting(prompt_key(arg, code))
        await show(
            cb, t(lang, "adm_prompt_reset") + await prompt_mode_text(lang, arg), prompt_mode_kb(lang, arg)
        )

    elif action == "mem":
        await state.clear()
        await show(cb, await memory_text(lang), memory_kb(lang, await get_memory_len()))

    elif action == "memset":
        if arg.isdigit() and MEMORY_MIN <= int(arg) <= MEMORY_MAX:
            await apply_memory_len(int(arg))
        await show(cb, await memory_text(lang), memory_kb(lang, await get_memory_len()))

    elif action == "mem_custom":
        await ask(cb, state, "memory", t(lang, "adm_ask_memory", min=MEMORY_MIN, max=MEMORY_MAX))

    elif action == "hist_chars":
        await ask(cb, state, "hist_chars", t(lang, "adm_ask_hist_chars", n=await get_history_chars()))

    elif action == "ref":
        await state.clear()
        await show(cb, await ref_text(lang), ref_kb(lang))

    elif action in ("ref_bonus", "ref_max"):
        bonus, mx = await get_ref_cfg()
        await ask(cb, state, action, t(lang, f"adm_ask_{action}", n=bonus if action == "ref_bonus" else mx))

    elif action == "web":
        await show(cb, await web_text(lang), web_kb(lang, await web_enabled()))

    elif action == "web_toggle":
        await set_setting("web_on", "0" if await web_enabled() else "1")
        await show(cb, await web_text(lang), web_kb(lang, await web_enabled()))

    elif action == "src":
        await show(cb, await sources_text(lang), src_kb(lang))

    elif action == "limits":
        per_min, per_day = await get_limits()
        await show(
            cb,
            t(lang, "adm_limits", per_min=fmt_limit(per_min, lang), per_day=fmt_limit(per_day, lang)),
            limits_kb(lang),
        )

    elif action in ("limit_min", "limit_day"):
        what = t(lang, "adm_per_min" if action == "limit_min" else "adm_per_day")
        await ask(cb, state, action, t(lang, "adm_ask_limit", what=what))

    elif action == "speed_menu":
        await show(cb, await speed_text(lang), speed_kb(lang))

    elif action == "eff_pick":
        if arg not in ("fast", "think"):
            await cb.answer()
            return
        current = await get_setting(f"effort_{arg}", EFFORT_FAST if arg == "fast" else EFFORT_THINK)
        await show(
            cb, t(lang, "adm_eff_pick", title=t(lang, f"adm_eff_title_{arg}")), effort_kb(lang, arg, current)
        )

    elif action == "eff":
        which, _, val = arg.partition(":")
        if which in ("fast", "think") and val in EFFORTS:
            await set_setting(f"effort_{which}", val)
        await show(cb, await speed_text(lang), speed_kb(lang))

    elif action == "fb_pick":
        current = await get_setting("fallback_model", FALLBACK_MODEL)
        await show(cb, t(lang, "adm_fb_pick"), fallback_kb(lang, current))

    elif action == "fb":
        await set_setting("fallback_model", arg)
        await show(cb, await speed_text(lang), speed_kb(lang))

    elif action == "speedtest":
        if STATE["speedtest"]:
            await cb.answer(t(lang, "adm_st_already"), show_alert=True)
            return
        await cb.answer(t(lang, "adm_st_starting"))
        await run_speedtest(lang, cb.message)

    elif action == "backup":
        await cb.answer(t(lang, "adm_backup_prep"))
        path = os.path.join(tempfile.gettempdir(), f"bot-backup-{int(time.time())}.db")
        try:
            await db.commit()
            await db.execute("VACUUM INTO ?", (path,))

            def _read() -> bytes:
                with open(path, "rb") as f:
                    return f.read()

            data = await asyncio.to_thread(_read)
            name = f"bot-backup-{fmt_ts(int(time.time()), '%Y%m%d-%H%M')}.db"
            await bot.send_document(
                cb.message.chat.id, BufferedInputFile(data, filename=name), caption=t(lang, "adm_backup_caption")
            )
        except Exception as e:
            logging.exception("Backup failed")
            await bot.send_message(cb.message.chat.id, t(lang, "adm_backup_fail", e=e))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    elif action == "maint":
        STATE["maintenance"] = not STATE["maintenance"]
        await set_setting("maintenance", "1" if STATE["maintenance"] else "0")
        note = t(lang, "adm_maint_on" if STATE["maintenance"] else "adm_maint_off")
        await show(cb, t(lang, "adm_title") + "\n\n" + note)

    elif action == "bc_yes":
        data = await state.get_data()
        text = data.get("bc_text")
        await state.clear()
        if not text:
            await show(cb, t(lang, "adm_bc_nothing"))
            return
        spawn(do_broadcast(text, cb.message.chat.id, lang))
        await show(cb, t(lang, "adm_bc_started"))

    elif action == "bc_no":
        await state.clear()
        await show(cb, t(lang, "adm_bc_cancelled"))

    else:
        await cb.answer()
