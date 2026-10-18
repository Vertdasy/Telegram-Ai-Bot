"""Plain text messages (must be registered last)."""

from aiogram import F, types

from aibot.services.ai import run_ai
from aibot.state import user_router


@user_router.message(F.text, ~F.text.startswith("/"))
async def handle_message(message: types.Message):
    await run_ai(message.chat.id, message.from_user.id, message.text)
