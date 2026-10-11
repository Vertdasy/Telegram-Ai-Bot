import os
import re
import asyncio
import base64
import logging
import tempfile
import time
import html
from datetime import datetime, timedelta, timezone

import aiosqlite
import httpx
import openai
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware, types
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from openai import AsyncOpenAI

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
# Модель для обработки фото (должна принимать изображения)
VISION_MODEL = os.getenv("VISION_MODEL", "deepseek-v4.1-flash")
# Модель распознавания речи. Если не задана — голосовые отключены.
STT_MODEL = os.getenv("STT_MODEL", "")

# --- скорость и надёжность ---
# Уровень «рассуждения» модели для режимов «Быстро» / «Вдумчиво»:
# default (не передавать параметр), low, medium, high, xhigh. Меняется в админке: ⚡ Скорость.
EFFORT_FAST = os.getenv("EFFORT_FAST", "low")
EFFORT_THINK = os.getenv("EFFORT_THINK", "high")
# Резервная модель: включается, если основная не ответила (таймаут/ошибка). «off» — отключить.
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "deepseek-v4-flash")
AI_TIMEOUT = env_float("AI_TIMEOUT", 150.0)         # макс. пауза без данных от модели, сек
TOTAL_TIMEOUT = env_float("TOTAL_TIMEOUT", 300.0)   # макс. общее время одного запроса, сек
MAX_TOKENS = env_int("MAX_TOKENS", 0)               # 0 = без ограничения длины ответа
HISTORY_CHARS = env_int("HISTORY_CHARS", 8000)      # лимит символов истории в запросе (меньше = быстрее)

# ID админов через запятую, например: ADMIN_IDS=123456789 (свой ID покажет команда /myid)
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

# Лимиты запросов на человека (0 = без лимита). Админ может менять их в панели.
RATE_PER_MIN = env_int("RATE_PER_MIN", 5)
RATE_PER_DAY = env_int("RATE_PER_DAY", 100)

# База данных. На Amvera постоянное хранилище смонтировано в /data
DB_PATH = os.getenv("DB_PATH") or ("/data/bot.db" if os.path.isdir("/data") else "bot.db")
TZ_HOURS = env_int("TZ_HOURS", 4)                   # часовой пояс для времени в админке

MAX_HISTORY_LEN = 10                                # сколько последних сообщений помнить
EDIT_INTERVAL = env_float("EDIT_INTERVAL", 1.0)     # как часто обновлять сообщение при стриминге (сек)
CHUNK = 3500                                        # размер куска текста для одного сообщения Telegram
PHOTO_TAG = "[Photo]"                               # пометка фото-вопроса в истории
PHOTO_TAGS = ("[Photo]", "[Фото]")                  # второй вариант — для баз прошлых версий
CUSTOM_PROMPT_MAX = 1000                            # макс. длина личных инструкций пользователя

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

# ----------------- ЛОКАЛИЗАЦИЯ (интерфейс пользователя) -----------------
# [i18n-start]
LANGS = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}

