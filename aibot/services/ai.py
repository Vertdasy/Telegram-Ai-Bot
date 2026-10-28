"""AI generation: streaming, tool calls, fallback model, and the per-user request queue."""

import asyncio
import logging
import time

import openai
from aiogram.exceptions import TelegramRetryAfter

from aibot.config import (
    EDIT_INTERVAL, EFFORT_FAST, EFFORT_THINK, FALLBACK_MODEL, MAX_QUEUE, MAX_TOKENS, MODEL, TOTAL_TIMEOUT,
    VISION_MODEL, WEB_MAX_ROUNDS,
)
from aibot.database.errors import log_error
from aibot.database.history import (
    get_history_chars, get_memory_len, load_history, pop_last_exchange, save_exchange, trim_by_chars,
)
from aibot.database.pinned import remember_answer
from aibot.database.settings import get_setting
from aibot.database.users import get_mode, get_prefs
from aibot.i18n import t
from aibot.keyboards.user import answer_kb, build_answer_kb, stop_kb
from aibot.services.limits import check_limit
from aibot.services.prompts import build_system_prompt
from aibot.services.referral import grant_ref_reward
from aibot.services.websearch import run_tool, sources_line, tools_bad, web_enabled, WEB_TOOL
from aibot.state import (
    active_gens, bot, client, db, last_images, last_kb_msg, reasoning_bad, user_locks, waiting,
)
from aibot.utils.telegram import edit_md, send_md, with_retry
from aibot.utils.text import photo_tag, split_markdown


class Gen:
    """State of a single generation: accumulated text and timings."""

    def __init__(self):
        self.text = ""
        self.started = time.time()
        self.first_any_at = None      # first chunk of data (including reasoning)
        self.first_token_at = None    # first chunk of the visible reply
        self.thinking = False         # the model sent reasoning
        self.dropped_effort = False   # the provider rejected reasoning_effort
        self.note = ""                # note for the placeholder (e.g. switching to the fallback model)
        self.searching = ""           # query the AI is currently searching the web for
        self.searches = 0             # number of searches made in this reply
        self.sources: list = []       # links from the search results
        self.stopped = False          # the user pressed Stop
        self.task = None              # asyncio generation task (cancelled by Stop)


