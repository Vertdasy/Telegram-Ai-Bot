"""Photo and album handling."""

import asyncio
import base64
import logging
import time

from aiogram import F, types

from aibot.config import ALBUM_WAIT, MAX_IMAGES
from aibot.database.users import get_lang
from aibot.i18n import t
from aibot.services.ai import run_ai
from aibot.state import albums, bot, user_router
from aibot.utils.tasks import spawn


# --- photos and albums ---
async def process_photos(
    chat_id: int, uid: int, photos: list, caption: str, over: int = 0, mimes: list | None = None
):
    """Downloads photos and sends them to the AI in a single request."""
    lang = await get_lang(uid)
    images: list[str] = []
    last_err = None
    for n, ph in enumerate(photos):
        try:
            buf = await bot.download(ph)
            mime = mimes[n] if mimes and n < len(mimes) else "image/jpeg"
            images.append(f"data:{mime};base64," + base64.b64encode(buf.read()).decode())
        except Exception as e:
            last_err = e
            logging.warning("Could not download the photo: %s", e)
    if not images:
        await bot.send_message(chat_id, t(lang, "photo_fail", e=last_err))
        return
    if over:
        await bot.send_message(chat_id, t(lang, "album_limit", n=MAX_IMAGES))
    caption = caption or t(lang, "photo_caption" if len(images) == 1 else "photo_caption_multi")
    await run_ai(chat_id, uid, caption, images=images)


async def flush_album(gid: str):
    """Waits until all album photos arrive and processes them in one request."""
    buf = albums.get(gid)
    if buf is None:
        return
    try:
        while True:
            pause = ALBUM_WAIT - (time.time() - buf["last"])
            if pause <= 0:
                break
            await asyncio.sleep(max(pause, 0.05))
    finally:
        albums.pop(gid, None)
    try:
        await process_photos(buf["chat"], buf["uid"], buf["photos"], buf["caption"], buf["over"])
    except Exception:
        logging.exception("Error while processing the album")


@user_router.message(F.photo)
async def handle_photo(message: types.Message):
    uid = message.from_user.id
    caption = (message.caption or "").strip()
    gid = message.media_group_id
    if not gid:
        await process_photos(message.chat.id, uid, [message.photo[-1]], caption)
        return

    # Telegram delivers each album photo as a separate message; collect them together
    buf = albums.get(gid)
    if buf is None:
        buf = albums[gid] = {
            "photos": [], "caption": "", "last": time.time(),
            "chat": message.chat.id, "uid": uid, "over": 0,
        }
        spawn(flush_album(gid))
    if len(buf["photos"]) < MAX_IMAGES:
        buf["photos"].append(message.photo[-1])
    else:
        buf["over"] += 1
    if caption and not buf["caption"]:
        buf["caption"] = caption
    buf["last"] = time.time()
