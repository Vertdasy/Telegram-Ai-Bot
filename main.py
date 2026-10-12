import os
import re
import asyncio
import base64
import logging
import tempfile
import time
import html
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import aiohttp
import aiosqlite
import openai
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware, types
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from openai import AsyncOpenAI
from duckduckgo_search import DDGS

logging.basicConfig(level=logging.INFO)


# ----------------- НАСТРОЙКИ (всё через переменные окружения) -----------------
def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")      # токен бота от @BotFather
AI_API_KEY = os.getenv("AI_API_KEY")              # ключ нейросети
if not TELEGRAM_TOKEN or not AI_API_KEY:
    raise SystemExit(
        "Не заданы переменные окружения TELEGRAM_TOKEN и/или AI_API_KEY. "
        "Добавь их в настройках приложения на Amvera."
    )

AI_BASE_URL = os.getenv("AI_BASE_URL", "https://ai.starimg.ru/v1")
MODEL = os.getenv("ID_MODEL", "deepseek-v4.1-flash")
VISION_MODEL = os.getenv("VISION_MODEL", "deepseek-v4.1-flash")
STT_MODEL = os.getenv("STT_MODEL", "")

EFFORT_FAST = os.getenv("EFFORT_FAST", "low")
EFFORT_THINK = os.getenv("EFFORT_THINK", "high")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "deepseek-v4-flash")
AI_TIMEOUT = env_float("AI_TIMEOUT", 150.0)
TOTAL_TIMEOUT = env_float("TOTAL_TIMEOUT", 300.0)
MAX_TOKENS = env_int("MAX_TOKENS", 0)

MEMORY_DEFAULT = env_int("MEMORY_LEN", 30)
MEMORY_MIN, MEMORY_MAX = 2, 200
HISTORY_CHARS = env_int("HISTORY_CHARS", 40000)

PINNED_MAX = env_int("PINNED_MAX", 10)
PINNED_ITEM_CHARS = 4000
PINNED_TOTAL_CHARS = 8000
ANSWERS_KEEP = 40

MAX_IMAGES = env_int("MAX_IMAGES", 6)
ALBUM_WAIT = env_float("ALBUM_WAIT", 1.2)
MAX_QUEUE = env_int("MAX_QUEUE", 2)

ADSGRAM_BLOCK_ID = re.sub(r"\D", "", os.getenv("ADSGRAM_BLOCK_ID", ""))
ADSGRAM_TOKEN = os.getenv("ADSGRAM_TOKEN", "")
ADS_EVERY = env_int("ADS_EVERY", 6)
ADS_MIN_GAP = env_int("ADS_MIN_GAP", 600)
ADS_API = "https://api.adsgram.ai/advbot"

REF_BONUS = env_int("REF_BONUS", 20)
REF_MAX = env_int("REF_MAX", 25)
SRC_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

ADMIN_IDS = {
    int(x)
    for x in os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "")).replace(" ", "").split(",")
    if x.isdigit()
}

AVAILABLE_MODELS = [
    m.strip()
    for m in os.getenv("MODELS", "deepseek-v4.1-flash,deepseek-v4-flash,deepseek-v4-pro").split(",")
    if m.strip()
]

RATE_PER_MIN = env_int("RATE_PER_MIN", 5)
RATE_PER_DAY = env_int("RATE_PER_DAY", 100)

DB_PATH = os.getenv("DB_PATH") or ("/data/bot.db" if os.path.isdir("/data") else "bot.db")
TZ_HOURS = env_int("TZ_HOURS", 4)

EDIT_INTERVAL = env_float("EDIT_INTERVAL", 1.0)
CHUNK = 3500
CUSTOM_PROMPT_MAX = 1000

PHOTO_RE = re.compile(r"^\[(?:Photo\vert{}Фото)(?: x(\d+))?\]\s*")


def photo_tag(n: int) -> str:
    return "[Photo]" if n <= 1 else f"[Photo x{n}]"


EFFORTS = ("default", "low", "medium", "high", "xhigh")
EFFORT_LABEL = {
    "default": "по умолчанию",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
}
TEST_MESSAGES = [
    {"role": "system", "content": "Отвечай максимально кратко."},
    {"role": "user", "content": "Ответь одним словом: ок"},
]

LANGS = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}

