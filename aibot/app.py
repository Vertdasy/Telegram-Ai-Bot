"""Application entry: wires middlewares and routers together and starts polling."""

import asyncio
import logging

from aiogram.types import BotCommand

from aibot.config import ADMIN_IDS
from aibot.database.connection import init_db
from aibot.i18n import COMMANDS
from aibot.middlewares import UserMiddleware
from aibot.state import admin_router, bot, db, dp, user_router


from aibot import handlers  # noqa: F401  (importing registers all handlers on the routers)


logging.basicConfig(level=logging.INFO)

dp.message.outer_middleware(UserMiddleware())
dp.callback_query.outer_middleware(UserMiddleware())

# The admin router goes first so that it intercepts its own commands and input
dp.include_router(admin_router)
dp.include_router(user_router)


async def setup_commands():
    """Command menu of Telegram: in Russian for Russian clients, otherwise in English."""
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS["en"]])
        for code, items in COMMANDS.items():
            await bot.set_my_commands(
                [BotCommand(command=c, description=d) for c, d in items], language_code=code
            )
    except Exception:
        logging.exception("Could not set the command menu (non-critical)")


async def main():
    await init_db()
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS is not set: the admin panel is unavailable. Find your ID with /myid.")
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_commands()
    logging.info("🚀 The bot is running!")
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()


def run():
    asyncio.run(main())
