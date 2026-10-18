"""Helpers for sending and editing Telegram messages (Markdown, retries)."""

import asyncio

from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from aibot.state import bot
from aibot.utils.text import md_to_html


async def edit_md(chat_id: int, message_id: int, md: str, kb=None):
    try:
        await bot.edit_message_text(
            md_to_html(md), chat_id=chat_id, message_id=message_id,
            parse_mode="HTML", reply_markup=kb,
        )
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        # HTML rejected; send as plain text
        await bot.edit_message_text(
            md[:4096], chat_id=chat_id, message_id=message_id, reply_markup=kb
        )


async def send_md(chat_id: int, md: str, kb=None) -> types.Message:
    try:
        return await bot.send_message(
            chat_id, md_to_html(md), parse_mode="HTML", reply_markup=kb
        )
    except TelegramBadRequest:
        return await bot.send_message(chat_id, md[:4096], reply_markup=kb)


async def with_retry(fn, *args, **kwargs):
    """Retries the call on Telegram's flood limit."""
    for attempt in range(3):
        try:
            return await fn(*args, **kwargs)
        except TelegramRetryAfter as e:
            if attempt == 2:
                raise
            await asyncio.sleep(e.retry_after + 0.5)