TEXTS = {
    "ru": {
        "btn_ask": "🤖 Задать вопрос ИИ",
        "btn_mode": "🎭 Режим",
        "btn_clear": "📝 Очистить диалог",
        "btn_info": "ℹ Инфо",
        "btn_settings": "⚙️ Настройки",
        "banned": "🚫 Доступ к боту ограничен.",
        "maintenance": "🔧 Бот на техническом обслуживании. Загляни чуть позже.",
        "start": (
            "👋 Привет, {name}!\n\n"
            "Я бот на базе <b>{model}</b> 🚀\n\n"
            "Я помню историю диалога, умею разные режимы (🎭 Режим) и понимаю фото.\n"
            "Используй меню или просто пиши мне текст!"
        ),
        "cleared": "🧹 История диалога очищена! Начнём с чистого листа.",
        "ask": "💬 Задавай любой вопрос или пришли фото, я готов!",
        "mode_choose": "🎭 Выбери режим работы:",
        "mode_set": "✅ Режим: {mode}\nДиалог очищен, можно писать.",
        "info": (
            "ℹ <b>Информация о боте:</b>\n"
            "• <b>Модель:</b> {model}\n"
            "• <b>Режим:</b> {mode}\n"
            "• <b>Режим ответа:</b> {think}\n"
            "• <b>Память:</b> последние {n} сообщений\n"
            "• <b>Лимиты:</b> {limits}\n"
            "• <b>Фото:</b> поддерживаются\n"
            "• <b>Создатель:</b> @ashoton"
        ),
        "limits_fmt": "{pm}/мин, {pd}/сутки",
        "unlimited": "без лимита",
        "busy": "⏳ Подожди, я ещё отвечаю на предыдущий запрос.",
        "limit_min": "⏳ Слишком часто: не больше {n} запросов в минуту. Подожди {wait}.",
        "limit_day": "📅 Дневной лимит ({n} запросов) исчерпан. Новый запрос будет доступен через {wait}.",
        "wait_sec": "{n} сек.",
        "wait_min": "{n} мин.",
        "wait_hm": "{h} ч {m} мин.",
        "thinking": "🤔 Запрос обрабатывается...",
        "thinking_time": "🧠 Думаю… {s} с",
        "fallback_note": "⚠️ Основная модель не ответила, пробую резервную…",
        "cut_note": "⚠️ Ответ оборвался (сбой связи или таймаут). Напиши «продолжи», чтобы получить остальное.",
        "error": "⚠️ Произошла ошибка при обращении к ИИ: {e}",
        "err_timeout": (
            "⏳ Модель слишком долго отвечала и не уложилась в отведённое время. "
            "Попробуй ещё раз или сформулируй запрос короче."
        ),
        "err_rate": "🚦 Сервис ИИ сейчас перегружен (слишком много запросов). Попробуй через минуту.",
        "err_conn": "📡 Не удалось связаться с сервисом ИИ. Попробуй ещё раз чуть позже.",
        "err_status": "⚠️ Сервис ИИ вернул ошибку (код {code}). Попробуй ещё раз.",
        "empty": "Извините, не удалось сформировать ответ. Попробуйте ещё раз!",
        "btn_regen": "🔄 Повторить",
        "btn_cont": "➡️ Продолжить",
        "nothing_retry": "Нечего повторять",
        "photo_gone": "Фото больше недоступно — отправь его заново.",
        "photo_fail": "⚠️ Не удалось загрузить фото: {e}",
        "photo_caption": "Опиши, что на изображении.",
        "voice_off": (
            "🎤 Голосовые пока не поддерживаются: у используемой модели нет распознавания речи. "
            "Отправь вопрос текстом или фото."
        ),
        "voice_fail": "⚠️ Не удалось распознать голосовое: {e}",
        "voice_empty": "Не удалось разобрать речь, попробуй ещё раз.",
        "voice_ok": "🎤 Распознано: {text}",
        "settings": (
            "⚙️ <b>Настройки</b>\n\n"
            "🌐 Язык интерфейса и ответов ИИ\n"
            "🔘 Кнопки «Повторить» и «Продолжить» под ответами\n"
            "🧠 Режим ответа: <b>⚡ Быстро</b> — ответ сразу, <b>🧠 Вдумчиво</b> — модель рассуждает "
            "дольше (точнее на сложных задачах)\n"
            "📝 Свои инструкции для ИИ: /myprompt"
        ),
        "btn_buttons": "🔘 Кнопки под ответом: {state}",
        "btn_think": "🧠 Режим ответа: {state}",
        "think_fast": "⚡ Быстро",
        "think_deep": "🧠 Вдумчиво",
        "on": "вкл ✅",
        "off": "выкл ❌",
        "lang_changed": "✅ Язык: Русский",
        "myid": "Твой Telegram ID: {id}",
        "denied": "⛔ Нет доступа.",
        "admin_unset": (
            "Админ не настроен. Задай переменную окружения ADMIN_IDS "
            "со своим ID (твой ID: {id}) и перезапусти бота."
        ),
        "continue_text": (
            "Продолжи свой предыдущий ответ с того места, где он оборвался. "
            "Не повторяй уже написанное."
        ),
        "user_instr": "Дополнительные инструкции пользователя (следуй им):",
        "myprompt_help": (
            "📝 <b>Свои инструкции для ИИ</b>\n\n"
            "Опиши, как ИИ должен отвечать именно тебе: стиль, длина, роль. "
            "Работает в режиме «Обычный».\n\n"
            "Задать: <code>/myprompt текст инструкций</code>\n"
            "Сбросить: <code>/myprompt reset</code>\n\n"
            "Сейчас: {current}"
        ),
        "myprompt_saved": "✅ Инструкции сохранены.",
        "myprompt_reset": "♻️ Инструкции сброшены.",
        "none": "не задано",
    },
    "en": {
        "btn_ask": "🤖 Ask the AI",
        "btn_mode": "🎭 Mode",
        "btn_clear": "📝 Clear chat",
        "btn_info": "ℹ Info",
        "btn_settings": "⚙️ Settings",
        "banned": "🚫 Access to the bot is restricted.",
        "maintenance": "🔧 The bot is under maintenance. Please come back a bit later.",
        "start": (
            "👋 Hi, {name}!\n\n"
            "I'm a bot powered by <b>{model}</b> 🚀\n\n"
            "I remember our conversation, have different modes (🎭 Mode) and understand photos.\n"
            "Use the menu or just send me a message!"
        ),
        "cleared": "🧹 Chat history cleared! Let's start fresh.",
        "ask": "💬 Ask me anything or send a photo, I'm ready!",
        "mode_choose": "🎭 Choose a mode:",
        "mode_set": "✅ Mode: {mode}\nChat cleared, you can start writing.",
        "info": (
            "ℹ <b>Bot info:</b>\n"
            "• <b>Model:</b> {model}\n"
            "• <b>Mode:</b> {mode}\n"
            "• <b>Answer style:</b> {think}\n"
            "• <b>Memory:</b> last {n} messages\n"
            "• <b>Limits:</b> {limits}\n"
            "• <b>Photos:</b> supported\n"
            "• <b>Creator:</b> @ashoton"
        ),
        "limits_fmt": "{pm}/min, {pd}/day",
        "unlimited": "unlimited",
        "busy": "⏳ Please wait, I'm still answering your previous request.",
        "limit_min": "⏳ Too fast: no more than {n} requests per minute. Please wait {wait}.",
        "limit_day": "📅 Daily limit ({n} requests) reached. You can send a new request in {wait}.",
        "wait_sec": "{n} sec.",
        "wait_min": "{n} min.",
        "wait_hm": "{h} h {m} min.",
        "thinking": "🤔 Thinking...",
        "thinking_time": "🧠 Thinking… {s} s",
        "fallback_note": "⚠️ The main model didn't respond, trying the backup one…",
        "cut_note": "⚠️ The answer was cut off (connection problem or timeout). Type “continue” to get the rest.",
        "error": "⚠️ An error occurred while contacting the AI: {e}",
        "err_timeout": (
            "⏳ The model took too long to answer and ran out of time. "
            "Please try again or make your request shorter."
        ),
        "err_rate": "🚦 The AI service is overloaded right now (too many requests). Please try again in a minute.",
        "err_conn": "📡 Couldn't reach the AI service. Please try again a bit later.",
        "err_status": "⚠️ The AI service returned an error (code {code}). Please try again.",
        "empty": "Sorry, I couldn't generate an answer. Please try again!",
        "btn_regen": "🔄 Regenerate",
        "btn_cont": "➡️ Continue",
        "nothing_retry": "Nothing to repeat",
        "photo_gone": "The photo is no longer available — please send it again.",
        "photo_fail": "⚠️ Couldn't load the photo: {e}",
        "photo_caption": "Describe what is in the image.",
        "voice_off": (
            "🎤 Voice messages are not supported yet: the model has no speech recognition. "
            "Please send your question as text or a photo."
        ),
        "voice_fail": "⚠️ Couldn't recognize the voice message: {e}",
        "voice_empty": "Couldn't make out any speech, please try again.",
        "voice_ok": "🎤 Recognized: {text}",
        "settings": (
            "⚙️ <b>Settings</b>\n\n"
            "🌐 Interface and AI reply language\n"
            "🔘 «Regenerate» and «Continue» buttons under answers\n"
            "🧠 Answer style: <b>⚡ Fast</b> — instant replies, <b>🧠 Thoughtful</b> — the model reasons "
            "longer (better on hard tasks)\n"
            "📝 Your own instructions for the AI: /myprompt"
        ),
        "btn_buttons": "🔘 Buttons under answers: {state}",
        "btn_think": "🧠 Answer style: {state}",
        "think_fast": "⚡ Fast",
        "think_deep": "🧠 Thoughtful",
        "on": "on ✅",
        "off": "off ❌",
        "lang_changed": "✅ Language: English",
        "myid": "Your Telegram ID: {id}",
        "denied": "⛔ Access denied.",
        "admin_unset": (
            "Admin is not configured. Set the ADMIN_IDS environment variable "
            "to your ID (your ID: {id}) and restart the bot."
        ),
        "continue_text": (
            "Continue your previous answer from where it was cut off. "
            "Do not repeat what has already been written."
        ),
        "user_instr": "Additional instructions from the user (follow them):",
        "myprompt_help": (
            "📝 <b>Your own instructions for the AI</b>\n\n"
            "Describe how the AI should answer you: style, length, role. "
            "Works in the “Default” mode.\n\n"
            "Set: <code>/myprompt your instructions</code>\n"
            "Reset: <code>/myprompt reset</code>\n\n"
            "Current: {current}"
        ),
        "myprompt_saved": "✅ Instructions saved.",
        "myprompt_reset": "♻️ Instructions reset.",
        "none": "not set",
    },
}