async def generate(
    model: str, messages: list, effort, gen: Gen, remember: bool = True,
    tools: list | None = None, lang: str = "ru", user_id: int = 0,
):
    """Streams the model reply into gen.text. If the AI calls search, runs it and continues.
    Raises on failure (gen.text is preserved)."""
    eff = effort if effort and effort != "default" else None
    if eff and remember and model in reasoning_bad:
        eff = None  # this provider/model rejected the parameter earlier
    use_tools = tools if (tools and not (remember and model in tools_bad)) else None
    msgs = list(messages)
    dropped = False
    tools_dropped = False
    rounds = 0

    while True:
        kwargs = dict(model=model, messages=msgs, temperature=0.7, stream=True)
        if MAX_TOKENS > 0:
            kwargs["max_tokens"] = MAX_TOKENS
        if eff:
            kwargs["extra_body"] = {"reasoning_effort": eff}
        if use_tools and rounds < WEB_MAX_ROUNDS:
            kwargs["tools"] = use_tools
            kwargs["tool_choice"] = "auto"

        calls: dict[int, dict] = {}
        reasoning = ""   # model reasoning: DeepSeek requires it to be sent back along with the tool call
        stream = None
        request_started = time.monotonic()
        first_chunk_logged = False
        # Do not log the API key or message contents. These markers help distinguish
        # local preparation delays from connection/TTFT delays at the OpenAI-compatible API.
        logging.info(
            "AI request dispatching: model=%s effort=%s tools=%s base_url=%s",
            model, eff or "default", bool("tools" in kwargs), client.base_url,
        )
        try:
            stream = await client.chat.completions.create(**kwargs)
            logging.info(
                "AI stream opened: model=%s elapsed=%.3fs",
                model, time.monotonic() - request_started,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                now = time.time()
                if not first_chunk_logged:
                    first_chunk_logged = True
                    logging.info(
                        "AI first chunk: model=%s elapsed=%.3fs",
                        model, time.monotonic() - request_started,
                    )
                if gen.first_any_at is None:
                    gen.first_any_at = now
                for tc in getattr(delta, "tool_calls", None) or []:
                    slot = calls.setdefault(getattr(tc, "index", 0) or 0, {"id": "", "name": "", "args": ""})
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        if getattr(fn, "name", None):
                            slot["name"] = fn.name
                        if getattr(fn, "arguments", None):
                            slot["args"] += fn.arguments
                if delta.content:
                    if gen.first_token_at is None:
                        gen.first_token_at = now
                    gen.text += delta.content
                elif getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None):
                    gen.thinking = True
                    reasoning += getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None) or ""
        except (openai.BadRequestError, openai.UnprocessableEntityError):
            if eff and not gen.text:
                logging.warning("Provider rejected reasoning_effort=%s for %s, retrying without it", eff, model)
                eff = None
                dropped = True
                gen.dropped_effort = True
                continue
            if "tools" in kwargs and not gen.text and rounds == 0:
                logging.warning("Model %s does not accept tools (search), answering without them", model)
                use_tools = None
                tools_dropped = True
                continue
            raise
        except Exception as e:
            logging.warning(
                "AI API stream failed: model=%s error=%s elapsed=%.3fs",
                model, type(e).__name__, time.monotonic() - request_started,
            )
            raise
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass

        if calls and "tools" in kwargs:
            rounds += 1
            ordered = [calls[i] for i in sorted(calls)]
            for n, c in enumerate(ordered):
                c["id"] = c["id"] or f"call_{rounds}_{n}"
            msgs.append({
                "role": "assistant",
                "content": gen.text or None,
                **({"reasoning_content": reasoning} if reasoning else {}),
                "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"] or "web_search", "arguments": c["args"] or "{}"}}
                    for c in ordered
                ],
            })
            gen.text = ""   # don't show the user the text produced before the search call
            for c in ordered:
                msgs.append({
                    "role": "tool", "tool_call_id": c["id"],
                    "content": await run_tool(c, gen, lang, user_id),
                })
            gen.searching = ""
            continue
        break

    if dropped and remember:
        reasoning_bad.add(model)
    if tools_dropped and remember:
        tools_bad.add(model)


def friendly_error(lang: str, e: BaseException) -> str:
    if isinstance(e, (asyncio.TimeoutError, openai.APITimeoutError)):
        return t(lang, "err_timeout")
    if isinstance(e, openai.RateLimitError):
        return t(lang, "err_rate")
    if isinstance(e, openai.APIConnectionError):
        return t(lang, "err_conn")
    if isinstance(e, openai.APIStatusError):
        return t(lang, "err_status", code=e.status_code)
    return t(lang, "error", e=e)


def waiting_text(lang: str, gen: Gen) -> str:
    if gen.searching:
        return t(lang, "searching", q=gen.searching[:80])
    txt = t(lang, "thinking_time", s=int(time.time() - gen.started))
    return f"{gen.note}\n{txt}" if gen.note else txt


async def drop_stop_kb(chat_id: int, message_id: int, kb=None):
    """Safety net: after a reply, make sure the Stop button is removed (or the proper reply buttons are set).
    Needed in case the last edit from the ticker reaches Telegram after the final one."""
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=kb)
    except Exception:
        pass  # "message is not modified": the buttons are already as required


