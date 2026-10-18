"""Broadcast of a message to all users."""

import asyncio

from aiogram.exceptions import TelegramRetryAfter

from aibot.i18n import t
from aibot.state import bot, db


async def do_broadcast(text: str, admin_chat: int, lang: str = "ru"):
    cur = await db.execute("SELECT id FROM users WHERE banned=0")
    ids = [r[0] for r in await cur.fetchall()]
    ok = fail = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(uid, text)
                ok += 1
            except Exception:
                fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await bot.send_message(admin_chat, t(lang, "adm_bc_done", ok=ok, fail=fail))