DEFAULT_PROMPT = {
    "ru": (
        "Ты полезный и вежливый AI-ассистент. "
        "Отвечай строго на русском языке, независимо от языка обращения."
    ),
    "en": (
        "You are a helpful and polite AI assistant. "
        "Always answer strictly in English, regardless of the language of the request."
    ),
}

# Режимы / персонажи. prompt=None — берётся основной промпт (его можно менять в админке).
MODES = {
    "default": {
        "title": {"ru": "🤖 Обычный", "en": "🤖 Default"},
        "prompt": None,
    },
    "translator": {
        "title": {"ru": "🌍 Переводчик", "en": "🌍 Translator"},
        "prompt": {
            "ru": (
                "Ты профессиональный переводчик. Если текст на русском — переводи его на английский, "
                "во всех остальных случаях переводи на русский. Сохраняй смысл, тон и форматирование. "
                "Выводи только перевод без пояснений, если не просят иное."
            ),
            "en": (
                "You are a professional translator. If the text is in English, translate it into Russian; "
                "in all other cases translate it into English. Preserve the meaning, tone and formatting. "
                "Output only the translation without explanations unless asked otherwise."
            ),
        },
    },
    "programmer": {
        "title": {"ru": "💻 Программист", "en": "💻 Programmer"},
        "prompt": {
            "ru": (
                "Ты опытный программист и наставник. Пиши чистый, рабочий код и оформляй его в блоках кода "
                "с указанием языка. Кратко объясняй решение, указывай на возможные ошибки и крайние случаи. "
                "Отвечай на русском языке."
            ),
            "en": (
                "You are an experienced programmer and mentor. Write clean, working code and put it in code "
                "blocks with the language specified. Briefly explain the solution and point out possible "
                "errors and edge cases. Answer in English."
            ),
        },
    },
    "editor": {
        "title": {"ru": "✍️ Редактор", "en": "✍️ Editor"},
        "prompt": {
            "ru": (
                "Ты внимательный редактор и корректор. Исправляй орфографию, пунктуацию и стиль присланного "
                "текста, сохраняя авторский смысл и голос. Сначала дай исправленный текст, затем коротко "
                "перечисли главные правки. Отвечай на русском языке."
            ),
            "en": (
                "You are a careful editor and proofreader. Fix spelling, punctuation and style in the text "
                "you receive while preserving the author's meaning and voice. First give the corrected text, "
                "then briefly list the main changes. Answer in English."
            ),
        },
    },
}