TEXTS = {
    "ru": {
        "btn_ask": "🤖 Задать вопрос ИИ",
        "btn_mode": "🎭 Режим",
        "btn_clear": "📝 Очистить диалог",
        "btn_info": "ℹ Инфо",
        "btn_settings": "⚙️ Настройки",
        "btn_invite": "🎁 Пригласить",
        "btn_help": "❓ Помощь",
        "menu_updated": "✨ В меню появились кнопки «🎁 Пригласить» и «❓ Помощь».",
        "banned": "🚫 Доступ к боту ограничен.",
        "maintenance": "🔧 Бот на техническом обслуживании. Загляни чуть позже.",
        "start": (
            "👋 <b>Привет, {name}!</b>\n\n"
            "Я — ИИ-ассистент на базе <b>{model}</b>. Отвечаю на вопросы, пишу и правлю тексты, "
            "помогаю с кодом, перевожу и разбираю изображения."
        ),
        "help": "❓ <b>Справка</b>\n\nПиши вопрос обычным текстом или присылай фото.",
        "invite": "🎁 <b>Пригласи друзей — получи больше запросов</b>\n\n🔗 Твоя ссылка:\n<code>{link}</code>",
        "invite_unavailable": "Не получилось сформировать ссылку, попробуй чуть позже.",
        "btn_share": "📤 Поделиться ботом",
        "share_text": "ИИ-помощник в Telegram: отвечает на вопросы, пишет код, переводит и разбирает фото 🤖",
        "ref_welcome": "🎉 Добро пожаловать! Тебе добавлено <b>+{b}</b> запросов в сутки.",
        "ref_notify": "🎁 Твой друг начал пользоваться ботом! Тебе добавлено <b>+{b}</b> запросов в сутки.",
        "ask": "💬 Задавай любой вопрос или пришли фото — можно сразу несколько. Я готов!",
        "mode_choose": "🎭 <b>Выбери режим</b>\n\n{list}",
        "mode_set": "✅ <b>Режим: {mode}</b>\n{desc}\n\n🧹 Прошлый диалог сброшен.{pinned}",
        "mode_pinned": "\n📌 Сохранённые сообщения ({n}) остались в памяти.",
        "mode_same": "Этот режим уже включён",
        "info": "ℹ <b>О боте</b>\n\n🧠 <b>Модель:</b> {model}\n🎭 <b>Режим:</b> {mode}",
        "limits_fmt": "{pm}/мин, {pd}/сутки",
        "limits_admin": "без ограничений (админ)",
        "unlimited": "без лимита",
        "clear_confirm": "🧹 <b>Очистить диалог?</b>",
        "clear_confirm_pinned": "🧹 <b>Очистить диалог?</b> Сохраненные останутся.",
        "btn_clear_yes": "🧹 Да, очистить",
        "btn_clear_all": "🧹 Очистить всё",
        "btn_clear_keep": "📌 Оставить сохранённые",
        "btn_cancel": "✖ Отмена",
        "cleared": "🧹 История диалога очищена!",
        "cleared_keep": "🧹 Диалог очищен. Сохранённые сообщения остались.",
        "clear_cancelled": "Отменено.",
        "clear_empty": "Память уже пуста.",
        "busy": "⏳ Подожди, я ещё отвечаю на предыдущие запросы.",
        "queued": "⏳ Предыдущий запрос ещё обрабатывается.",
        "btn_stop": "⏹ Стоп",
        "stopped": "⏹ Остановлено.",
        "stopped_note": "⏹ Ответ остановлен.",
        "stop_none": "Сейчас нечего останавливать",
        "limit_min": "⏳ Слишком часто: не больше {n} запросов в минуту. Подожди {wait}.",
        "limit_day": "📅 Дневной лимит исчерпан. Доступно через {wait}.",
        "wait_sec": "{n} сек.",
        "wait_min": "{n} мин.",
        "wait_hm": "{h} ч {m} мин.",
        "thinking": "🤔 Запрос обрабатывается...",
        "thinking_time": "🧠 Думаю… {s} с",
        "fallback_note": "⚠️ Основная модель не ответила, пробую резервную…",
        "cut_note": "⚠️ Ответ оборвался. Напиши «продолжи».",
        "error": "⚠️ Произошла ошибка: {e}",
        "err_timeout": "⏳ Модель слишком долго отвечала.",
        "err_rate": "🚦 Сервис ИИ перегружен. Попробуй через минуту.",
        "err_conn": "📡 Не удалось связаться с сервисом.",
        "err_status": "⚠️ Ошибка сервиса ({code}).",
        "empty": "Извините, не удалось сформировать ответ.",
        "btn_regen": "🔄 Повторить",
        "btn_cont": "➡️ Продолжить",
        "nothing_retry": "Нечего повторять",
        "btn_save": "📌 Сохранить",
        "btn_saved": "✅ Сохранено",
        "save_ok": "📌 Сохранено в память.",
        "save_dup": "Это сообщение уже сохранено",
        "save_limit": "Лимит сохраненных сообщений исчерпан.",
        "save_gone": "Сообщение недоступно.",
        "saved_title": "📌 <b>Сохраненные сообщения</b>\n\n{items}",
        "saved_empty": "📌 Сохраненных сообщений пока нет.",
        "btn_del_all": "🗑 Удалить все",
        "saved_deleted": "Удалено",
        "pinned_hdr": "Важный контекст:",
        "photo_gone": "Фото больше недоступно.",
        "photo_fail": "⚠️ Не удалось загрузить фото: {e}",
        "photo_caption": "Опиши, что на изображении.",
        "photo_caption_multi": "Опиши, что на этих изображениях.",
        "album_limit": "📎 Максимум фото за раз.",
        "voice_off": "🎤 Голосовые не поддерживаются.",
        "voice_fail": "⚠️ Ошибка распознавания: {e}",
        "voice_empty": "Не удалось разобрать речь.",
        "voice_ok": "🎤 Распознано: {text}",
        "settings": "⚙️ <b>Настройки</b>",
        "btn_buttons": "🔁 Повторить/Продолжить: {state}",
        "btn_savebtn": "📌 Кнопка «Сохранить»: {state}",
        "btn_think": "🧠 Стиль ответа: {state}",
        "think_fast": "⚡ Быстро",
        "think_deep": "🧠 Вдумчиво",
        "on": "вкл ✅",
        "off": "выкл ❌",
        "lang_changed": "✅ Язык изменен.",
        "myid": "Твой Telegram ID: {id}",
        "denied": "⛔ Нет доступа.",
        "admin_unset": "Админ не настроен.",
        "continue_text": "Продолжи свой предыдущий ответ с того места, где он оборвался.",
        "user_instr": "Инструкции пользователя:",
        "myprompt_help": "📝 <b>Инструкции для ИИ</b>\n\nСейчас: {current}",
        "myprompt_saved": "✅ Инструкции сохранены.",
        "myprompt_reset": "♻️ Инструкции сброшены.",
        "none": "не задано",
        "export_empty": "Диалог пуст.",
        "export_caption": "📄 Диалог: {n} сообщений.",
        "export_you": "Ты",
        "export_ai": "ИИ",
        "export_saved": "Сохранённые",
    },
    "en": {
        "btn_ask": "🤖 Ask the AI",
        "btn_mode": "🎭 Mode",
        "btn_clear": "📝 Clear chat",
        "btn_info": "ℹ Info",
        "btn_settings": "⚙️ Settings",
        "btn_invite": "🎁 Invite",
        "btn_help": "❓ Help",
        "menu_updated": "✨ Menu updated.",
        "banned": "🚫 Access denied.",
        "maintenance": "🔧 Maintenance mode.",
        "start": "👋 <b>Hi, {name}!</b>",
        "help": "❓ <b>Help</b>",
        "invite": "🎁 <b>Invite</b>",
        "invite_unavailable": "Unavailable.",
        "btn_share": "📤 Share",
        "share_text": "AI Assistant",
        "ref_welcome": "Welcome!",
        "ref_notify": "Friend joined!",
        "ask": "💬 Ask me anything!",
        "mode_choose": "🎭 <b>Choose mode</b>",
        "mode_set": "✅ <b>Mode: {mode}</b>",
        "mode_pinned": "",
        "mode_same": "Already enabled",
        "info": "ℹ <b>About</b>",
        "limits_fmt": "{pm}/min",
        "limits_admin": "unlimited",
        "unlimited": "unlimited",
        "clear_confirm": "Clear?",
        "clear_confirm_pinned": "Clear?",
        "btn_clear_yes": "Yes",
        "btn_clear_all": "All",
        "btn_clear_keep": "Keep",
        "btn_cancel": "Cancel",
        "cleared": "Cleared.",
        "cleared_keep": "Cleared.",
        "clear_cancelled": "Cancelled.",
        "clear_empty": "Empty.",
        "busy": "Busy.",
        "queued": "Queued.",
        "btn_stop": "Stop",
        "stopped": "Stopped.",
        "stopped_note": "Stopped.",
        "stop_none": "None",
        "limit_min": "Limit reached.",
        "limit_day": "Limit reached.",
        "wait_sec": "{n}s",
        "wait_min": "{n}m",
        "wait_hm": "{h}h {m}m",
        "thinking": "Thinking...",
        "thinking_time": "Thinking… {s}s",
        "fallback_note": "Fallback...",
        "cut_note": "Cut off.",
        "error": "Error: {e}",
        "err_timeout": "Timeout.",
        "err_rate": "Rate limit.",
        "err_conn": "Connection error.",
        "err_status": "Status error.",
        "empty": "Empty response.",
        "btn_regen": "Regenerate",
        "btn_cont": "Continue",
        "nothing_retry": "Nothing to repeat",
        "btn_save": "Save",
        "btn_saved": "Saved",
        "save_ok": "Saved.",
        "save_dup": "Duplicate",
        "save_limit": "Limit.",
        "save_gone": "Gone.",
        "saved_title": "Saved",
        "saved_empty": "Empty",
        "btn_del_all": "Delete all",
        "saved_deleted": "Deleted",
        "pinned_hdr": "Context:",
        "photo_gone": "Gone.",
        "photo_fail": "Fail.",
        "photo_caption": "Describe.",
        "photo_caption_multi": "Describe.",
        "album_limit": "Limit.",
        "voice_off": "Off.",
        "voice_fail": "Fail.",
        "voice_empty": "Empty.",
        "voice_ok": "OK: {text}",
        "settings": "Settings",
        "btn_buttons": "Buttons: {state}",
        "btn_savebtn": "Save: {state}",
        "btn_think": "Style: {state}",
        "think_fast": "Fast",
        "think_deep": "Deep",
        "on": "on ✅",
        "off": "off ❌",
        "lang_changed": "Language changed.",
        "myid": "ID: {id}",
        "denied": "Denied.",
        "admin_unset": "Unset.",
        "continue_text": "Continue.",
        "user_instr": "Instructions:",
        "myprompt_help": "Prompt: {current}",
        "myprompt_saved": "Saved.",
        "myprompt_reset": "Reset.",
        "none": "none",
        "export_empty": "Empty.",
        "export_caption": "Export.",
        "export_you": "You",
        "export_ai": "AI",
        "export_saved": "Saved",
    },
}

DEFAULT_PROMPT = {
    "ru": "Ты полезный и вежливый AI-ассистент.",
    "en": "You are a helpful AI assistant.",
}

MODES = {
    "default": {
        "title": {"ru": "🤖 Обычный", "en": "🤖 Default"},
        "desc": {"ru": "универсальный помощник", "en": "assistant"},
        "prompt": None,
    },
    "translator": {
        "title": {"ru": "🌍 Переводчик", "en": "🌍 Translator"},
        "desc": {"ru": "переводчик", "en": "translator"},
        "prompt": {"ru": "Ты переводчик.", "en": "You are a translator."},
    },
    "programmer": {
        "title": {"ru": "💻 Программист", "en": "💻 Programmer"},
        "desc": {"ru": "программист", "en": "programmer"},
        "prompt": {"ru": "Ты программист.", "en": "You are a programmer."},
    },
    "editor": {
        "title": {"ru": "✍️ Редактор", "en": "✍️ Editor"},
        "desc": {"ru": "редактор", "en": "editor"},
        "prompt": {"ru": "Ты редактор.", "en": "You are an editor."},
    },
}

COMMANDS = {
    "ru": [("start", "Запустить"), ("help", "Справка"), ("mode", "Режим"), ("settings", "Настройки"), ("saved", "Сохраненные"), ("export", "Экспорт"), ("invite", "Пригласить"), ("clear", "Очистить"), ("myprompt", "Инструкции")],
    "en": [("start", "Start"), ("help", "Help"), ("mode", "Mode"), ("settings", "Settings"), ("saved", "Saved"), ("export", "Export"), ("invite", "Invite"), ("clear", "Clear"), ("myprompt", "Prompt")],
}


def t(lang: str, key: str, **kw) -> str:
    s = TEXTS.get(lang, TEXTS["ru"]).get(key) or TEXTS["ru"][key]
    return s.format(**kw) if kw else s


def all_langs(key: str) -> set:
    return {TEXTS[code][key] for code in TEXTS}


def modes_list(lang: str, current: str) -> str:
    lines = []
    for key, m in MODES.items():
        mark = "👉" if key == current else "•"
        lines.append(f"{mark} <b>{m['title'][lang]}</b> — {m['desc'][lang]}")
    return "\n".join(lines)


BTN_ASK = all_langs("btn_ask")
BTN_MODE = all_langs("btn_mode")
BTN_CLEAR = all_langs("btn_clear")
BTN_INFO = all_langs("btn_info")
BTN_SETTINGS = all_langs("btn_settings")
BTN_INVITE = all_langs("btn_invite")
BTN_HELP = all_langs("btn_help")
MENU_VERSION = 3


def make_timeout():
    timeout_cls = getattr(openai, "Timeout", None)
    if timeout_cls is not None:
        try:
            return timeout_cls(connect=15.0, read=AI_TIMEOUT, write=30.0, pool=30.0)
        except Exception:
            pass
    return AI_TIMEOUT


bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL, timeout=make_timeout(), max_retries=1)

admin_router = Router()
user_router = Router()

user_locks: dict[int, asyncio.Lock] = {}
waiting: dict[int, int] = {}
active_gens: dict = {}
last_kb_msg: dict[int, tuple[int, int]] = {}
last_images: dict[int, list] = {}
albums: dict[str, dict] = {}
bg_tasks: set = set()
reasoning_bad: set[str] = set()
STATE = {"maintenance": False, "speedtest": False}
db: aiosqlite.Connection


def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)
    return task


def md_to_html(text: str) -> str:
    if text.count("```") % 2:
        text += "\n```"
    stash: list[str] = []

    def keep(s: str) -> str:
        stash.append(s)
        return f"\x00{len(stash) - 1}\x00"

    def code_block(m):
        lang = m.group(1)
        code = m.group(2).strip("\n")
        cls = f' class="language-{html.escape(lang)}"' if lang else ""
        return keep(f"<pre><code{cls}>{html.escape(code, quote=False)}</code></pre>")

    text = re.sub(r"```([\w+#.-]*)[ \t]*\n?(.*?)```", code_block, text, flags=re.S)
    text = re.sub(r"`([^`\n]+)`", lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = html.escape(text, quote=False)
    text = re.sub(r"^[ ]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", r"<b>\1</b>", text, flags=re.M)
    text = re.sub(r"^([ \t]*)[-*+][ \t]+", r"\1• ", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*(?![\s*])([^*\n]+?)(?<![\s*])\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w_])_(?![\s_])([^_\n]+?)(?<![\s_])_(?![\w_])", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)\"]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return text.strip()


def split_markdown(text: str, limit: int = CHUNK) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunk, text = text[:cut], text[cut:].lstrip("\n")
        if chunk.count("```") % 2:
            chunk += "\n```"
            text = "```\n" + text
        chunks.append(chunk)
    chunks.append(text)
    return chunks