async def run_ai(
    chat_id: int,
    user_id: int,
    question: str,
    images: list | None = None,
    regen: bool = False,
    stored: str | None = None,
):
    """Sends a question (with or without photos) to the model. One user's requests are queued.
    stored: what to save in history instead of the question (e.g. question + file text)."""
    lang = (await get_prefs(user_id)).lang
    lock = user_locks.setdefault(user_id, asyncio.Lock())

    note = None
    if lock.locked():
        if waiting.get(user_id, 0) >= MAX_QUEUE:
            await bot.send_message(chat_id, t(lang, "busy"))
            return
        waiting[user_id] = waiting.get(user_id, 0) + 1
        try:
            try:
                note = await bot.send_message(chat_id, t(lang, "queued"))
            except Exception:
                note = None
            await lock.acquire()
        finally:
            waiting[user_id] -= 1
    else:
        await lock.acquire()

    try:
        if note is not None:
            try:
                await bot.delete_message(chat_id, note.message_id)
            except Exception:
                pass
        await _run_locked(chat_id, user_id, question, images, regen, stored)
    finally:
        lock.release()


async def _run_locked(
    chat_id: int, user_id: int, question: str, images: list | None, regen: bool, stored: str | None = None
):
    prefs = await get_prefs(user_id)
    lang = prefs.lang

    denied = await check_limit(user_id, lang)
    if denied:
        await bot.send_message(chat_id, denied)
        return

    # remove Regenerate/Continue from the previous reply (the Save button stays)
    old = last_kb_msg.pop(user_id, None)
    if old:
        try:
            kb = await build_answer_kb(user_id, old[1], lang, regen=False, save_on=prefs.save)
            await bot.edit_message_reply_markup(chat_id=old[0], message_id=old[1], reply_markup=kb)
        except Exception:
            pass

    # placeholder first (with the Stop button), everything else afterwards
    stop_markup = stop_kb(lang)
    sent = await bot.send_message(chat_id, t(lang, "thinking"), reply_markup=stop_markup)

    # count the attempt immediately so the limit can't be bypassed via errors
    await db.execute("INSERT INTO requests(ts, user_id) VALUES(?,?)", (int(time.time()), user_id))
    await db.commit()

    mode = await get_mode(user_id)
    # web search: only in the default and Programmer modes, not for photos, unless disabled
    web = bool(
        not images and prefs.web and mode in ("default", "programmer") and WEB_MAX_ROUNDS > 0
        and await web_enabled()
    )
    tools = WEB_TOOL if web else None
    prompt = await build_system_prompt(user_id, mode, lang, web=web)
    model = VISION_MODEL if images else await get_setting("model", MODEL)
    effort = await get_setting(
        "effort_think" if prefs.think else "effort_fast",
        EFFORT_THINK if prefs.think else EFFORT_FAST,
    )
    fallback = await get_setting("fallback_model", FALLBACK_MODEL)

    memory = await get_memory_len()
    history = await load_history(user_id, memory + (2 if regen else 0))
    if regen:
        history = history[:-2]
    history = trim_by_chars(history, await get_history_chars())

    if images:
        user_content = [{"type": "text", "text": question}] + [
            {"type": "image_url", "image_url": {"url": u}} for u in images
        ]
        stored_question = f"{photo_tag(len(images))} {question}"
    else:
        user_content = question
        stored_question = stored or question

    messages = [{"role": "system", "content": prompt}] + history + [
        {"role": "user", "content": user_content}
    ]

    gen = Gen()

    async def ticker():
        """Updates the Telegram message independently of stream reading, so it doesn't slow down reply intake."""
        last_shown = ""
        last_typing = 0.0
        last_wait_edit = 0.0
        try:
            while True:
                await asyncio.sleep(EDIT_INTERVAL)
                now = time.time()
                if now - last_typing > 4:
                    last_typing = now
                    try:
                        await bot.send_chat_action(chat_id, "typing")
                    except Exception:
                        pass
                if gen.text.strip():
                    shown = split_markdown(gen.text.strip())[0]
                    if shown != last_shown:
                        try:
                            await edit_md(chat_id, sent.message_id, shown, stop_markup)
                            last_shown = shown
                        except TelegramRetryAfter as e:
                            await asyncio.sleep(e.retry_after)
                        except Exception:
                            pass
                elif (gen.searching or now - gen.started >= 4) and now - last_wait_edit >= (1 if gen.searching else 3):
                    last_wait_edit = now
                    try:
                        await bot.edit_message_text(
                            waiting_text(lang, gen),
                            chat_id=chat_id,
                            message_id=sent.message_id,
                            reply_markup=stop_markup,
                        )
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            return

    # --- generation: primary model; fallback if it fails before producing any text ---
    attempts = [(model, effort)]
    if fallback and fallback != "off" and fallback != model and not images:
        attempts.append((fallback, None))

    holder = {"err": None}

    async def produce():
        for i, (m, eff) in enumerate(attempts):
            try:
                await asyncio.wait_for(
                    generate(m, messages, eff, gen, tools=tools if i == 0 else None, lang=lang, user_id=user_id),
                    TOTAL_TIMEOUT,
                )
                holder["err"] = None
                return
            except Exception as e:
                holder["err"] = e
                logging.warning("Model %s failed: %s: %s", m, type(e).__name__, e)
                await log_error(user_id, f"{m}: {type(e).__name__}: {e}")
                if gen.text or i == len(attempts) - 1:
                    return
                gen.note = t(lang, "fallback_note")
                gen.started = time.time()

    work = asyncio.create_task(produce())
    gen.task = work
    active_gens[user_id] = gen
    ticker_task = asyncio.create_task(ticker())
    try:
        await asyncio.wait({work})
    finally:
        active_gens.pop(user_id, None)
        if not work.done():
            work.cancel()
        ticker_task.cancel()
        try:
            await ticker_task
        except asyncio.CancelledError:
            pass

    err = holder["err"]
    if not work.cancelled() and work.exception() is not None:
        err = work.exception()
    if gen.stopped:
        err = None  # stopped by the user itself, not a failure

    final = gen.text.strip()

    if not final:
        if gen.stopped:
            msg = t(lang, "stopped")
        elif err is not None:
            msg = friendly_error(lang, err)
        else:
            await log_error(user_id, "Empty response from the model")
            msg = t(lang, "empty")
        try:
            await bot.edit_message_text(msg[:4096], chat_id=chat_id, message_id=sent.message_id)
        except Exception:
            await bot.send_message(chat_id, msg[:4096])
        await drop_stop_kb(chat_id, sent.message_id)
        return

    # the reply was cut off mid-way or stopped: show what was received, plus a note
    display = final
    if gen.searches and gen.sources and not gen.stopped:
        line = sources_line(lang, gen)
        if line:
            display += "\n\n" + line
    if gen.stopped:
        display += "\n\n" + t(lang, "stopped_note")
    elif err is not None:
        display += "\n\n" + t(lang, "cut_note")

    # --- delivery with formatting (and buttons, if enabled in settings) ---
    last_id = sent.message_id
    chunks = split_markdown(display)
    kb = answer_kb(lang, prefs.buttons, "save" if prefs.save else None)
    try:
        await with_retry(
            edit_md, chat_id, sent.message_id, chunks[0], kb if len(chunks) == 1 else None
        )
        for i, part in enumerate(chunks[1:], start=1):
            msg = await with_retry(send_md, chat_id, part, kb if i == len(chunks) - 1 else None)
            last_id = msg.message_id
        if prefs.buttons:
            last_kb_msg[user_id] = (chat_id, last_id)
    except Exception:
        logging.exception("Could not deliver the answer")
        await log_error(user_id, "Could not deliver the answer to Telegram")
    # the first reply message must lose its Stop button in any case
    await drop_stop_kb(chat_id, sent.message_id, kb if len(chunks) == 1 else None)

    # --- save to memory ---
    if prefs.save:
        await remember_answer(user_id, last_id, final)
    if regen:
        await pop_last_exchange(user_id)
    await save_exchange(user_id, stored_question, final)
    if images:
        last_images[user_id] = images
    if not gen.stopped and err is None and not regen:
        await grant_ref_reward(user_id)
