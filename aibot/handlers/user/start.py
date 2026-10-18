"""Start, language picker, help, invite and utility commands."""

import html
from urllib.parse import quote

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from aibot.config import ADMIN_IDS, MAX_IMAGES, MODEL
from aibot.database.history import get_memory_len
from aibot.database.settings import get_setting, scalar
from aibot.database.users import get_lang, set_user_field
from aibot.i18n import BTN_HELP, BTN_INVITE, LANGS, t
from aibot.keyboards.user import get_main_menu
from aibot.services.limits import get_bonus
from aibot.services.referral import bot_username, get_ref_cfg, register_start
from aibot.state import bot, user_router


@user_router.message(Command("myid"))
async def cmd_myid(message: types.Message):
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "myid", id=message.from_user.id))


@user_router.message(Command("admin"))
async def cmd_admin_denied(message: types.Message):
    lang = await get_lang(message.from_user.id)
    if ADMIN_IDS:
        await message.answer(t(lang, "denied"))
    else:
        await message.answer(t(lang, "admin_unset", id=message.from_user.id))


async def send_welcome(chat_id: int, user, lang: str):
    model = await get_setting("model", MODEL)
    await bot.send_message(
        chat_id,
        t(
            lang,
            "start",
            name=html.escape(user.first_name or (t(lang, "friend"))),
            model=html.escape(model),
            n=await get_memory_len(),
            k=MAX_IMAGES,
        ),
        parse_mode="HTML",
        reply_markup=get_main_menu(lang),
    )


@user_router.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject):
    uid = message.from_user.id
    await register_start(uid, command.args or "")
    if not await scalar("SELECT COALESCE(lang_set, 0) FROM users WHERE id=?", (uid,)):
        # on the very first visit, ask the user to choose a language
        b = InlineKeyboardBuilder()
        for code, name in LANGS.items():
            b.button(text=name, callback_data=f"lg:{code}")
        b.adjust(2)
        await message.answer(t("ru", "lang_pick"), parse_mode="HTML", reply_markup=b.as_markup())
        return
    await send_welcome(message.chat.id, message.from_user, await get_lang(uid))


@user_router.callback_query(F.data.startswith("lg:"))
async def cb_pick_lang(cb: types.CallbackQuery):
    code = cb.data.split(":", 1)[1]
    if code not in LANGS:
        await cb.answer()
        return
    uid = cb.from_user.id
    await set_user_field(uid, "lang", code)
    await set_user_field(uid, "lang_set", 1)
    try:
        await cb.message.edit_text(t(code, "lang_changed"))
    except TelegramBadRequest:
        pass
    await cb.answer()
    await send_welcome(cb.message.chat.id, cb.from_user, code)


@user_router.message(Command("invite"))
@user_router.message(F.text.in_(BTN_INVITE))
async def cmd_invite(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    uname = await bot_username()
    if not uname:
        await message.answer(t(lang, "invite_unavailable"))
        return
    link = f"https://t.me/{uname}?start=ref_{uid}"
    invited = await scalar("SELECT COUNT(*) FROM users WHERE ref_by=?", (uid,))
    paid = await scalar("SELECT COUNT(*) FROM users WHERE ref_by=? AND ref_paid=1", (uid,))
    b = InlineKeyboardBuilder()
    b.button(
        text=t(lang, "btn_share"),
        url=f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(t(lang, 'share_text'))}",
    )
    ref_bonus, ref_max = await get_ref_cfg()
    await message.answer(
        t(lang, "invite", b=ref_bonus, m=ref_max, link=link, n=invited, paid=paid, bonus=await get_bonus(uid)),
        parse_mode="HTML",
        reply_markup=b.as_markup(),
        disable_web_page_preview=True,
    )


@user_router.message(Command("help"))
@user_router.message(F.text.in_(BTN_HELP))
async def cmd_help(message: types.Message):
    lang = await get_lang(message.from_user.id)
    await message.answer(
        t(lang, "help", n=await get_memory_len(), k=MAX_IMAGES),
        parse_mode="HTML",
    )
