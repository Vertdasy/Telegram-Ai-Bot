"""Document handling (text, code, docx, pdf, xlsx, pptx) and images sent as files."""

import asyncio
import html
import logging
import time

from aiogram import F, types

from aibot.config import ALBUM_WAIT, HISTORY_DOC_CHARS, MAX_DOC_CHARS, MAX_DOCS, MAX_FILE_MB
from aibot.database.errors import log_error
from aibot.database.users import get_lang
from aibot.handlers.user.photos import process_photos
from aibot.i18n import t
from aibot.services.ai import run_ai
from aibot.state import bot, doc_albums, user_router
from aibot.utils.files import extract_text, FileProblem, IMAGE_MIMES
from aibot.utils.tasks import spawn


async def process_documents(chat_id: int, uid: int, docs: list, caption: str, over: int = 0):
    """Downloads files, extracts their text and sends it to the AI in a single request."""
    lang = await get_lang(uid)
    status = await bot.send_message(chat_id, t(lang, "doc_reading"))
    items: list[tuple[str, str]] = []   # (name, text)
    budget = MAX_DOC_CHARS
    shown_total = orig_total = 0
    for d in docs:
        name = getattr(d, "file_name", None) or "file"
        safe = html.escape(name)
        size = getattr(d, "file_size", None) or 0
        if size > MAX_FILE_MB * 1024 * 1024:
            await bot.send_message(chat_id, t(lang, "doc_big", name=safe, mb=MAX_FILE_MB), parse_mode="HTML")
            continue
        try:
            buf = await bot.download(d)
            data = buf.read()
        except Exception as e:
            logging.warning("Could not download file %s: %s", name, e)
            await bot.send_message(chat_id, t(lang, "doc_read_fail", name=safe), parse_mode="HTML")
            continue
        try:
            text = await asyncio.to_thread(extract_text, name, getattr(d, "mime_type", None), data)
        except FileProblem as e:
            await bot.send_message(chat_id, t(lang, e.key, name=safe, **e.kw), parse_mode="HTML")
            continue
        except Exception:
            logging.exception("Error reading file %s", name)
            await log_error(uid, f"File {name}: read error")
            await bot.send_message(chat_id, t(lang, "doc_bad", name=safe), parse_mode="HTML")
            continue
        orig_total += len(text)
        if budget <= 0:
            continue
        if len(text) > budget:
            text = text[:budget]
        budget -= len(text)
        shown_total += len(text)
        items.append((name, text))
    try:
        await bot.delete_message(chat_id, status.message_id)
    except Exception:
        pass
    if not items:
        return
    if over:
        await bot.send_message(chat_id, t(lang, "docs_limit", n=MAX_DOCS))
    if shown_total < orig_total:
        await bot.send_message(chat_id, t(lang, "doc_cut", n=shown_total, m=orig_total))
    question = caption or t(lang, "doc_caption")
    prompt = question + "\n\n" + "\n\n".join(f"--- {n} ---\n{x}\n--- end ---" for n, x in items)
    per = max(1000, HISTORY_DOC_CHARS // len(items))
    names = ", ".join(n for n, _ in items)[:80]
    stored = f"[File: {names}] {question}\n\n" + "\n\n".join(f"--- {n} ---\n{x[:per]}" for n, x in items)
    await run_ai(chat_id, uid, prompt, stored=stored)


async def flush_docs(gid: str):
    """Waits until all files of the batch arrive and processes them in one request."""
    buf = doc_albums.get(gid)
    if buf is None:
        return
    try:
        while True:
            pause = ALBUM_WAIT - (time.time() - buf["last"])
            if pause <= 0:
                break
            await asyncio.sleep(max(pause, 0.05))
    finally:
        doc_albums.pop(gid, None)
    try:
        await process_documents(buf["chat"], buf["uid"], buf["docs"], buf["caption"], buf["over"])
    except Exception:
        logging.exception("Error while processing files")


@user_router.message(F.document)
async def handle_document(message: types.Message):
    uid = message.from_user.id
    d = message.document
    name = d.file_name or "file"
    mime = (d.mime_type or "").lower()
    caption = (message.caption or "").strip()
    if mime in IMAGE_MIMES:   # image sent as a file (uncompressed)
        size = getattr(d, "file_size", None) or 0
        if size > MAX_FILE_MB * 1024 * 1024:
            lang = await get_lang(uid)
            await message.answer(
                t(lang, "doc_big", name=html.escape(name), mb=MAX_FILE_MB),
                parse_mode="HTML",
            )
            return
        await process_photos(message.chat.id, uid, [d], caption, mimes=[mime])
        return
    gid = message.media_group_id
    if not gid:
        await process_documents(message.chat.id, uid, [d], caption)
        return
    buf = doc_albums.get(gid)
    if buf is None:
        buf = doc_albums[gid] = {
            "docs": [], "caption": "", "last": time.time(), "chat": message.chat.id, "uid": uid, "over": 0,
        }
        spawn(flush_docs(gid))
    if len(buf["docs"]) < MAX_DOCS:
        buf["docs"].append(d)
    else:
        buf["over"] += 1
    if caption and not buf["caption"]:
        buf["caption"] = caption
    buf["last"] = time.time()