async def edit_md(chat_id: int, message_id: int, md: str, kb=None):
    try:
        await bot.edit_message_text(md_to_html(md), chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        await bot.edit_message_text(md[:4096], chat_id=chat_id, message_id=message_id, reply_markup=kb)


async def send_md(chat_id: int, md: str, kb=None) -> types.Message:
    try:
        return await bot.send_message(chat_id, md_to_html(md), parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        return await bot.send_message(chat_id, md[:4096], reply_markup=kb)


async def with_retry(fn, *args, **kwargs):
    for attempt in range(3):
        try:
            return await fn(*args, **kwargs)
        except TelegramRetryAfter as e:
            if attempt == 2:
                raise
            await asyncio.sleep(e.retry_after + 0.5)


def fmt_ts(ts, fmt: str = "%d.%m %H:%M") -> str:
    if not ts:
        return "—"
    tz = timezone(timedelta(hours=TZ_HOURS))
    return datetime.fromtimestamp(ts, tz=tz).strftime(fmt)


async def init_db():
    global db
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined INTEGER,
            last_seen INTEGER,
            msgs INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            mode TEXT DEFAULT 'default',
            lang TEXT DEFAULT 'ru',
            buttons INTEGER DEFAULT 0,
            think INTEGER DEFAULT 0,
            custom_prompt TEXT,
            save_btn INTEGER DEFAULT 0,
            ref_by INTEGER,
            ref_paid INTEGER DEFAULT 0,
            bonus INTEGER DEFAULT 0,
            src TEXT,
            menu_ver INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT);
        CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id);
        CREATE TABLE IF NOT EXISTS pinned(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, content TEXT, ts INTEGER);
        CREATE INDEX IF NOT EXISTS idx_pinned_user ON pinned(user_id);
        CREATE TABLE IF NOT EXISTS answers(user_id INTEGER, message_id INTEGER, content TEXT, ts INTEGER, PRIMARY KEY (user_id, message_id));
        CREATE TABLE IF NOT EXISTS ads_log(ts INTEGER, user_id INTEGER, ok INTEGER);
        CREATE INDEX IF NOT EXISTS idx_ads_ts ON ads_log(ts);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS errors(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, user_id INTEGER, text TEXT);
        CREATE TABLE IF NOT EXISTS requests(ts INTEGER, user_id INTEGER);
        CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
        CREATE INDEX IF NOT EXISTS idx_requests_user ON requests(user_id, ts);
        """
    )
    cur = await db.execute("PRAGMA table_info(users)")
    cols = {r["name"] for r in await cur.fetchall()}
    for name, ddl in (
        ("mode", "TEXT DEFAULT 'default'"),
        ("lang", "TEXT DEFAULT 'ru'"),
        ("buttons", "INTEGER DEFAULT 0"),
        ("think", "INTEGER DEFAULT 0"),
        ("custom_prompt", "TEXT"),
        ("save_btn", "INTEGER DEFAULT 0"),
        ("ref_by", "INTEGER"),
        ("ref_paid", "INTEGER DEFAULT 0"),
        ("bonus", "INTEGER DEFAULT 0"),
        ("src", "TEXT"),
        ("menu_ver", "INTEGER DEFAULT 0"),
    ):
        if name not in cols:
            await db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
            if name == "src":
                await db.execute("UPDATE users SET src='old'")
    await db.commit()
    STATE["maintenance"] = (await get_setting("maintenance", "0")) == "1"
    logging.info(f"База данных: {DB_PATH}")
# ================= АДМИН-ПАНЕЛЬ (на русском) =================

admin_router.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


class Admin(StatesGroup):
    waiting = State()
    confirm = State()


def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика", callback_data="adm:stats")
    b.button(text="👥 Пользователи", callback_data="adm:users")
    b.button(text="📢 Рассылка", callback_data="adm:broadcast")
    b.button(text="⚠️ Ошибки", callback_data="adm:errors")
    b.button(text="🚫 Бан", callback_data="adm:ban")
    b.button(text="✅ Разбан", callback_data="adm:unban")
    b.button(text="🧠 Модель", callback_data="adm:model")
    b.button(text="📝 Промпт", callback_data="adm:prompt")
    b.button(text="🧮 Память", callback_data="adm:mem")
    b.button(text="⏱ Лимиты", callback_data="adm:limits")
    b.button(text="💰 Реклама", callback_data="adm:ads")
    b.button(text="🔗 Источники", callback_data="adm:src")
    b.button(text="🎁 Приглашения", callback_data="adm:ref")
    b.button(text="⚡ Скорость", callback_data="adm:speed_menu")
    b.button(text="👤 Пользователь", callback_data="adm:userinfo")
    b.button(text="💾 Бэкап БД", callback_data="adm:backup")
    b.button(
        text=f"🔧 Техработы: {'вкл' if STATE['maintenance'] else 'выкл'}",
        callback_data="adm:maint",
    )
    b.adjust(2)
    return b.as_markup()


def model_kb(current: str):
    b = InlineKeyboardBuilder()
    for m in AVAILABLE_MODELS:
        b.button(text=("✅ " if m == current else "") + m, callback_data=f"adm:m:{m}")
    b.button(text="✏️ Другая модель", callback_data="adm:model_custom")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(1)
    return b.as_markup()


def prompt_kb():
    b = InlineKeyboardBuilder()
    b.button(text="✏️ RU", callback_data="adm:prompt_edit:ru")
    b.button(text="✏️ EN", callback_data="adm:prompt_edit:en")
    b.button(text="♻️ Сбросить оба", callback_data="adm:prompt_reset")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(2, 1, 1)
    return b.as_markup()


def limits_kb():
    b = InlineKeyboardBuilder()
    b.button(text="✏️ В минуту", callback_data="adm:limit_min")
    b.button(text="✏️ В сутки", callback_data="adm:limit_day")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(2, 1)
    return b.as_markup()


def confirm_kb():
    b = InlineKeyboardBuilder()
    b.button(text="✅ Отправить", callback_data="adm:bc_yes")
    b.button(text="❌ Отмена", callback_data="adm:bc_no")
    b.adjust(2)
    return b.as_markup()


MEMORY_PRESETS = (10, 20, 30, 50, 100)


def memory_kb(current: int):
    b = InlineKeyboardBuilder()
    for n in MEMORY_PRESETS:
        b.button(text=("✅ " if n == current else "") + str(n), callback_data=f"adm:memset:{n}")
    b.button(text="✏️ Своё число", callback_data="adm:mem_custom")
    b.button(text="📏 Лимит объёма", callback_data="adm:hist_chars")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(5, 2, 1)
    return b.as_markup()


async def memory_text() -> str:
    n = await get_memory_len()
    chars = await get_history_chars()
    total = await scalar("SELECT COUNT(*) FROM history")
    users = await scalar("SELECT COUNT(DISTINCT user_id) FROM history")
    pins = await scalar("SELECT COUNT(*) FROM pinned")
    return (
        "🧮 <b>Память диалога</b>\n\n"
        f"Сейчас: <b>{n}</b> сообщений (≈{n // 2} пар «вопрос — ответ»).\n"
        "Бот хранит и сообщения пользователя, и ответы ИИ — всё это уходит модели с каждым запросом. "
        "Чем больше память, тем больше токенов тратится и тем дольше может идти ответ.\n\n"
        f"📏 Лимит объёма: <b>{chars}</b> символов истории в одном запросе. Если диалог длиннее, "
        "самые старые сообщения не отправляются модели (но остаются в памяти до вытеснения).\n\n"
        f"В базе сейчас: {total} сообщений у {users} пользователей, 📌 сохранённых: {pins}.\n"
        f"Допустимо от {MEMORY_MIN} до {MEMORY_MAX}. При уменьшении старые сообщения у всех сразу удаляются."
    )


def ref_kb():
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Запросов в день", callback_data="adm:ref_bonus")
    b.button(text="✏️ Макс. друзей", callback_data="adm:ref_max")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(2, 1)
    return b.as_markup()


async def ref_text() -> str:
    bonus, mx = await get_ref_cfg()
    refs = await scalar("SELECT COUNT(*) FROM users WHERE ref_by IS NOT NULL")
    paid = await scalar("SELECT COUNT(*) FROM users WHERE ref_paid=1")
    top = await db.execute(
        "SELECT ref_by, COUNT(*) AS n FROM users WHERE ref_paid=1 GROUP BY ref_by ORDER BY n DESC LIMIT 5"
    )
    top_rows = await top.fetchall()
    top_text = "\n".join(f"{i}. <code>{r['ref_by']}</code> — {r['n']}" for i, r in enumerate(top_rows, 1)) or "—"
    state = f"+{bonus} запросов в сутки" if bonus > 0 else "выключены (0)"
    return (
        "🎁 <b>Приглашения друзей</b>\n\n"
        f"Бонус за приглашение: <b>{state}</b> — получает и друг, и пригласивший, навсегда.\n"
        f"Максимум друзей с бонусом у одного человека: <b>{mx}</b> (приглашённый получает бонус в любом случае).\n\n"
        "Бонус прибавляется к суточному лимиту (⏱ Лимиты) и начисляется после первого ответа друга. "
        "Новые значения действуют только на будущие приглашения — уже выданные бонусы не меняются.\n\n"
        f"Перешли по ссылкам: {refs}, начали пользоваться: {paid}\n\n"
        f"🏆 Лучшие по приглашениям:\n{top_text}"
    )


def src_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить", callback_data="adm:src")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(2)
    return b.as_markup()


async def sources_text() -> str:
    week = int(time.time()) - 7 * 86400
    cur = await db.execute(
        "SELECT COALESCE(src, 'direct') AS s, COUNT(*) AS n, "
        "SUM(CASE WHEN msgs > 0 THEN 1 ELSE 0 END) AS act, "
        "SUM(CASE WHEN joined > ? THEN 1 ELSE 0 END) AS d7 "
        "FROM users GROUP BY s ORDER BY n DESC LIMIT 15",
        (week,),
    )
    rows = await cur.fetchall()
    names = {"old": "до учёта источников", "direct": "напрямую (без метки)", "ref": "по приглашению друзей"}
    lines = "\n".join(
        f"• <b>{html.escape(names.get(r['s'], r['s']))}</b> — всего {r['n']}, писали боту {r['act'] or 0}, "
        f"новых за 7 дн: {r['d7'] or 0}"
        for r in rows
    ) or "—"
    refs = await scalar("SELECT COUNT(*) FROM users WHERE ref_by IS NOT NULL")
    refs_paid = await scalar("SELECT COUNT(*) FROM users WHERE ref_paid=1")
    uname = await bot_username()
    return (
        "🔗 <b>Откуда приходят пользователи</b>\n\n"
        f"{lines}\n\n"
        f"🎁 Приглашений по ссылкам друзей: {refs}, начали пользоваться: {refs_paid}\n\n"
        "<b>Как отслеживать рекламу.</b> Для каждого места, где рекламируешь бота, дай свою ссылку с меткой:\n"
        f"<code>https://t.me/{uname or 'твой_бот'}?start=метка</code>\n"
        "Метка — латиница, цифры, _ и -, до 32 символов (например <code>vc_post</code>, "
        "<code>tgads1</code>). Считаются только новые пользователи, которые нажали «Старт»."
    )


ADS_PRESETS = (3, 5, 6, 10, 20)


def ads_kb(on: bool, every: int):
    b = InlineKeyboardBuilder()
    b.button(text="⏸ Выключить" if on else "▶️ Включить", callback_data="adm:ads_toggle")
    for n in ADS_PRESETS:
        b.button(text=("✅ " if n == every else "") + f"каждый {n}-й", callback_data=f"adm:ads_every:{n}")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(1, 3, 2, 1)
    return b.as_markup()


async def ads_text() -> str:
    day = int(time.time()) - 86400
    shown = await scalar("SELECT COUNT(*) FROM ads_log WHERE ok=1")
    shown_day = await scalar("SELECT COUNT(*) FROM ads_log WHERE ok=1 AND ts>?", (day,))
    empty_day = await scalar("SELECT COUNT(*) FROM ads_log WHERE ok=0 AND ts>?", (day,))
    people = await scalar("SELECT COUNT(DISTINCT user_id) FROM ads_log WHERE ok=1 AND ts>?", (day,))
    if not ADSGRAM_BLOCK_ID:
        state = "⚠️ не настроена: задай переменную ADSGRAM_BLOCK_ID на Amvera"
    elif await ads_enabled():
        state = "✅ включена"
    else:
        state = "⏸ выключена"
    return (
        "💰 <b>Реклама AdsGram</b>\n\n"
        f"Статус: {state}\n"
        f"Блок: <code>{ADSGRAM_BLOCK_ID or '—'}</code>"
        f"{' · токен задан' if ADSGRAM_TOKEN else ''}\n"
        f"Частота: после каждого <b>{await get_ads_every()}-го</b> ответа, не чаще раза в "
        f"{ADS_MIN_GAP // 60} мин на человека. Админам реклама не показывается.\n\n"
        f"Показов за 24ч: <b>{shown_day}</b> ({people} чел.), всего: {shown}\n"
        f"За 24ч у AdsGram не нашлось рекламы: {empty_day} раз\n\n"
        + (f"Последняя причина «нет рекламы»: <code>{html.escape(ads_last['why'])}</code> "
           f"({fmt_ts(ads_last['ts'])})\n\n" if ads_last["why"] else "")
        + "Доход смотри в кабинете partner.adsgram.ai."
    )


def speed_kb():
    b = InlineKeyboardBuilder()
    b.button(text="⚡ Уровень «Быстро»", callback_data="adm:eff_pick:fast")
    b.button(text="🧠 Уровень «Вдумчиво»", callback_data="adm:eff_pick:think")
    b.button(text="🛟 Резервная модель", callback_data="adm:fb_pick")
    b.button(text="🧪 Тест скорости", callback_data="adm:speedtest")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(2, 1, 1, 1)
    return b.as_markup()


def effort_kb(which: str, current: str):
    b = InlineKeyboardBuilder()
    for e in EFFORTS:
        label = EFFORT_LABEL[e] + (" (не передавать)" if e == "default" else "")
        b.button(text=("✅ " if e == current else "") + label, callback_data=f"adm:eff:{which}:{e}")
    b.button(text="⬅️ Назад", callback_data="adm:speed_menu")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def fallback_kb(current: str):
    b = InlineKeyboardBuilder()
    for m in AVAILABLE_MODELS:
        b.button(text=("✅ " if m == current else "") + m, callback_data=f"adm:fb:{m}")
    b.button(text=("✅ " if current == "off" else "") + "Выключить резерв", callback_data="adm:fb:off")
    b.button(text="⬅️ Назад", callback_data="adm:speed_menu")
    b.adjust(1)
    return b.as_markup()


def fmt_user(r) -> str:
    name = html.escape(r["first_name"] or "—")
    uname = f" @{html.escape(r['username'])}" if r["username"] else ""
    return f"{name}{uname}"


async def speed_text() -> str:
    model = await get_setting("model", MODEL)
    fast = await get_setting("effort_fast", EFFORT_FAST)
    deep = await get_setting("effort_think", EFFORT_THINK)
    fb = await get_setting("fallback_model", FALLBACK_MODEL)
    bad = ", ".join(sorted(reasoning_bad)) or "—"
    return (
        "⚡ <b>Скорость и надёжность</b>\n\n"
        f"🧠 Модель: <code>{html.escape(model)}</code>\n"
        f"⚡ «Быстро» (по умолчанию у всех): <b>{EFFORT_LABEL.get(fast, fast)}</b>\n"
        f"🧠 «Вдумчиво» (включается в настройках пользователя): <b>{EFFORT_LABEL.get(deep, deep)}</b>\n"
        f"🛟 Резервная модель: <code>{html.escape(fb) if fb not in ('', 'off') else 'выключена'}</code>\n"
        f"⏱ Таймаут без данных: {AI_TIMEOUT:.0f} с, общий: {TOTAL_TIMEOUT:.0f} с\n"
        f"🚫 Модели, не принявшие параметр рассуждения: {html.escape(bad)}\n\n"
        "Уровень рассуждения — чем ниже, тем быстрее ответ. «Тест скорости» замерит время до первого "
        "слова при разных уровнях и покажет, принимает ли провайдер параметр."
    )


async def run_speedtest(msg: types.Message):
    """Замеряет время до первого слова для разных уровней рассуждения и резервной модели."""
    if STATE["speedtest"]:
        return
    STATE["speedtest"] = True
    try:
        model = await get_setting("model", MODEL)
        fb = await get_setting("fallback_model", FALLBACK_MODEL)
        cases = [(model, e) for e in ("default", "low", "medium", "high")]
        if fb not in ("", "off") and fb != model:
            cases.append((fb, "default"))

        head = "🧪 <b>Тест скорости</b> (запрос: «ответь одним словом»)\n\n"
        lines: list[str] = []

        async def render(tail: str, kb=None):
            try:
                await msg.edit_text(head + "\n".join(lines) + tail, parse_mode="HTML", reply_markup=kb)
            except TelegramBadRequest:
                pass

        await render("\n⏳ тестирую…")
        for m, eff in cases:
            g = Gen()
            label = f"<code>{html.escape(m)}</code> · {EFFORT_LABEL[eff]}"
            try:
                await asyncio.wait_for(generate(m, TEST_MESSAGES, eff, g, remember=False), 60)
                total = time.time() - g.started
                if g.first_token_at is None:
                    lines.append(f"⚠️ {label}\n    пустой ответ, {total:.1f} с")
                else:
                    ttft = g.first_token_at - g.started
                    extra = ""
                    if g.thinking:
                        extra += " · есть рассуждение"
                    if g.dropped_effort:
                        extra += " · параметр не принят провайдером"
                    lines.append(
                        f"✅ {label}\n    до первого слова: <b>{ttft:.1f} с</b>, всего {total:.1f} с{extra}"
                    )
            except Exception as e:
                lines.append(
                    f"❌ {label}\n    {html.escape(type(e).__name__)}: {html.escape(str(e)[:120])}"
                )
            await render("\n⏳ тестирую…")
        await render("\n\nГотово. Выбери лучший вариант в разделе «Скорость».", speed_kb())
    finally:
        STATE["speedtest"] = False


async def user_card(uid: int):
    cur = await db.execute("SELECT * FROM users WHERE id=?", (uid,))
    r = await cur.fetchone()
    if not r:
        return None, None
    req24 = await scalar(
        "SELECT COUNT(*) FROM requests WHERE user_id=? AND ts>?", (uid, int(time.time()) - 86400)
    )
    pins = await count_pinned(uid)
    used = await count_history(uid)
    custom = r["custom_prompt"]
    text = (
        f"👤 <b>{fmt_user(r)}</b>\n"
        f"ID: <code>{uid}</code>\n"
        f"Статус: {'🚫 забанен' if r['banned'] else '✅ активен'}\n"
        f"Регистрация: {fmt_ts(r['joined'], '%d.%m.%Y %H:%M')}\n"
        f"Последняя активность: {fmt_ts(r['last_seen'], '%d.%m.%Y %H:%M')}\n"
        f"Ответов получено: {r['msgs']} (за 24ч запросов: {req24})\n"
        f"Режим: {html.escape(str(r['mode']))} · язык: {html.escape(str(r['lang']))} · "
        f"стиль: {'вдумчиво' if r['think'] else 'быстро'}\n"
        f"Кнопки «Повторить/Продолжить»: {'вкл' if r['buttons'] else 'выкл'} · "
        f"«Сохранить»: {'вкл' if r['save_btn'] else 'выкл'}\n"
        f"В памяти: {used} сообщений · 📌 сохранено: {pins}\n"
        f"Свои инструкции: {html.escape(custom[:200]) if custom else '—'}"
    )
    b = InlineKeyboardBuilder()
    if r["banned"]:
        b.button(text="✅ Разбанить", callback_data=f"adm:unban_id:{uid}")
    elif uid not in ADMIN_IDS:
        b.button(text="🚫 Забанить", callback_data=f"adm:ban_id:{uid}")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(2)
    return text, b.as_markup()


async def show(cb: types.CallbackQuery, text: str, kb=None):
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb or admin_kb())
    except TelegramBadRequest:
        pass
    await cb.answer()


@admin_router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=admin_kb())


@admin_router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=admin_kb())


@admin_router.callback_query(F.data.startswith("adm:"))
async def admin_callbacks(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split(":", 2)
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if action == "menu":
        await state.clear()
        await show(cb, "🛠 <b>Админ-панель</b>")

    elif action == "stats":
        now = int(time.time())
        day = now - 86400
        total = await scalar("SELECT COUNT(*) FROM users")
        banned = await scalar("SELECT COUNT(*) FROM users WHERE banned=1")
        active = await scalar("SELECT COUNT(*) FROM users WHERE last_seen>?", (day,))
        new = await scalar("SELECT COUNT(*) FROM users WHERE joined>?", (day,))
        req_day = await scalar("SELECT COUNT(*) FROM requests WHERE ts>?", (day,))
        req_all = await scalar("SELECT COUNT(*) FROM requests")
        err_day = await scalar("SELECT COUNT(*) FROM errors WHERE ts>?", (day,))
        en_users = await scalar("SELECT COUNT(*) FROM users WHERE lang='en'")
        model = await get_setting("model", MODEL)
        memory = await get_memory_len()
        cur = await db.execute(
            "SELECT id, first_name, username, msgs FROM users ORDER BY msgs DESC LIMIT 5"
        )
        top = await cur.fetchall()
        top_text = "\n".join(
            f"{i}. {fmt_user(r)} — {r['msgs']}" for i, r in enumerate(top, 1) if r["msgs"]
        ) or "—"
        await show(
            cb,
            "📊 <b>Статистика</b>\n\n"
            f"👥 Пользователей: <b>{total}</b> (забанено: {banned}, на English: {en_users})\n"
            f"🟢 Активны за 24ч: <b>{active}</b>\n"
            f"🆕 Новых за 24ч: <b>{new}</b>\n"
            f"💬 Запросов за 24ч: <b>{req_day}</b>\n"
            f"💬 Запросов всего: <b>{req_all}</b>\n"
            f"⚠️ Ошибок за 24ч: <b>{err_day}</b>\n"
            f"🧠 Модель: <code>{html.escape(model)}</code>\n"
            f"🧮 Память диалога: <b>{memory}</b> сообщений\n\n"
            f"🏆 <b>Топ по запросам:</b>\n{top_text}",
        )

    elif action == "users":
        cur = await db.execute(
            "SELECT id, first_name, username, msgs, banned FROM users ORDER BY last_seen DESC LIMIT 15"
        )
        rows = await cur.fetchall()
        lines = [
            f"{'🚫' if r['banned'] else '👤'} <code>{r['id']}</code> {fmt_user(r)} — {r['msgs']}"
            for r in rows
        ]
        await show(cb, "👥 <b>Последние пользователи:</b>\n\n" + ("\n".join(lines) or "—"))

    elif action == "errors":
        cur = await db.execute("SELECT ts, user_id, text FROM errors ORDER BY id DESC LIMIT 8")
        rows = await cur.fetchall()
        lines = [
            f"<code>{fmt_ts(r['ts'])}</code> ({r['user_id']}): {html.escape(r['text'][:200])}"
            for r in rows
        ]
        await show(cb, "⚠️ <b>Последние ошибки:</b>\n\n" + ("\n\n".join(lines) or "Ошибок нет 🎉"))

    elif action == "broadcast":
        await state.set_state(Admin.waiting)
        await state.update_data(action="broadcast")
        await show(cb, "📢 Пришли текст рассылки одним сообщением.\n/cancel — отмена")

    elif action in ("ban", "unban"):
        await state.set_state(Admin.waiting)
        await state.update_data(action=action)
        verb = "забанить" if action == "ban" else "разбанить"
        await show(cb, f"Пришли ID пользователя, которого нужно {verb}.\n/cancel — отмена")

    elif action in ("ban_id", "unban_id"):
        if not arg.isdigit():
            await cb.answer()
            return
        uid = int(arg)
        if action == "ban_id" and uid in ADMIN_IDS:
            await cb.answer("Админа банить нельзя", show_alert=True)
            return
        await db.execute(
            "UPDATE users SET banned=? WHERE id=?", (1 if action == "ban_id" else 0, uid)
        )
        await db.commit()
        text, kb = await user_card(uid)
        await show(cb, text or "Пользователь не найден.", kb)

    elif action == "userinfo":
        await state.set_state(Admin.waiting)
        await state.update_data(action="userinfo")
        await show(cb, "Пришли ID пользователя, чтобы посмотреть его карточку.\n/cancel — от        мена")

    elif action == "model":
        current = await get_setting("model", MODEL)
        await show(
            cb,
            f"🧠 <b>Текущая модель:</b> <code>{html.escape(current)}</code>\nВыбери новую:",
            model_kb(current),
        )

    elif action == "m":
        await set_setting("model", arg)
        await show(cb, f"✅ Модель изменена: <code>{html.escape(arg)}</code>", model_kb(arg))

    elif action == "model_custom":
        await state.set_state(Admin.waiting)
        await state.update_data(action="model")
        await show(cb, "Пришли ID модели (как в личном кабинете провайдера).\n/cancel — отмена")

    elif action == "prompt":
        ru = await get_setting("system_prompt", DEFAULT_PROMPT["ru"])
        en = await get_setting("system_prompt_en", DEFAULT_PROMPT["en"])
        await show(
            cb,
            "📝 <b>Системный промпт</b> (режим «Обычный»)\n\n"
            f"🇷🇺 <b>RU</b> (для русского интерфейса):\n<code>{html.escape(ru[:1500])}</code>\n\n"
            f"🇬🇧 <b>EN</b> (для английского интерфейса):\n<code>{html.escape(en[:1500])}</code>",
            prompt_kb(),
        )

    elif action == "prompt_edit":
        await state.set_state(Admin.waiting)
        await state.update_data(action="prompt", plang=arg if arg in ("ru", "en") else "ru")
        await show(cb, f"Пришли новый системный промпт ({arg.upper()}).\n/cancel — отмена")

    elif action == "prompt_reset":
        await del_setting("system_prompt")
        await del_setting("system_prompt_en")
        await show(cb, "♻️ Промпты сброшены на стандартные.", prompt_kb())

    elif action == "mem":
        await state.clear()
        await show(cb, await memory_text(), memory_kb(await get_memory_len()))

    elif action == "memset":
        if arg.isdigit() and MEMORY_MIN <= int(arg) <= MEMORY_MAX:
            await apply_memory_len(int(arg))
        n = await get_memory_len()
        await show(cb, await memory_text(), memory_kb(n))

    elif action == "mem_custom":
        await state.set_state(Admin.waiting)
        await state.update_data(action="memory")
        await show(
            cb,
            f"Пришли, сколько сообщений помнить (от {MEMORY_MIN} до {MEMORY_MAX}). "
            "Вопрос и ответ считаются двумя сообщениями.\n/cancel — отмена",
        )

    elif action == "hist_chars":
        await state.set_state(Admin.waiting)
        await state.update_data(action="hist_chars")
        await show(
            cb,
            "Пришли лимит объёма истории в символах (от 1000). Чем меньше, тем быстрее ответы, "
            "но тем меньше сообщений реально видит модель. Сейчас: "
            f"<b>{await get_history_chars()}</b>.\n/cancel — отмена",
        )

    elif action == "ref":
        await state.clear()
        await show(cb, await ref_text(), ref_kb())

    elif action in ("ref_bonus", "ref_max"):
        await state.set_state(Admin.waiting)
        await state.update_data(action=action)
        bonus, mx = await get_ref_cfg()
        if action == "ref_bonus":
            await show(
                cb,
                f"Пришли, сколько запросов в сутки давать за приглашение (0 — бонус выключить). "
                f"Сейчас: <b>{bonus}</b>.\n/cancel — отмена",
            )
        else:
            await show(
                cb,
                f"Пришли максимум друзей, за которых пригласивший получает бонус (0 — только друг получает бонус). "
                f"Сейчас: <b>{mx}</b>.\n/cancel — отмена",
            )

    elif action == "src":
        await show(cb, await sources_text(), src_kb())

    elif action == "ads":
        await show(cb, await ads_text(), ads_kb(await ads_enabled(), await get_ads_every()))

    elif action == "ads_toggle":
        await set_setting("ads_on", "0" if (await get_setting("ads_on", "1")) == "1" else "1")
        await show(cb, await ads_text(), ads_kb(await ads_enabled(), await get_ads_every()))

    elif action == "ads_every":
        if arg.isdigit() and 1 <= int(arg) <= 100:
            await set_setting("ads_every", arg)
        await show(cb, await ads_text(), ads_kb(await ads_enabled(), await get_ads_every()))

    elif action == "limits":
        per_min, per_day = await get_limits()
        await show(
            cb,
            "⏱ <b>Лимиты запросов на человека</b>\n\n"
            f"В минуту: <b>{fmt_limit(per_min)}</b>\n"
            f"В сутки: <b>{fmt_limit(per_day)}</b>\n\n"
            "Админы лимитами не ограничиваются. 0 = без лимита.",
            limits_kb(),
        )

    elif action in ("limit_min", "limit_day"):
        await state.set_state(Admin.waiting)
        await state.update_data(action=action)
        what = "в минуту" if action == "limit_min" else "в сутки"
        await show(cb, f"Пришли число запросов {what} (0 — без лимита).\n/cancel — отмена")

    elif action == "speed_menu":
        await show(cb, await speed_text(), speed_kb())

    elif action == "eff_pick":
        if arg not in ("fast", "think"):
            await cb.answer()
            return
        current = await get_setting(
            f"effort_{arg}", EFFORT_FAST if arg == "fast" else EFFORT_THINK
        )
        title = "⚡ «Быстро»" if arg == "fast" else "🧠 «Вдумчиво»"
        await show(
            cb,
            f"{title}: выбери уровень рассуждения модели.\n"
            "Ниже — быстрее ответ, но меньше «раздумий» над сложными задачами.\n"
            "«По умолчанию» — параметр не передаётся, работает настройка провайдера.",
            effort_kb(arg, current),
        )

    elif action == "eff":
        which, _, val = arg.partition(":")
        if which in ("fast", "think") and val in EFFORTS:
            await set_setting(f"effort_{which}", val)
        await show(cb, await speed_text(), speed_kb())

    elif action == "fb_pick":
        current = await get_setting("fallback_model", FALLBACK_MODEL)
        await show(
            cb,
            "🛟 <b>Резервная модель</b>\nВключается, если основная не ответила (таймаут или ошибка "
            "до первого слова).",
            fallback_kb(current),
        )

    elif action == "fb":
        await set_setting("fallback_model", arg)
        await show(cb, await speed_text(), speed_kb())

    elif action == "speedtest":
        if STATE["speedtest"]:
            await cb.answer("Тест уже идёт", show_alert=True)
            return
        await cb.answer("Запускаю тест…")
        await run_speedtest(cb.message)

    elif action == "backup":
        await cb.answer("Готовлю копию базы…")
        path = os.path.join(tempfile.gettempdir(), f"bot-backup-{int(time.time())}.db")
        try:
            await db.commit()
            await db.execute("VACUUM INTO ?", (path,))

            def _read() -> bytes:
                with open(path, "rb") as f:
                    return f.read()

            data = await asyncio.to_thread(_read)
            name = f"bot-backup-{fmt_ts(int(time.time()), '%Y%m%d-%H%M')}.db"
            await bot.send_document(
                cb.message.chat.id,
                BufferedInputFile(data, filename=name),
                caption="💾 Копия базы данных (пользователи, история, настройки).",
            )
        except Exception as e:
            logging.exception("Бэкап не удался")
            await bot.send_message(cb.message.chat.id, f"⚠️ Не удалось сделать копию: {e}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    elif action == "maint":
        STATE["maintenance"] = not STATE["maintenance"]
        await set_setting("maintenance", "1" if STATE["maintenance"] else "0")
        note = (
            "🔧 Техработы <b>включены</b>: обычные пользователи видят сообщение об обслуживании."
            if STATE["maintenance"]
            else "✅ Техработы выключены."
        )
        await show(cb, "🛠 <b>Админ-панель</b>\n\n" + note)

    elif action == "bc_yes":
        data = await state.get_data()
        text = data.get("bc_text")
        await state.clear()
        if not text:
            await show(cb, "Нечего отправлять.")
            return
        spawn(do_broadcast(text, cb.message.chat.id))
        await show(cb, "📢 Рассылка запущена, по окончании пришлю отчёт.")

    elif action == "bc_no":
        await state.clear()
        await show(cb, "Рассылка отменена.")

    else:
        await cb.answer()


@admin_router.message(Admin.waiting, F.text)
async def admin_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    action = data.get("action")
    text = message.text.strip()

    if action in ("ban", "unban"):
        if not text.lstrip("-").isdigit():
            await message.answer("ID должен быть числом. Попробуй ещё раз или /cancel.")
            return
        uid = int(text)
        if action == "ban" and uid in ADMIN_IDS:
            await message.answer("Админа банить нельзя.")
            return
        cur = await db.execute(
            "UPDATE users SET banned=? WHERE id=?", (1 if action == "ban" else 0, uid)
        )
        await db.commit()
        await state.clear()
        if cur.rowcount:
            done = "забанен 🚫" if action == "ban" else "разбанен ✅"
            await message.answer(f"Пользователь {uid} {done}", reply_markup=admin_kb())
        else:
            await message.answer("Такого пользователя нет в базе.", reply_markup=admin_kb())

    elif action == "userinfo":
        if not text.lstrip("-").isdigit():
            await message.answer("ID должен быть числом. Попробуй ещё раз или /cancel.")
            return
        card, kb = await user_card(int(text))
        await state.clear()
        if card:
            await message.answer(card, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer("Такого пользователя нет в базе.", reply_markup=admin_kb())

    elif action == "model":
        await set_setting("model", text)
        await state.clear()
        await message.answer(
            f"✅ Модель изменена: <code>{html.escape(text)}</code>",
            parse_mode="HTML",
            reply_markup=admin_kb(),
        )

    elif action == "prompt":
        plang = data.get("plang", "ru")
        await set_setting("system_prompt" if plang == "ru" else "system_prompt_en", text)
        await state.clear()
        await message.answer(
            f"✅ Системный промпт ({plang.upper()}) обновлён.", reply_markup=admin_kb()
        )

    elif action == "memory":
        if not text.isdigit() or not (MEMORY_MIN <= int(text) <= MEMORY_MAX):
            await message.answer(
                f"Нужно целое число от {MEMORY_MIN} до {MEMORY_MAX}. Попробуй ещё раз или /cancel."
            )
            return
        await apply_memory_len(int(text))
        await state.clear()
        await message.answer(
            await memory_text(), parse_mode="HTML", reply_markup=memory_kb(await get_memory_len())
        )

    elif action == "hist_chars":
        if not text.isdigit() or int(text) < 1000:
            await message.answer("Нужно целое число от 1000. Попробуй ещё раз или /cancel.")
            return
        await set_setting("history_chars", str(int(text)))
        await state.clear()
        await message.answer(
            await memory_text(), parse_mode="HTML", reply_markup=memory_kb(await get_memory_len())
        )

    elif action in ("ref_bonus", "ref_max"):
        top = 10000 if action == "ref_bonus" else 1000
        if not text.isdigit() or int(text) > top:
            await message.answer(f"Нужно целое число от 0 до {top}. Попробуй ещё раз или /cancel.")
            return
        await set_setting(action, str(int(text)))
        await state.clear()
        await message.answer(await ref_text(), parse_mode="HTML", reply_markup=ref_kb())

    elif action in ("limit_min", "limit_day"):
        if not text.isdigit():
            await message.answer("Нужно целое число (0 — без лимита). Попробуй ещё раз или /cancel.")
            return
        await set_setting("rate_min" if action == "limit_min" else "rate_day", str(int(text)))
        await state.clear()
        await message.answer("✅ Лимит обновлён.", reply_markup=admin_kb())

    elif action == "broadcast":
        total = await scalar("SELECT COUNT(*) FROM users WHERE banned=0")
        await state.set_state(Admin.confirm)
        await state.update_data(bc_text=text)
        await message.answer(
            f"Разослать это сообщение {total} пользователям?\n\n— — —\n{text[:3000]}",
            reply_markup=confirm_kb(),
        )


async def do_broadcast(text: str, admin_chat: int):
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
    await bot.send_message(
        admin_chat, f"📢 Рассылка завершена: доставлено {ok}, не доставлено {fail}."
    )


# ================= ПОЛЬЗОВАТЕЛЬСКАЯ ЧАСТЬ =================

@user_router.message(Command("myid"))
async def cmd_myid(message: types.Message):
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "myid", id=message.from_user.id))


@user_router.message(Command("admin"))
async def cmd_admin_denied(message: types.Message):
    lang = await get_lang(message.from_user.id)
    if ADMIN_IDS:
        await message.answer(t(lang, "denied"))
    else:
        await message.answer(t(lang, "admin_unset", id=message.from_user.id))


@user_router.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject):
    await register_start(message.from_user.id, command.args or "")
    lang = await get_lang(message.from_user.id)
    model = await get_setting("model", MODEL)
    await message.answer(
        t(
            lang,
            "start",
            name=html.escape(message.from_user.first_name or ("друг" if lang == "ru" else "friend")),
            model=html.escape(model),
            n=await get_memory_len(),
            k=MAX_IMAGES,
        ),
        parse_mode="HTML",
        reply_markup=get_main_menu(lang),
    )


@user_router.message(Command("invite"))
@user_router.message(F.text.in_(BTN_INVITE))
async def cmd_invite(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    uname = await bot_username()
    if not uname:
        await message.answer(t(lang, "invite_unavailable"))
        return
    link = f"https://t.me/{uname}?start=ref_{uid}"
    invited = await scalar("SELECT COUNT(*) FROM users WHERE ref_by=?", (uid,))
    paid = await scalar("SELECT COUNT(*) FROM users WHERE ref_by=? AND ref_paid=1", (uid,))
    b = InlineKeyboardBuilder()
    b.button(
        text=t(lang, "btn_share"),
        url=f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(t(lang, 'share_text'))}",
    )
    ref_bonus, ref_max = await get_ref_cfg()
    await message.answer(
        t(lang, "invite", b=ref_bonus, m=ref_max, link=link, n=invited, paid=paid, bonus=await get_bonus(uid)),
        parse_mode="HTML",
        reply_markup=b.as_markup(),
        disable_web_page_preview=True,
    )


@user_router.message(Command("help"))
@user_router.message(F.text.in_(BTN_HELP))
async def cmd_help(message: types.Message):
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "help", n=await get_memory_len(), k=MAX_IMAGES), parse_mode="HTML")


@user_router.message(Command("clear", "reset"))
@user_router.message(F.text.in_(BTN_CLEAR))
async def cmd_clear(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    n = await count_history(uid)
    p = await count_pinned(uid)
    if n == 0 and p == 0:
        await message.answer(t(lang, "clear_empty"))
        return
    key = "clear_confirm_pinned" if p else "clear_confirm"
    await message.answer(
        t(lang, key, n=n, p=p), parse_mode="HTML", reply_markup=clear_kb(lang, p > 0)
    )


@user_router.callback_query(F.data.startswith("cl:"))
async def cb_clear(cb: types.CallbackQuery):
    uid = cb.from_user.id
    lang = await get_lang(uid)
    action = cb.data.split(":", 1)[1]
    if action == "no":
        text = t(lang, "clear_cancelled")
    elif action == "keep":
        p = await count_pinned(uid)
        await clear_history(uid, pinned=False)
        text = t(lang, "cleared_keep", p=p) if p else t(lang, "cleared")
    elif action == "all":
        await clear_history(uid)
        text = t(lang, "cleared")
    else:
        await cb.answer()
        return
    last_kb_msg.pop(uid, None)
    try:
        await cb.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await cb.answer()


@user_router.message(Command("mode"))
@user_router.message(F.text.in_(BTN_MODE))
async def cmd_mode(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    current = await get_mode(uid)
    await message.answer(
        t(lang, "mode_choose", list=modes_list(lang, current)),
        parse_mode="HTML",
        reply_markup=modes_kb(current, lang),
    )


@user_router.callback_query(F.data.startswith("mode:"))
async def cb_mode(cb: types.CallbackQuery):
    key = cb.data.split(":", 1)[1]
    if key not in MODES:
        await cb.answer()
        return
    uid = cb.from_user.id
    lang = await get_lang(uid)
    if key == await get_mode(uid):
        await cb.answer(t(lang, "mode_same"))
        return
    await set_user_field(uid, "mode", key)
    await clear_history(uid, pinned=False, answers=False)
    p = await count_pinned(uid)
    try:
        await cb.message.edit_text(
            t(
                lang,
                "mode_set",
                mode=MODES[key]["title"][lang],
                desc=MODES[key]["desc"][lang],
                pinned=t(lang, "mode_pinned", n=p) if p else "",
            ),
            parse_mode="HTML",
            reply_markup=modes_kb(key, lang),
        )
    except TelegramBadRequest:
        pass
    await cb.answer()


@user_router.message(Command("settings"))
@user_router.message(F.text.in_(BTN_SETTINGS))
async def cmd_settings(message: types.Message):
    prefs = await get_prefs(message.from_user.id)
    await message.answer(
        t(prefs.lang, "settings"), parse_mode="HTML", reply_markup=settings_kb(prefs.lang, prefs)
    )


@user_router.callback_query(F.data.startswith("set:"))
async def cb_settings(cb: types.CallbackQuery):
    uid = cb.from_user.id
    parts = cb.data.split(":")
    prefs = await get_prefs(uid)

    if parts[1] == "lang" and len(parts) > 2 and parts[2] in LANGS:
        if parts[2] != prefs.lang:
            await set_user_field(uid, "lang", parts[2])
            await bot.send_message(
                cb.message.chat.id, t(parts[2], "lang_changed"), reply_markup=get_main_menu(parts[2])
            )
    elif parts[1] == "btns":
        await set_user_field(uid, "buttons", 0 if prefs.buttons else 1)
    elif parts[1] == "save":
        await set_user_field(uid, "save_btn", 0 if prefs.save else 1)
    elif parts[1] == "think":
        await set_user_field(uid, "think", 0 if prefs.think else 1)

    prefs = await get_prefs(uid)
    try:
        await cb.message.edit_text(
            t(prefs.lang, "settings"), parse_mode="HTML", reply_markup=settings_kb(prefs.lang, prefs)
        )
    except TelegramBadRequest:
        pass
    await cb.answer()


@user_router.message(Command("myprompt"))
async def cmd_myprompt(message: types.Message, command: CommandObject):
    uid = message.from_user.id
    lang = await get_lang(uid)
    arg = (command.args or "").strip(            )
            if not arg:
                current = await get_custom_prompt(uid)
                await message.answer(
                    t(lang, "myprompt_help", current=html.escape(current) if current else t(lang, "none")),
                    parse_mode="HTML",
                )
            elif arg.lower() in ("reset", "clear", "off", "сброс"):
                await set_user_field(uid, "custom_prompt", None)
                await message.answer(t(lang, "myprompt_reset"))
            else:
                await set_user_field(uid, "custom_prompt", arg[:CUSTOM_PROMPT_MAX])
                await message.answer(t(lang, "myprompt_saved"))


@user_router.message(F.text.in_(BTN_INFO))
async def cmd_info(message: types.Message):
    uid = message.from_user.id
    prefs = await get_prefs(uid)
    lang = prefs.lang
    model = await get_setting("model", MODEL)
    mode = await get_mode(uid)
    memory = await get_memory_len()
    used = await count_history(uid)
    pinned = await count_pinned(uid)
    if uid in ADMIN_IDS:
        limits = t(lang, "limits_admin")
    else:
        per_min, per_day = await get_limits()
        if per_day > 0:
            per_day += await get_bonus(uid)
        spent = await scalar(
            "SELECT COUNT(*) FROM requests WHERE user_id=? AND ts>?", (uid, int(time.time()) - 86400)
        )
        limits = t(
            lang, "limits_fmt", pm=fmt_limit(per_min, lang), pd=fmt_limit(per_day, lang), u=spent
        )
    await message.answer(
        t(
            lang,
            "info",
            model=html.escape(model),
            mode=MODES[mode]["title"][lang],
            think=t(lang, "think_deep" if prefs.think else "think_fast"),
            language=LANGS[lang],
            used=used,
            n=memory,
            pairs=memory // 2,
            pinned=pinned,
            k=MAX_IMAGES,
            limits=limits,
            bonus=await get_bonus(uid),
        ),
        parse_mode="HTML",
    )


@user_router.message(F.text.in_(BTN_ASK))
async def cmd_ask(message: types.Message):
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "ask"))


def _preview(text: str, n: int = 140) -> str:
    s = " ".join(text.split())
    return s if len(s) <= n else s[: n - 1] + "…"


async def render_saved(uid: int, lang: str):
    rows = await list_pinned(uid)
    if not rows:
        return t(lang, "saved_empty"), None
    items = "\n\n".join(
        f"<b>{i}.</b> {html.escape(_preview(r['content']))}" for i, r in enumerate(rows, 1)
    )
    text = t(lang, "saved_title", n=len(rows), max=PINNED_MAX, items=items)
    b = InlineKeyboardBuilder()
    for i, r in enumerate(rows, 1):
        b.button(text=f"🗑 {i}", callback_data=f"sv:del:{r['id']}")
    b.button(text=t(lang, "btn_del_all"), callback_data="sv:clr")
    k = len(rows)
    sizes = [5] * (k // 5) + ([k % 5] if k % 5 else []) + [1]
    b.adjust(*sizes)
    return text, b.as_markup()


@user_router.message(Command("saved"))
async def cmd_saved(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    text, kb = await render_saved(uid, lang)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@user_router.callback_query(F.data.startswith("sv:"))
async def cb_save(cb: types.CallbackQuery):
    uid = cb.from_user.id
    prefs = await get_prefs(uid)
    lang = prefs.lang
    parts = cb.data.split(":")
    action = parts[1]

    if action == "add":
        content = await get_answer(uid, cb.message.message_id)
        if content is None:
            await cb.answer(t(lang, "save_gone"), show_alert=True)
            return
        status = await add_pinned(uid, content)
        if status == "limit":
            await cb.answer(t(lang, "save_limit", n=PINNED_MAX), show_alert=True)
            return
        regen = prefs.buttons and last_kb_msg.get(uid) == (cb.message.chat.id, cb.message.message_id)
        try:
            await cb.message.edit_reply_markup(reply_markup=answer_kb(lang, regen, "saved"))
        except TelegramBadRequest:
            pass
        await cb.answer(t(lang, "save_ok" if status == "ok" else "save_dup"))
        return

    if action == "del" and len(parts) > 2 and parts[2].isdigit():
        await db.execute("DELETE FROM pinned WHERE id=? AND user_id=?", (int(parts[2]), uid))
        await db.commit()
    elif action == "clr":
        await db.execute("DELETE FROM pinned WHERE user_id=?", (uid,))
        await db.commit()
    else:
        await cb.answer()
        return
    text, kb = await render_saved(uid, lang)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass
    await cb.answer(t(lang, "saved_deleted"))


@user_router.callback_query(F.data.in_({"re:regen", "re:cont"}))
async def cb_retry(cb: types.CallbackQuery):
    uid = cb.from_user.id
    lang = await get_lang(uid)
    hist = await load_history(uid)
    if len(hist) < 2 or hist[-1]["role"] != "assistant" or hist[-2]["role"] != "user":
        await cb.answer(t(lang, "nothing_retry"), show_alert=True)
        return

    if cb.data == "re:regen":
        q = hist[-2]["content"]
        images = None
        m = PHOTO_RE.match(q)
        if m:
            images = last_images.get(uid)
            if not images:
                await cb.answer(t(lang, "photo_gone"), show_alert=True)
                return
            q = q[m.end():].strip()
        await cb.answer()
        await run_ai(cb.message.chat.id, uid, q, images=images, regen=True)
    else:
        await cb.answer()
        await run_ai(cb.message.chat.id, uid, t(lang, "continue_text"))


@user_router.callback_query(F.data == "st:stop")
async def cb_stop(cb: types.CallbackQuery):
    uid = cb.from_user.id
    gen = active_gens.get(uid)
    if gen is None:
        await cb.answer(t(await get_lang(uid), "stop_none"))
        return
    gen.stopped = True
    if gen.task is not None and not gen.task.done():
        gen.task.cancel()
    await cb.answer()


@user_router.message(Command("export"))
async def cmd_export(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    hist = await load_history(uid)
    pins = await list_pinned(uid)
    if not hist and not pins:
        await message.answer(t(lang, "export_empty"))
        return
    lines: list[str] = []
    for m in hist:
        who = t(lang, "export_you" if m["role"] == "user" else "export_ai")
        lines.append(f"{who}:\n{m['content']}\n")
    if pins:
        lines.append(f"— {t(lang, 'export_saved')} —\n")
        for i, r in enumerate(pins, 1):
            lines.append(f"[{i}] {r['content']}\n")
    data = "\n".join(lines).encode("utf-8")
    name = f"dialog-{fmt_ts(int(time.time()), '%Y%m%d-%H%M')}.txt"
    await bot.send_document(
        message.chat.id,
        BufferedInputFile(data, filename=name),
        caption=t(lang, "export_caption", n=len(hist), m=await get_memory_len()),
    )


async def process_photos(chat_id: int, uid: int, photos: list, caption: str, over: int = 0):
    lang = await get_lang(uid)
    images: list[str] = []
    last_err = None
    for ph in photos:
        try:
            buf = await bot.download(ph)
            images.append("data:image/jpeg;base64," + base64.b64encode(buf.read()).decode())
        except Exception as e:
            last_err = e
            logging.warning("Не удалось скачать фото: %s", e)
    if not images:
        await bot.send_message(chat_id, t(lang, "photo_fail", e=last_err))
        return
    if over:
        await bot.send_message(chat_id, t(lang, "album_limit", n=MAX_IMAGES))
    caption = caption or t(lang, "photo_caption" if len(images) == 1 else "photo_caption_multi")
    await run_ai(chat_id, uid, caption, images=images)


async def flush_album(gid: str):
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
        logging.exception("Ошибка при обработке альбома")


@user_router.message(F.photo)
async def handle_photo(message: types.Message):
    uid = message.from_user.id
    caption = (message.caption or "").strip()
    gid = message.media_group_id
    if not gid:
        await process_photos(message.chat.id, uid, [message.photo[-1]], caption)
        return

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


@user_router.message(F.voice)
async def handle_voice(message: types.Message):
    uid = message.from_user.id
    lang = await get_lang(uid)
    if not STT_MODEL:
        await message.answer(t(lang, "voice_off"))
        return
    try:
        buf = await bot.download(message.voice)
        tr = await client.audio.transcriptions.create(
            model=STT_MODEL, file=("voice.ogg", buf.read(), "audio/ogg")
        )
        text = (tr.text or "").strip()
    except Exception as e:
        logging.exception("Ошибка распознавания голоса:")
        await log_error(uid, f"STT {type(e).__name__}: {e}")
        await message.answer(t(lang, "voice_fail", e=e))
        return
    if not text:
        await message.answer(t(lang, "voice_empty"))
        return
    await message.answer(t(lang, "voice_ok", text=text[:3500]))
    await run_ai(message.chat.id, uid, text)


@user_router.message(F.text, ~F.text.startswith("/"))
async def handle_message(message: types.Message):
    await run_ai(message.chat.id, message.from_user.id, message.text)


dp.include_router(admin_router)
dp.include_router(user_router)


async def setup_commands():
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS["en"]])
        for code, items in COMMANDS.items():
            await bot.set_my_commands(
                [BotCommand(command=c, description=d) for c, d in items], language_code=code
            )
    except Exception:
        logging.exception("Не удалось задать меню команд")


async def main():
    await init_db()
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS не задан — админ-панель недоступна.")
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_commands()
    logging.info("🚀 Бот запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