def t(lang: str, key: str, **kw) -> str:
    s = TEXTS.get(lang, TEXTS["ru"]).get(key) or TEXTS["ru"][key]
    return s.format(**kw) if kw else s


def all_langs(key: str) -> set:
    """Все варианты текста кнопки на всех языках (для фильтров хендлеров)."""
    return {TEXTS[code][key] for code in TEXTS}
# [i18n-end]

BTN_ASK = all_langs("btn_ask")
BTN_MODE = all_langs("btn_mode")
BTN_CLEAR = all_langs("btn_clear")
BTN_INFO = all_langs("btn_info")
BTN_SETTINGS = all_langs("btn_settings")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(
    api_key=AI_API_KEY,
    base_url=AI_BASE_URL,
    # read — максимальная пауза между кусками ответа. Раньше было 60 с, и «долго думающая»
    # модель на сложном запросе падала с ошибкой таймаута, ничего не успев написать.
    timeout=httpx.Timeout(connect=15.0, read=AI_TIMEOUT, write=30.0, pool=30.0),
    max_retries=1,
)

admin_router = Router()
user_router = Router()

user_locks: dict[int, asyncio.Lock] = {}
last_kb_msg: dict[int, tuple[int, int]] = {}   # user_id -> (chat_id, message_id) с кнопками под ответом
last_image: dict[int, str] = {}                # user_id -> data URL последнего фото (для «Повторить»)
bg_tasks: set = set()
reasoning_bad: set[str] = set()                # модели, отвергшие параметр reasoning_effort
STATE = {"maintenance": False, "speedtest": False}
db: aiosqlite.Connection


# ----------------- MARKDOWN -> TELEGRAM HTML -----------------
# [md-start]
def md_to_html(text: str) -> str:
    """Конвертирует Markdown от нейросети в HTML, который понимает Telegram."""
    if text.count("```") % 2:
        text += "\n```"  # незакрытый блок кода (например, во время стриминга)

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
    """Режет длинный ответ на куски, не ломая блоки кода."""
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
# [md-end]


async def edit_md(chat_id: int, message_id: int, md: str, kb=None):
    try:
        await bot.edit_message_text(
            md_to_html(md), chat_id=chat_id, message_id=message_id,
            parse_mode="HTML", reply_markup=kb,
        )
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        # HTML не принят — отправляем как обычный текст
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
    """Повторяет вызов при flood-лимите Telegram."""
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


