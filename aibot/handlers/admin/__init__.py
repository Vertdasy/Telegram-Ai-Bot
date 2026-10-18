"""Admin handlers. The router only accepts updates from users listed in ADMIN_IDS."""
from aiogram import F

from aibot.config import ADMIN_IDS
from aibot.state import admin_router

admin_router.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

from aibot.handlers.admin import panel, input  # noqa: E402,F401  (registers the handlers)
