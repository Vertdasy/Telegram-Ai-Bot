"""Admin panel: handling of text typed by the admin while the FSM is waiting for input."""

import html

from aiogram import F, types
from aiogram.fsm.context import FSMContext

from aibot.config import ADMIN_IDS, MEMORY_MAX, MEMORY_MIN, PROMPT_MAX
from aibot.database.history import apply_memory_len, get_memory_len
from aibot.database.settings import scalar, set_setting
from aibot.database.users import get_lang
from aibot.handlers.admin.states import Admin
from aibot.handlers.admin.views import memory_text, prompt_mode_text, ref_text, user_card
from aibot.i18n import LANGS, MODES, t
from aibot.keyboards.admin import admin_kb, confirm_kb, memory_kb, prompt_mode_kb, ref_kb
from aibot.services.prompts import prompt_key
from aibot.state import admin_router, db


@admin_router.message(Admin.waiting, F.text)
async def admin_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    action = data.get("action")
    text = message.text.strip()
    lang = await get_lang(message.from_user.id)

    if action in ("ban", "unban"):
        if not text.lstrip("-").isdigit():
            await message.answer(t(lang, "adm_id_not_number"))
            return
        uid = int(text)
        if action == "ban" and uid in ADMIN_IDS:
            await message.answer(t(lang, "adm_cannot_ban_admin_msg"))
            return
        cur = await db.execute("UPDATE users SET banned=? WHERE id=?", (1 if action == "ban" else 0, uid))
        await db.commit()
        await state.clear()
        if cur.rowcount:
            done = t(lang, "adm_banned" if action == "ban" else "adm_unbanned")
            await message.answer(t(lang, "adm_ban_done", uid=uid, done=done), reply_markup=admin_kb(lang))
        else:
            await message.answer(t(lang, "adm_no_such_user"), reply_markup=admin_kb(lang))

    elif action == "userinfo":
        if not text.lstrip("-").isdigit():
            await message.answer(t(lang, "adm_id_not_number"))
            return
        card, kb = await user_card(lang, int(text))
        await state.clear()
        if card:
            await message.answer(card, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(t(lang, "adm_no_such_user"), reply_markup=admin_kb(lang))

    elif action == "model":
        await set_setting("model", text)
        await state.clear()
        await message.answer(
            t(lang, "adm_model_set", model=html.escape(text)), parse_mode="HTML", reply_markup=admin_kb(lang)
        )

    elif action == "prompt":
        pmode = data.get("pmode", "default")
        plang = data.get("plang", "ru")
        if pmode not in MODES or plang not in LANGS:
            await state.clear()
            await message.answer(t(lang, "adm_prompt_error"))
            return
        if len(text) > PROMPT_MAX:
            await message.answer(t(lang, "adm_prompt_too_long", n=len(text), max=PROMPT_MAX))
            return
        await set_setting(prompt_key(pmode, plang), text)
        await state.clear()
        await message.answer(
            t(lang, "adm_prompt_updated", title=MODES[pmode]["title"][lang], code=plang.upper())
            + await prompt_mode_text(lang, pmode),
            parse_mode="HTML",
            reply_markup=prompt_mode_kb(lang, pmode),
        )

    elif action == "memory":
        if not text.isdigit() or not (MEMORY_MIN <= int(text) <= MEMORY_MAX):
            await message.answer(t(lang, "adm_memory_bad_number", min=MEMORY_MIN, max=MEMORY_MAX))
            return
        await apply_memory_len(int(text))
        await state.clear()
        await message.answer(
            await memory_text(lang), parse_mode="HTML", reply_markup=memory_kb(lang, await get_memory_len())
        )

    elif action == "hist_chars":
        if not text.isdigit() or int(text) < 1000:
            await message.answer(t(lang, "adm_hist_bad_number"))
            return
        await set_setting("history_chars", str(int(text)))
        await state.clear()
        await message.answer(
            await memory_text(lang), parse_mode="HTML", reply_markup=memory_kb(lang, await get_memory_len())
        )

    elif action in ("ref_bonus", "ref_max"):
        top = 10000 if action == "ref_bonus" else 1000
        if not text.isdigit() or int(text) > top:
            await message.answer(t(lang, "adm_bad_number_range", top=top))
            return
        await set_setting(action, str(int(text)))
        await state.clear()
        await message.answer(await ref_text(lang), parse_mode="HTML", reply_markup=ref_kb(lang))

    elif action in ("limit_min", "limit_day"):
        if not text.isdigit():
            await message.answer(t(lang, "adm_limit_bad"))
            return
        await set_setting("rate_min" if action == "limit_min" else "rate_day", str(int(text)))
        await state.clear()
        await message.answer(t(lang, "adm_limit_updated"), reply_markup=admin_kb(lang))

    elif action == "broadcast":
        total = await scalar("SELECT COUNT(*) FROM users WHERE banned=0")
        await state.set_state(Admin.confirm)
        await state.update_data(bc_text=text)
        await message.answer(t(lang, "adm_bc_confirm", total=total, text=text[:3000]), reply_markup=confirm_kb(lang))