# ----------------- БАЗА ДАННЫХ -----------------

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
            custom_prompt TEXT
        );
        CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS errors(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER, user_id INTEGER, text TEXT
        );
        CREATE TABLE IF NOT EXISTS requests(ts INTEGER, user_id INTEGER);
        CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
        CREATE INDEX IF NOT EXISTS idx_requests_user ON requests(user_id, ts);
        """
    )
    # миграция для баз, созданных прошлыми версиями бота
    cur = await db.execute("PRAGMA table_info(users)")
    cols = {r["name"] for r in await cur.fetchall()}
    for name, ddl in (
        ("mode", "TEXT DEFAULT 'default'"),
        ("lang", "TEXT DEFAULT 'ru'"),
        ("buttons", "INTEGER DEFAULT 0"),
        ("think", "INTEGER DEFAULT 0"),
        ("custom_prompt", "TEXT"),
    ):
        if name not in cols:
            await db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
    await db.commit()
    STATE["maintenance"] = (await get_setting("maintenance", "0")) == "1"
    logging.info(f"База данных: {DB_PATH}")


async def scalar(query: str, args=()):
    cur = await db.execute(query, args)
    row = await cur.fetchone()
    return row[0] if row else 0


async def get_setting(key: str, default: str) -> str:
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(key: str, value: str):
    await db.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await db.commit()


async def del_setting(key: str):
    await db.execute("DELETE FROM settings WHERE key=?", (key,))
    await db.commit()


async def touch_user(user: types.User):
    now = int(time.time())
    await db.execute(
        "INSERT INTO users(id, username, first_name, joined, last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
        "first_name=excluded.first_name, last_seen=excluded.last_seen",
        (user.id, user.username, user.first_name, now, now),
    )
    await db.commit()


async def is_banned(user_id: int) -> bool:
    return bool(await scalar("SELECT banned FROM users WHERE id=?", (user_id,)))


async def get_prefs(user_id: int) -> tuple[str, bool, bool]:
    """Язык интерфейса, кнопки «Повторить/Продолжить» (вкл/выкл), режим «Вдумчиво» (вкл/выкл)."""
    cur = await db.execute("SELECT lang, buttons, think FROM users WHERE id=?", (user_id,))
    row = await cur.fetchone()
    if not row:
        return "ru", False, False
    lang = row["lang"] if row["lang"] in LANGS else "ru"
    return lang, bool(row["buttons"]), bool(row["think"])


async def get_lang(user_id: int) -> str:
    return (await get_prefs(user_id))[0]


async def set_user_field(user_id: int, field: str, value):
    if field not in ("mode", "lang", "buttons", "think", "custom_prompt"):
        raise ValueError(field)
    await db.execute(f"UPDATE users SET {field}=? WHERE id=?", (value, user_id))
    await db.commit()


async def get_mode(user_id: int) -> str:
    mode = await scalar("SELECT mode FROM users WHERE id=?", (user_id,))
    return mode if mode in MODES else "default"


async def get_custom_prompt(user_id: int) -> str:
    return (await scalar("SELECT custom_prompt FROM users WHERE id=?", (user_id,))) or ""


async def get_system_prompt(mode: str, lang: str) -> str:
    custom = MODES[mode]["prompt"]
    if custom:
        return custom[lang]
    key = "system_prompt" if lang == "ru" else "system_prompt_en"
    return await get_setting(key, DEFAULT_PROMPT[lang])


async def load_history(user_id: int, limit: int = MAX_HISTORY_LEN) -> list:
    cur = await db.execute(
        "SELECT role, content FROM ("
        " SELECT id, role, content FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?"
        ") ORDER BY id",
        (user_id, limit),
    )
    return [{"role": r["role"], "content": r["content"]} for r in await cur.fetchall()]


def trim_by_chars(history: list, limit: int = HISTORY_CHARS) -> list:
    """Оставляет самые свежие сообщения в пределах лимита символов — чем короче запрос, тем быстрее ответ."""
    out: list = []
    total = 0
    for m in reversed(history):
        total += len(m["content"])
        if total > limit and len(out) >= 2:
            break
        out.append(m)
    out.reverse()
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


async def pop_last_exchange(user_id: int):
    await db.execute(
        "DELETE FROM history WHERE id IN ("
        " SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT 2)",
        (user_id,),
    )
    await db.commit()


async def save_exchange(user_id: int, question: str, answer: str):
    await db.executemany(
        "INSERT INTO history(user_id, role, content) VALUES(?,?,?)",
        [(user_id, "user", question), (user_id, "assistant", answer)],
    )
    await db.execute(
        "DELETE FROM history WHERE user_id=? AND id NOT IN ("
        " SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?)",
        (user_id, user_id, MAX_HISTORY_LEN),
    )
    await db.execute("UPDATE users SET msgs = msgs + 1 WHERE id=?", (user_id,))
    await db.commit()


async def clear_history(user_id: int):
    await db.execute("DELETE FROM history WHERE user_id=?", (user_id,))
    await db.commit()
    last_image.pop(user_id, None)


async def log_error(user_id: int, text: str):
    await db.execute(
        "INSERT INTO errors(ts, user_id, text) VALUES(?,?,?)",
        (int(time.time()), user_id, text[:500]),
    )
    await db.execute(
        "DELETE FROM errors WHERE id NOT IN (SELECT id FROM errors ORDER BY id DESC LIMIT 200)"
    )
    await db.commit()


# ----------------- ЛИМИТЫ -----------------

async def get_limits() -> tuple[int, int]:
    per_min = int(await get_setting("rate_min", str(RATE_PER_MIN)))
    per_day = int(await get_setting("rate_day", str(RATE_PER_DAY)))
    return per_min, per_day


def fmt_limit(n: int, lang: str = "ru") -> str:
    return str(n) if n > 0 else t(lang, "unlimited")


def fmt_wait(sec: int, lang: str) -> str:
    sec = max(sec, 1)
    if sec < 60:
        return t(lang, "wait_sec", n=sec)
    if sec < 3600:
        return t(lang, "wait_min", n=sec // 60 + 1)
    return t(lang, "wait_hm", h=sec // 3600, m=sec % 3600 // 60)


async def check_limit(user_id: int, lang: str) -> str | None:
    """Возвращает текст отказа, если лимит исчерпан, иначе None. Админов не ограничиваем."""
    if user_id in ADMIN_IDS:
        return None
    per_min, per_day = await get_limits()
    now = int(time.time())
    if per_min > 0:
        cnt = await scalar(
            "SELECT COUNT(*) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 60)
        )
        if cnt >= per_min:
            oldest = await scalar(
                "SELECT MIN(ts) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 60)
            )
            return t(lang, "limit_min", n=per_min, wait=fmt_wait(oldest + 60 - now, lang))
    if per_day > 0:
        cnt = await scalar(
            "SELECT COUNT(*) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 86400)
        )
        if cnt >= per_day:
            oldest = await scalar(
                "SELECT MIN(ts) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 86400)
            )
            return t(lang, "limit_day", n=per_day, wait=fmt_wait(oldest + 86400 - now, lang))
    return None


# ----------------- КЛАВИАТУРЫ -----------------

def get_main_menu(lang: str):
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text=t(lang, "btn_ask")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_mode")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_clear")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_info")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_settings")))
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)


def answer_kb(lang: str):
    b = InlineKeyboardBuilder()
    b.button(text=t(lang, "btn_regen"), callback_data="re:regen")
    b.button(text=t(lang, "btn_cont"), callback_data="re:cont")
    b.adjust(2)
    return b.as_markup()


def modes_kb(current: str, lang: str):
    b = InlineKeyboardBuilder()
    for key, m in MODES.items():
        b.button(text=("✅ " if key == current else "") + m["title"][lang], callback_data=f"mode:{key}")
    b.adjust(1)
    return b.as_markup()


def settings_kb(lang: str, buttons_on: bool, think: bool):
    b = InlineKeyboardBuilder()
    for code, name in LANGS.items():
        b.button(text=("✅ " if code == lang else "") + name, callback_data=f"set:lang:{code}")
    b.button(
        text=t(lang, "btn_buttons", state=t(lang, "on" if buttons_on else "off")),
        callback_data="set:btns",
    )
    b.button(
        text=t(lang, "btn_think", state=t(lang, "think_deep" if think else "think_fast")),
        callback_data="set:think",
    )
    b.adjust(2, 1, 1)
    return b.as_markup()


# ----------------- MIDDLEWARE: регистрация, бан, техработы -----------------

class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and not user.is_bot:
            await touch_user(user)
            if user.id not in ADMIN_IDS:
                if await is_banned(user.id):
                    await event.answer(t(await get_lang(user.id), "banned"))
                    return
                if STATE["maintenance"]:
                    await event.answer(t(await get_lang(user.id), "maintenance"))
                    return
        return await handler(event, data)


dp.message.outer_middleware(UserMiddleware())
dp.callback_query.outer_middleware(UserMiddleware())


# ================= ЯДРО: запрос к ИИ =================

class Gen:
    """Состояние одной генерации: накопленный текст и тайминги."""

    def __init__(self):
        self.text = ""
        self.started = time.time()
        self.first_any_at = None      # первый кусок данных (в т.ч. «рассуждение»)
        self.first_token_at = None    # первый кусок видимого ответа
        self.thinking = False         # модель присылала рассуждение
        self.dropped_effort = False   # провайдер не принял reasoning_effort
        self.note = ""                # пометка для заглушки (например, переключение на резервную модель)


async def generate(model: str, messages: list, effort, gen: Gen, remember: bool = True):
    """Стримит ответ модели в gen.text. Бросает исключение при сбое (gen.text при этом сохраняется)."""
    eff = effort if effort and effort != "default" else None
    if eff and remember and model in reasoning_bad:
        eff = None  # раньше этот провайдер/модель уже отвергли параметр
    dropped = False

    while True:
        kwargs = dict(model=model, messages=messages, temperature=0.7, stream=True)
        if MAX_TOKENS > 0:
            kwargs["max_tokens"] = MAX_TOKENS
        if eff:
            kwargs["extra_body"] = {"reasoning_effort": eff}

        stream = None
        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                now = time.time()
                if gen.first_any_at is None:
                    gen.first_any_at = now
                if delta.content:
                    if gen.first_token_at is None:
                        gen.first_token_at = now
                    gen.text += delta.content
                elif getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None):
                    gen.thinking = True
            break
        except (openai.BadRequestError, openai.UnprocessableEntityError):
            if eff and not gen.text:
                logging.warning("Провайдер отверг reasoning_effort=%s для %s, повторяю без него", eff, model)
                eff = None
                dropped = True
                gen.dropped_effort = True
                continue
            raise
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass

    if dropped and remember:
        reasoning_bad.add(model)


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
    txt = t(lang, "thinking_time", s=int(time.time() - gen.started))
    return f"{gen.note}\n{txt}" if gen.note else txt


async def run_ai(
    chat_id: int,
    user_id: int,
    question: str,
    image_url: str | None = None,
    regen: bool = False,
):
    lang, buttons_on, think = await get_prefs(user_id)

    lock = user_locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        await bot.send_message(chat_id, t(lang, "busy"))
        return

    async with lock:
        denied = await check_limit(user_id, lang)
        if denied:
            await bot.send_message(chat_id, denied)
            return

        # убираем кнопки под прошлым ответом
        old = last_kb_msg.pop(user_id, None)
        if old:
            try:
                await bot.edit_message_reply_markup(chat_id=old[0], message_id=old[1], reply_markup=None)
            except Exception:
                pass

        # заглушка сразу, всё остальное — потом
        sent = await bot.send_message(chat_id, t(lang, "thinking"))

        # считаем попытку сразу, чтобы лимит нельзя было обойти ошибками
        await db.execute("INSERT INTO requests(ts, user_id) VALUES(?,?)", (int(time.time()), user_id))
        await db.commit()

        mode = await get_mode(user_id)
        prompt = await get_system_prompt(mode, lang)
        if mode == "default":
            custom = await get_custom_prompt(user_id)
            if custom:
                prompt += "\n\n" + t(lang, "user_instr") + "\n" + custom
        model = VISION_MODEL if image_url else await get_setting("model", MODEL)
        effort = await get_setting(
            "effort_think" if think else "effort_fast", EFFORT_THINK if think else EFFORT_FAST
        )
        fallback = await get_setting("fallback_model", FALLBACK_MODEL)

        history = await load_history(user_id, MAX_HISTORY_LEN + (2 if regen else 0))
        if regen:
            history = history[:-2]
        history = trim_by_chars(history)

        if image_url:
            user_content = [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
            stored_question = f"{PHOTO_TAG} {question}"
        else:
            user_content = question
            stored_question = question

        messages = [{"role": "system", "content": prompt}] + history + [
            {"role": "user", "content": user_content}
        ]

        gen = Gen()

        async def ticker():
            """Обновляет сообщение в Telegram независимо от чтения потока: не тормозит приём ответа."""
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
                                await edit_md(chat_id, sent.message_id, shown)
                                last_shown = shown
                            except TelegramRetryAfter as e:
                                await asyncio.sleep(e.retry_after)
                            except Exception:
                                pass
                    elif now - gen.started >= 4 and now - last_wait_edit >= 3:
                        last_wait_edit = now
                        try:
                            await bot.edit_message_text(
                                waiting_text(lang, gen), chat_id=chat_id, message_id=sent.message_id
                            )
                        except TelegramRetryAfter as e:
                            await asyncio.sleep(e.retry_after)
                        except Exception:
                            pass
            except asyncio.CancelledError:
                return

        # --- генерация: основная модель, при сбое без единого символа — резервная ---
        attempts = [(model, effort)]
        if fallback and fallback != "off" and fallback != model and not image_url:
            attempts.append((fallback, None))

        err = None
        ticker_task = asyncio.create_task(ticker())
        try:
            for i, (m, eff) in enumerate(attempts):
                try:
                    await asyncio.wait_for(generate(m, messages, eff, gen), TOTAL_TIMEOUT)
                    err = None
                    break
                except Exception as e:
                    err = e
                    logging.warning("Сбой модели %s: %s: %s", m, type(e).__name__, e)
                    await log_error(user_id, f"{m}: {type(e).__name__}: {e}")
                    if gen.text or i == len(attempts) - 1:
                        break
                    gen.note = t(lang, "fallback_note")
                    gen.started = time.time()
        finally:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass

        final = gen.text.strip()

        if not final:
            if err is None:
                await log_error(user_id, "Пустой ответ от модели")
            msg = friendly_error(lang, err) if err is not None else t(lang, "empty")
            try:
                await bot.edit_message_text(msg[:4096], chat_id=chat_id, message_id=sent.message_id)
            except Exception:
                await bot.send_message(chat_id, msg[:4096])
            return

        # ответ обрывался на середине: показываем, что успели получить, и пометку
        display = final + ("\n\n" + t(lang, "cut_note") if err is not None else "")

        # --- доставка с форматированием (и кнопками, если включены в настройках) ---
        try:
            chunks = split_markdown(display)
            kb = answer_kb(lang) if buttons_on else None
            await with_retry(
                edit_md, chat_id, sent.message_id, chunks[0], kb if len(chunks) == 1 else None
            )
            last_id = sent.message_id
            for i, part in enumerate(chunks[1:], start=1):
                msg = await with_retry(send_md, chat_id, part, kb if i == len(chunks) - 1 else None)
                last_id = msg.message_id
            if kb:
                last_kb_msg[user_id] = (chat_id, last_id)
        except Exception:
            logging.exception("Не удалось доставить ответ")
            await log_error(user_id, "Не удалось доставить ответ в Telegram")

        # --- сохраняем в память ---
        if regen:
            await pop_last_exchange(user_id)
        await save_exchange(user_id, stored_question, final)
        if image_url:
            last_image[user_id] = image_url


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
    b.button(text="⏱ Лимиты", callback_data="adm:limits")
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
    custom = r["custom_prompt"]
    text = (
        f"👤 <b>{fmt_user(r)}</b>\n"
        f"ID: <code>{uid}</code>\n"
        f"Статус: {'🚫 забанен' if r['banned'] else '✅ активен'}\n"
        f"Регистрация: {fmt_ts(r['joined'], '%d.%m.%Y %H:%M')}\n"
        f"Последняя активность: {fmt_ts(r['last_seen'], '%d.%m.%Y %H:%M')}\n"
        f"Ответов получено: {r['msgs']} (за 24ч запросов: {req24})\n"
        f"Режим: {html.escape(str(r['mode']))} · язык: {html.escape(str(r['lang']))} · "
        f"стиль: {'вдумчиво' if r['think'] else 'быстро'} · кнопки: {'вкл' if r['buttons'] else 'выкл'}\n"
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
            f"🧠 Модель: <code>{html.escape(model)}</code>\n\n"
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
        await show(cb, "Пришли ID пользователя, чтобы посмотреть его карточку.\n/cancel — отмена")

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
        task = asyncio.create_task(do_broadcast(text, cb.message.chat.id))
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)
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
async def cmd_start(message: types.Message):
    lang = await get_lang(message.from_user.id)
    model = await get_setting("model", MODEL)
    await message.answer(
        t(
            lang,
            "start",
            name=html.escape(message.from_user.first_name or ("друг" if lang == "ru" else "friend")),
            model=html.escape(model),
        ),
        parse_mode="HTML",
        reply_markup=get_main_menu(lang),
    )


@user_router.message(Command("clear", "reset"))
@user_router.message(F.text.in_(BTN_CLEAR))
async def cmd_clear(message: types.Message):
    lang = await get_lang(message.from_user.id)
    await clear_history(message.from_user.id)
    await message.answer(t(lang, "cleared"))


@user_router.message(Command("mode"))
@user_router.message(F.text.in_(BTN_MODE))
async def cmd_mode(message: types.Message):
    lang = await get_lang(message.from_user.id)
    current = await get_mode(message.from_user.id)
    await message.answer(t(lang, "mode_choose"), reply_markup=modes_kb(current, lang))


@user_router.callback_query(F.data.startswith("mode:"))
async def cb_mode(cb: types.CallbackQuery):
    key = cb.data.split(":", 1)[1]
    if key not in MODES:
        await cb.answer()
        return
    lang = await get_lang(cb.from_user.id)
    await set_user_field(cb.from_user.id, "mode", key)
    await clear_history(cb.from_user.id)  # чтобы контекст прошлого режима не мешал
    try:
        await cb.message.edit_text(
            t(lang, "mode_set", mode=MODES[key]["title"][lang]),
            reply_markup=modes_kb(key, lang),
        )
    except TelegramBadRequest:
        pass
    await cb.answer()


@user_router.message(Command("settings"))
@user_router.message(F.text.in_(BTN_SETTINGS))
async def cmd_settings(message: types.Message):
    lang, buttons_on, think = await get_prefs(message.from_user.id)
    await message.answer(
        t(lang, "settings"), parse_mode="HTML", reply_markup=settings_kb(lang, buttons_on, think)
    )


@user_router.callback_query(F.data.startswith("set:"))
async def cb_settings(cb: types.CallbackQuery):
    uid = cb.from_user.id
    parts = cb.data.split(":")
    lang, buttons_on, think = await get_prefs(uid)

    if parts[1] == "lang" and len(parts) > 2 and parts[2] in LANGS:
        if parts[2] != lang:
            lang = parts[2]
            await set_user_field(uid, "lang", lang)
            # у reply-клавиатуры нельзя поменять текст кнопок — присылаем новую
            await bot.send_message(
                cb.message.chat.id, t(lang, "lang_changed"), reply_markup=get_main_menu(lang)
            )
    elif parts[1] == "btns":
        buttons_on = not buttons_on
        await set_user_field(uid, "buttons", 1 if buttons_on else 0)
    elif parts[1] == "think":
        think = not think
        await set_user_field(uid, "think", 1 if think else 0)

    try:
        await cb.message.edit_text(
            t(lang, "settings"), parse_mode="HTML", reply_markup=settings_kb(lang, buttons_on, think)
        )
    except TelegramBadRequest:
        pass
    await cb.answer()


@user_router.message(Command("myprompt"))
async def cmd_myprompt(message: types.Message, command: CommandObject):
    uid = message.from_user.id
    lang = await get_lang(uid)
    arg = (command.args or "").strip()
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
    lang, _, think = await get_prefs(message.from_user.id)
    model = await get_setting("model", MODEL)
    mode = await get_mode(message.from_user.id)
    per_min, per_day = await get_limits()
    limits = t(lang, "limits_fmt", pm=fmt_limit(per_min, lang), pd=fmt_limit(per_day, lang))
    await message.answer(
        t(
            lang,
            "info",
            model=html.escape(model),
            mode=MODES[mode]["title"][lang],
            think=t(lang, "think_deep" if think else "think_fast"),
            n=MAX_HISTORY_LEN,
            limits=limits,
        ),
        parse_mode="HTML",
    )


@user_router.message(F.text.in_(BTN_ASK))
async def cmd_ask(message: types.Message):
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "ask"))


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
        image = None
        tag = next((tg for tg in PHOTO_TAGS if q.startswith(tg)), None)
        if tag:
            image = last_image.get(uid)
            if not image:
                await cb.answer(t(lang, "photo_gone"), show_alert=True)
                return
            q = q[len(tag):].strip()
        await cb.answer()
        await run_ai(cb.message.chat.id, uid, q, image_url=image, regen=True)
    else:
        await cb.answer()
        await run_ai(cb.message.chat.id, uid, t(lang, "continue_text"))


@user_router.message(F.photo)
async def handle_photo(message: types.Message):
    lang = await get_lang(message.from_user.id)
    try:
        buf = await bot.download(message.photo[-1])
        data = buf.read()
    except Exception as e:
        await message.answer(t(lang, "photo_fail", e=e))
        return
    url = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    caption = (message.caption or "").strip() or t(lang, "photo_caption")
    await run_ai(message.chat.id, message.from_user.id, caption, image_url=url)


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


# Админский роутер подключаем первым, чтобы он перехватывал свои команды и ввод
dp.include_router(admin_router)
dp.include_router(user_router)


async def main():
    await init_db()
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS не задан — админ-панель недоступна. Узнай ID командой /myid.")
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
