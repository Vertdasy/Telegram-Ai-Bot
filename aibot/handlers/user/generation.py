"""Ask button, Regenerate/Continue and Stop buttons."""

from aiogram import F, types

from aibot.database.history import load_history
from aibot.database.users import get_lang
from aibot.i18n import BTN_ASK, t
from aibot.services.ai import run_ai
from aibot.state import active_gens, last_images, user_router
from aibot.utils.text import PHOTO_RE


@user_router.message(F.text.in_(BTN_ASK))
async def cmd_ask(message: types.Message):
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "ask"))


# --- Regenerate / Continue ---
@user_router.callback_query(F.data.in_({"re:regen", "re:cont"}))
async def cb_retry(cb: types.CallbackQuery):
    uid = cb.from_user.id
    lang = await get_lang(uid)
    hist = await load_history(uid)
    if len(hist) < 2 or hist[-1]["role"] != "assistant" or hist[-2]["role"] != "user":
        await cb.answer(t(lang, "nothing_retry"), show_alert=True)
        return

    if cb.data == "re:regen":
        q = hist[-2]["content"]
        images = None
        m = PHOTO_RE.match(q)
        if m:
            images = last_images.get(uid)
            if not images:
                await cb.answer(t(lang, "photo_gone"), show_alert=True)
                return
            q = q[m.end():].strip()
        await cb.answer()
        await run_ai(cb.message.chat.id, uid, q, images=images, regen=True)
    else:
        await cb.answer()
        await run_ai(cb.message.chat.id, uid, t(lang, "continue_text"))


# --- Stop ---
@user_router.callback_query(F.data == "st:stop")
async def cb_stop(cb: types.CallbackQuery):
    uid = cb.from_user.id
    gen = active_gens.get(uid)
    if gen is None:
        await cb.answer(t(await get_lang(uid), "stop_none"))
        return
    gen.stopped = True
    if gen.task is not None and not gen.task.done():
        gen.task.cancel()
    await cb.answer()
