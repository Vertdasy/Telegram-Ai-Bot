"""Middlewares: user registration, bans, maintenance mode."""

import logging

from aiogram import BaseMiddleware

from aibot.config import ADMIN_IDS, MENU_VERSION
from aibot.database.settings import scalar
from aibot.database.users import get_lang, is_banned, touch_user
from aibot.i18n import t
from aibot.keyboards.user import get_main_menu
from aibot.state import db, STATE


async def refresh_menu(event, user):
    """Sends existing users the updated keyboard once (a reply keyboard cannot be updated in place)."""
    if hasattr(event, "data") or not hasattr(event, "chat"):
        return  # regular messages only, not inline-button presses
    try:
        ver = await scalar("SELECT COALESCE(menu_ver, 0) FROM users WHERE id=?", (user.id,))
        if ver >= MENU_VERSION:
            return
        await db.execute("UPDATE users SET menu_ver=? WHERE id=?", (MENU_VERSION, user.id))
        await db.commit()
        if (getattr(event, "text", None) or "").startswith("/start"):
            return  # /start already sends the new menu
        lang = await get_lang(user.id)
        await event.answer(t(lang, "menu_updated"), reply_markup=get_main_menu(lang))
    except Exception:
        logging.exception("Could not refresh the menu (non-critical)")


class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and not user.is_bot:
            await touch_user(user)
            if user.id not in ADMIN_IDS:
                if await is_banned(user.id):
                    await event.answer(t(await get_lang(user.id), "banned"))
                    return
                if STATE["maintenance"]:
                    await event.answer(t(await get_lang(user.id), "maintenance"))
                    return
            await refresh_menu(event, user)
        return await handler(event, data)
