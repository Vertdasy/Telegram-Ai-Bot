"""Users table: registration, bans, language, mode and per-user preferences."""

import time
from typing import NamedTuple

from aiogram import types

from aibot.config import MENU_VERSION
from aibot.database.settings import scalar
from aibot.i18n import LANGS, MODES
from aibot.state import db


async def touch_user(user: types.User):
    now = int(time.time())
    await db.execute(
        "INSERT INTO users(id, username, first_name, joined, last_seen, menu_ver) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
        "first_name=excluded.first_name, last_seen=excluded.last_seen",
        (user.id, user.username, user.first_name, now, now, MENU_VERSION),
    )
    await db.commit()


async def is_banned(user_id: int) -> bool:
    return bool(await scalar("SELECT banned FROM users WHERE id=?", (user_id,)))


class Prefs(NamedTuple):
    lang: str        # interface language
    buttons: bool    # Regenerate / Continue buttons under replies
    think: bool      # "Thoughtful" style
    save: bool       # Save button under replies
    web: bool = True  # web search enabled


async def get_prefs(user_id: int) -> Prefs:
    cur = await db.execute(
        "SELECT lang, buttons, think, save_btn, web FROM users WHERE id=?", (user_id,)
    )
    row = await cur.fetchone()
    if not row:
        return Prefs("ru", False, False, False, True)
    lang = row["lang"] if row["lang"] in LANGS else "ru"
    return Prefs(
        lang, bool(row["buttons"]), bool(row["think"]), bool(row["save_btn"]),
        row["web"] is None or bool(row["web"]),
    )


async def get_lang(user_id: int) -> str:
    return (await get_prefs(user_id)).lang


async def set_user_field(user_id: int, field: str, value):
    if field not in ("mode", "lang", "buttons", "think", "custom_prompt", "save_btn", "web", "lang_set"):
        raise ValueError(field)
    await db.execute(f"UPDATE users SET {field}=? WHERE id=?", (value, user_id))
    await db.commit()


async def get_mode(user_id: int) -> str:
    mode = await scalar("SELECT mode FROM users WHERE id=?", (user_id,))
    return mode if mode in MODES else "default"


async def get_custom_prompt(user_id: int) -> str:
    return (await scalar("SELECT custom_prompt FROM users WHERE id=?", (user_id,))) or ""
