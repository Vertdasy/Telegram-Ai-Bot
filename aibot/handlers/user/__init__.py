"""Handlers for regular users.

Order matters: aiogram tries handlers in registration order, so the catch-all text handler
(``text``) must be imported last.
"""
from aibot.handlers.user import (  # noqa: F401
    start, modes, settings, memory, generation, photos, documents, text,
)
