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
# Уровень «рассуждения» модели для стилей «Быстро» / «Вдумчиво»:
# default (не передавать параметр), low, medium, high, xhigh. Меняется в админке: ⚡ Скорость.
EFFORT_FAST = os.getenv("EFFORT_FAST", "low")
EFFORT_THINK = os.getenv("EFFORT_THINK", "high")
# Резервная модель: включается, если основная не ответила (таймаут/ошибка). «off» — отключить.
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "deepseek-v4-flash")
AI_TIMEOUT = env_float("AI_TIMEOUT", 150.0)         # макс. пауза без данных от модели, сек
TOTAL_TIMEOUT = env_float("TOTAL_TIMEOUT", 300.0)   # макс. общее время одного запроса, сек
MAX_TOKENS = env_int("MAX_TOKENS", 0)               # 0 = без ограничения длины ответа

# --- память диалога ---
# Бот хранит И вопросы пользователя, И ответы ИИ (каждое — отдельное «сообщение»),
# поэтому 30 сообщений = 15 пар «вопрос — ответ». Размер меняется в админке: 🧮 Память.
MEMORY_DEFAULT = env_int("MEMORY_LEN", 30)
MEMORY_MIN, MEMORY_MAX = 2, 200
# Лимит объёма истории в одном запросе (символов). Если диалог длиннее — самые старые
# сообщения не отправляются модели (так ответы быстрее). Меняется в админке.
HISTORY_CHARS = env_int("HISTORY_CHARS", 40000)

# --- сохранённые сообщения («📌 Сохранить») ---
PINNED_MAX = env_int("PINNED_MAX", 10)              # сколько сообщений можно сохранить
PINNED_ITEM_CHARS = 4000                            # макс. длина одного сохранённого сообщения
PINNED_TOTAL_CHARS = 8000                           # сколько символов сохранённого уходит модели
ANSWERS_KEEP = 40                                   # сколько последних ответов помнить для кнопки «Сохранить»

# --- фото и очередь ---
MAX_IMAGES = env_int("MAX_IMAGES", 6)               # макс. фото в одном сообщении/альбоме
ALBUM_WAIT = env_float("ALBUM_WAIT", 1.2)           # сколько ждать остальные фото альбома, сек
MAX_QUEUE = env_int("MAX_QUEUE", 2)                 # сколько запросов одного человека могут ждать очереди

# --- реклама AdsGram (https://adsgram.ai) ---
# ID рекламного блока типа «Бот» из кабинета AdsGram: можно «bot-53426» или просто 53426.
# Пока переменная пустая — реклама полностью выключена.
ADSGRAM_BLOCK_ID = re.sub(r"\D", "", os.getenv("ADSGRAM_BLOCK_ID", ""))
ADSGRAM_TOKEN = os.getenv("ADSGRAM_TOKEN", "")      # токен аккаунта (если AdsGram его требует; профиль в кабинете)
ADS_EVERY = env_int("ADS_EVERY", 6)                 # показывать рекламу после каждого N-го ответа (меняется в админке)
ADS_MIN_GAP = env_int("ADS_MIN_GAP", 600)           # не чаще одного раза в N секунд на человека
ADS_API = "https://api.adsgram.ai/advbot"

# --- приглашения друзей ---
REF_BONUS = env_int("REF_BONUS", 20)      # сколько запросов в сутки получает каждый (приглашённый и пригласивший)
REF_MAX = env_int("REF_MAX", 25)          # за скольких друзей пригласивший получает бонус
SRC_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")   # метки источников в ссылке: t.me/бот?start=метка

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

EDIT_INTERVAL = env_float("EDIT_INTERVAL", 1.0)     # как часто обновлять сообщение при стриминге (сек)
CHUNK = 3500                                        # размер куска текста для одного сообщения Telegram
CUSTOM_PROMPT_MAX = 1000                            # макс. длина личных инструкций пользователя

# Пометка фото-вопроса в истории: «[Photo]» или «[Photo x3]» (старый вариант «[Фото]» тоже понимаем)
PHOTO_RE = re.compile(r"^\[(?:Photo|Фото)(?: x(\d+))?\]\s*")


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

# ----------------- ЛОКАЛИЗАЦИЯ (интерфейс пользователя) -----------------
# [i18n-start]
LANGS = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}

TEXTS = {
    "ru": {
        # --- кнопки главного меню (тексты не менять: по ним бот узнаёт нажатия) ---
        "btn_ask": "🤖 Задать вопрос ИИ",
        "btn_mode": "🎭 Режим",
        "btn_clear": "📝 Очистить диалог",
        "btn_info": "ℹ Инфо",
        "btn_settings": "⚙️ Настройки",
        "btn_invite": "🎁 Пригласить",
        "btn_help": "❓ Помощь",
        "menu_updated": "✨ В меню появились кнопки «🎁 Пригласить» (бонусные запросы за друзей) и «❓ Помощь» (все возможности и команды).",
        "banned": "🚫 Доступ к боту ограничен.",
        "maintenance": "🔧 Бот на техническом обслуживании. Загляни чуть позже.",
        # --- приветствие и справка ---
        "start": (
            "👋 <b>Привет, {name}!</b>\n\n"
            "Я — ИИ-ассистент на базе <b>{model}</b>. Отвечаю на вопросы, пишу и правлю тексты, "
            "помогаю с кодом, перевожу и разбираю изображения.\n\n"
            "✨ <b>Что я умею</b>\n"
            "• 💬 Помню разговор — последние {n} сообщений (вопросы и ответы вместе)\n"
            "• 🎭 Меняю роль: переводчик, программист, редактор — кнопка «Режим»\n"
            "• 🖼 Смотрю фото и альбомы — до {k} изображений за раз\n"
            "• 📌 Сохраняю важное: включи «Сохранить» в настройках — сообщение останется в памяти, "
            "пока ты не очистишь диалог\n"
            "• ⏹ Ответ можно остановить кнопкой «Стоп»\n"
            "• 📄 Выгружаю диалог файлом — /export\n"
            "• 🎁 Приглашай друзей и получай бонусные запросы — кнопка «Пригласить»\n\n"
            "🚀 <b>С чего начать</b>\n"
            "Напиши вопрос или пришли фото. Меню внизу: режимы, настройки, очистка диалога, "
            "приглашение друзей и «Помощь» со всеми командами."
        ),
        "help": (
            "❓ <b>Справка</b>\n\n"
            "<b>Как пользоваться</b>\n"
            "Пиши вопрос обычным текстом или присылай фото — можно сразу несколько (до {k}), "
            "с подписью или без. Ответ приходит по мере написания, его можно остановить кнопкой «⏹ Стоп». "
            "Если отправишь несколько сообщений подряд, отвечу на них по очереди.\n\n"
            "<b>Память</b>\n"
            "Я помню последние {n} сообщений — и твои вопросы, и мои ответы. Фото в памяти остаётся "
            "только пометкой, сама картинка не хранится. Важное можно закрепить кнопкой «📌 Сохранить» "
            "(включается в настройках): такие сообщения остаются, пока ты не очистишь диалог. "
            "При смене режима диалог сбрасывается, сохранённое остаётся.\n\n"
            "<b>Кнопки меню</b>\n"
            "🤖 Задать вопрос · 🎭 Режим · 📝 Очистить диалог (с подтверждением) · ℹ Инфо · "
            "⚙️ Настройки · 🎁 Пригласить · ❓ Помощь\n\n"
            "<b>Команды</b>\n"
            "/mode — выбрать режим (роль ИИ)\n"
            "/settings — язык, кнопки под ответом, кнопка «Сохранить», стиль ответа\n"
            "/saved — сохранённые сообщения\n"
            "/export — выгрузить диалог файлом\n"
            "/invite — пригласить друзей и получить бонус к лимиту\n"
            "/clear — очистить диалог\n"
            "/myprompt — свои инструкции для ИИ\n"
            "/myid — твой Telegram ID"
        ),
        "invite": (
            "🎁 <b>Пригласи друзей — получи больше запросов</b>\n\n"
            "Когда друг по твоей ссылке задаст боту первый вопрос, вы оба получите "
            "<b>+{b}</b> запросов в сутки насовсем (бонус за {m} друзей).\n\n"
            "🔗 Твоя ссылка:\n<code>{link}</code>\n\n"
            "👥 Перешли по ссылке: <b>{n}</b>, из них уже пользуются: <b>{paid}</b>\n"
            "🎁 Твой бонус: <b>+{bonus}</b> запросов в сутки"
        ),
        "invite_unavailable": "Не получилось сформировать ссылку, попробуй чуть позже.",
        "btn_share": "📤 Поделиться ботом",
        "share_text": "ИИ-помощник в Telegram: отвечает на вопросы, пишет код, переводит, разбирает фото и помнит разговор 🤖",
        "ref_welcome": "🎉 Добро пожаловать! Ты пришёл по приглашению — тебе добавлено <b>+{b}</b> запросов в сутки.",
        "ref_notify": "🎁 Твой друг начал пользоваться ботом! Тебе добавлено <b>+{b}</b> запросов в сутки. Подробнее: /invite",
        "ask": "💬 Задавай любой вопрос или пришли фото — можно сразу несколько. Я готов!",
        # --- режимы ---
        "mode_choose": (
            "🎭 <b>Выбери режим</b>\n\n{list}\n\n"
            "При смене режима диалог сбрасывается, а сохранённые сообщения остаются."
        ),
        "mode_set": (
            "✅ <b>Режим: {mode}</b>\n{desc}\n\n"
            "🧹 Прошлый диалог сброшен — он не повлияет на новый режим.{pinned}\n\n"
            "Можно писать!"
        ),
        "mode_pinned": "\n📌 Сохранённые сообщения ({n}) остались в памяти.",
        "mode_same": "Этот режим уже включён",
        # --- инфо ---
        "info": (
            "ℹ <b>О боте</b>\n\n"
            "🧠 <b>Модель:</b> {model}\n"
            "🎭 <b>Режим:</b> {mode}\n"
            "⚡ <b>Стиль ответа:</b> {think}\n"
            "🌐 <b>Язык:</b> {language}\n"
            "💾 <b>Память:</b> {used} из {n} сообщений (вопросы и ответы вместе, ≈{pairs} пар)\n"
            "📌 <b>Сохранено:</b> {pinned}\n"
            "🖼 <b>Фото:</b> до {k} за раз\n"
            "⏱ <b>Лимиты:</b> {limits}\n"
            "🎁 <b>Бонус за друзей:</b> +{bonus} запросов в сутки (/invite)\n"
            "👤 <b>Создатель:</b> @ashoton"
        ),
        "limits_fmt": "{pm}/мин, {pd}/сутки (за 24 ч использовано: {u})",
        "limits_admin": "без ограничений (админ)",
        "unlimited": "без лимита",
        # --- очистка ---
        "clear_confirm": (
            "🧹 <b>Очистить диалог?</b>\n"
            "В памяти {n} сообщений — они будут удалены, и ИИ забудет контекст разговора."
        ),
        "clear_confirm_pinned": (
            "🧹 <b>Очистить диалог?</b>\n"
            "В памяти {n} сообщений, и ещё 📌 сохранено: {p}.\n"
            "Можно очистить всё или оставить только сохранённые."
        ),
        "btn_clear_yes": "🧹 Да, очистить",
        "btn_clear_all": "🧹 Очистить всё",
        "btn_clear_keep": "📌 Оставить сохранённые",
        "btn_cancel": "✖ Отмена",
        "cleared": "🧹 История диалога очищена! Начнём с чистого листа.",
        "cleared_keep": "🧹 Диалог очищен. Сохранённые сообщения ({p}) остались в памяти.",
        "clear_cancelled": "Отменено — диалог остался как был.",
        "clear_empty": "Память уже пуста — очищать нечего.",
        # --- очередь, стоп, ожидание ---
        "busy": "⏳ Подожди, я ещё отвечаю на предыдущие запросы.",
        "queued": "⏳ Предыдущий запрос ещё обрабатывается — отвечу следом.",
        "btn_stop": "⏹ Стоп",
        "stopped": "⏹ Остановлено.",
        "stopped_note": "⏹ Ответ остановлен.",
        "stop_none": "Сейчас нечего останавливать",
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
        # --- кнопки под ответом ---
        "btn_regen": "🔄 Повторить",
        "btn_cont": "➡️ Продолжить",
        "nothing_retry": "Нечего повторять",
        "btn_save": "📌 Сохранить",
        "btn_saved": "✅ Сохранено",
        "save_ok": "📌 Сохранено в память. Список — /saved",
        "save_dup": "Это сообщение уже сохранено",
        "save_limit": "Можно хранить до {n} сообщений. Удали лишние через /saved",
        "save_gone": "Это сообщение уже нельзя сохранить — диалог был очищен.",
        "saved_title": (
            "📌 <b>Сохранённые сообщения</b> ({n}/{max})\n"
            "Они остаются в памяти ИИ, пока ты не очистишь историю.\n\n{items}"
        ),
        "saved_empty": (
            "📌 Сохранённых сообщений пока нет.\n\n"
            "Включи кнопку «Сохранить» в ⚙️ Настройках — она появится под ответами ИИ."
        ),
        "btn_del_all": "🗑 Удалить все",
        "saved_deleted": "Удалено",
        "pinned_hdr": "Сообщения, которые пользователь сохранил как важный контекст (учитывай их в ответах):",
        # --- фото ---
        "photo_gone": "Фото больше недоступно — отправь его заново.",
        "photo_fail": "⚠️ Не удалось загрузить фото: {e}",
        "photo_caption": "Опиши, что на изображении.",
        "photo_caption_multi": "Опиши, что на этих изображениях.",
        "album_limit": "📎 Взял первые {n} фото — это максимум за один раз.",
        # --- голос ---
        "voice_off": (
            "🎤 Голосовые пока не поддерживаются: у используемой модели нет распознавания речи. "
            "Отправь вопрос текстом или фото."
        ),
        "voice_fail": "⚠️ Не удалось распознать голосовое: {e}",
        "voice_empty": "Не удалось разобрать речь, попробуй ещё раз.",
        "voice_ok": "🎤 Распознано: {text}",
        # --- настройки ---
        "settings": (
            "⚙️ <b>Настройки</b>\n\n"
            "🌐 <b>Язык</b> — интерфейс бота и ответы ИИ.\n"
            "🔁 <b>Повторить / Продолжить</b> — кнопки под ответом.\n"
            "📌 <b>Сохранить</b> — кнопка под ответом: закрепляет сообщение в памяти, пока ты не очистишь "
            "историю (список — /saved).\n"
            "🧠 <b>Стиль ответа</b> — ⚡ Быстро: отвечает сразу; 🧠 Вдумчиво: рассуждает дольше, точнее "
            "в сложных задачах.\n"
            "✏️ Свои инструкции для ИИ — /myprompt"
        ),
        "btn_buttons": "🔁 Повторить/Продолжить: {state}",
        "btn_savebtn": "📌 Кнопка «Сохранить»: {state}",
        "btn_think": "🧠 Стиль ответа: {state}",
        "think_fast": "⚡ Быстро",
        "think_deep": "🧠 Вдумчиво",
        "on": "вкл ✅",
        "off": "выкл ❌",
        "lang_changed": "✅ Язык: Русский",
        # --- прочее ---
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
        "export_empty": "Нечего выгружать — диалог пуст.",
        "export_caption": "📄 Диалог: {n} сообщений (бот хранит только последние {m}).",
        "export_you": "Ты",
        "export_ai": "ИИ",
        "export_saved": "Сохранённые сообщения",
    },
    "en": {
        "btn_ask": "🤖 Ask the AI",
        "btn_mode": "🎭 Mode",
        "btn_clear": "📝 Clear chat",
        "btn_info": "ℹ Info",
        "btn_settings": "⚙️ Settings",
        "btn_invite": "🎁 Invite",
        "btn_help": "❓ Help",
        "menu_updated": "✨ The menu now has “🎁 Invite” (bonus requests for friends) and “❓ Help” (all features and commands) buttons.",
        "banned": "🚫 Access to the bot is restricted.",
        "maintenance": "🔧 The bot is under maintenance. Please come back a bit later.",
        "start": (
            "👋 <b>Hi, {name}!</b>\n\n"
            "I'm an AI assistant powered by <b>{model}</b>. I answer questions, write and edit texts, "
            "help with code, translate and look at images.\n\n"
            "✨ <b>What I can do</b>\n"
            "• 💬 Remember our conversation — the last {n} messages (questions and answers together)\n"
            "• 🎭 Switch roles: translator, programmer, editor — the “Mode” button\n"
            "• 🖼 Look at photos and albums — up to {k} images at once\n"
            "• 📌 Keep what matters: turn on “Save” in settings — the message stays in memory "
            "until you clear the chat\n"
            "• ⏹ You can stop an answer with the “Stop” button\n"
            "• 📄 Export the chat as a file — /export\n"
            "• 🎁 Invite friends and get bonus requests — the “Invite” button\n\n"
            "🚀 <b>Getting started</b>\n"
            "Type a question or send a photo. The menu below has modes, settings, chat clearing, "
            "friend invites and “Help” with all commands."
        ),
        "help": (
            "❓ <b>Help</b>\n\n"
            "<b>How to use</b>\n"
            "Type a question as plain text or send photos — several at once (up to {k}), with a caption "
            "or without. The answer appears as it is written and can be stopped with the “⏹ Stop” button. "
            "If you send several messages in a row, I'll answer them one by one.\n\n"
            "<b>Memory</b>\n"
            "I remember the last {n} messages — both your questions and my answers. A photo stays in "
            "memory only as a marker, the image itself isn't stored. You can pin something important with "
            "the “📌 Save” button (turn it on in settings): saved messages stay until you clear the chat. "
            "Switching the mode resets the chat, saved messages are kept.\n\n"
            "<b>Menu buttons</b>\n"
            "🤖 Ask AI · 🎭 Mode · 📝 Clear chat (with confirmation) · ℹ Info · "
            "⚙️ Settings · 🎁 Invite · ❓ Help\n\n"
            "<b>Commands</b>\n"
            "/mode — choose a mode (the AI's role)\n"
            "/settings — language, buttons under answers, “Save” button, answer style\n"
            "/saved — saved messages\n"
            "/export — download the chat as a file\n"
            "/invite — invite friends and get a limit bonus\n"
            "/clear — clear the chat\n"
            "/myprompt — your own instructions for the AI\n"
            "/myid — your Telegram ID"
        ),
        "invite": (
            "🎁 <b>Invite friends — get more requests</b>\n\n"
            "When a friend opens the bot with your link and asks their first question, you both get "
            "<b>+{b}</b> requests per day for good (bonus for up to {m} friends).\n\n"
            "🔗 Your link:\n<code>{link}</code>\n\n"
            "👥 Opened the link: <b>{n}</b>, already using the bot: <b>{paid}</b>\n"
            "🎁 Your bonus: <b>+{bonus}</b> requests per day"
        ),
        "invite_unavailable": "Couldn't build the link, please try again in a moment.",
        "btn_share": "📤 Share the bot",
        "share_text": "An AI assistant in Telegram: answers questions, writes code, translates, analyzes photos and remembers the chat 🤖",
        "ref_welcome": "🎉 Welcome! You joined by invitation — <b>+{b}</b> requests per day were added for you.",
        "ref_notify": "🎁 Your friend started using the bot! <b>+{b}</b> requests per day were added for you. Details: /invite",
        "ask": "💬 Ask me anything or send a photo — several at once is fine. I'm ready!",
        "mode_choose": (
            "🎭 <b>Choose a mode</b>\n\n{list}\n\n"
            "Switching the mode resets the chat; saved messages are kept."
        ),
        "mode_set": (
            "✅ <b>Mode: {mode}</b>\n{desc}\n\n"
            "🧹 The previous chat was reset — it won't affect the new mode.{pinned}\n\n"
            "You can start writing!"
        ),
        "mode_pinned": "\n📌 Saved messages ({n}) are still in memory.",
        "mode_same": "This mode is already on",
        "info": (
            "ℹ <b>About the bot</b>\n\n"
            "🧠 <b>Model:</b> {model}\n"
            "🎭 <b>Mode:</b> {mode}\n"
            "⚡ <b>Answer style:</b> {think}\n"
            "🌐 <b>Language:</b> {language}\n"
            "💾 <b>Memory:</b> {used} of {n} messages (questions and answers together, ≈{pairs} pairs)\n"
            "📌 <b>Saved:</b> {pinned}\n"
            "🖼 <b>Photos:</b> up to {k} at once\n"
            "⏱ <b>Limits:</b> {limits}\n"
            "🎁 <b>Friend bonus:</b> +{bonus} requests per day (/invite)\n"
            "👤 <b>Creator:</b> @ashoton"
        ),
        "limits_fmt": "{pm}/min, {pd}/day ({u} used in the last 24 h)",
        "limits_admin": "no limits (admin)",
        "unlimited": "unlimited",
        "clear_confirm": (
            "🧹 <b>Clear the chat?</b>\n"
            "{n} messages are in memory — they will be deleted and the AI will forget the conversation."
        ),
        "clear_confirm_pinned": (
            "🧹 <b>Clear the chat?</b>\n"
            "{n} messages are in memory, plus 📌 saved: {p}.\n"
            "You can clear everything or keep only the saved ones."
        ),
        "btn_clear_yes": "🧹 Yes, clear",
        "btn_clear_all": "🧹 Clear everything",
        "btn_clear_keep": "📌 Keep saved",
        "btn_cancel": "✖ Cancel",
        "cleared": "🧹 Chat history cleared! Let's start fresh.",
        "cleared_keep": "🧹 Chat cleared. Saved messages ({p}) are still in memory.",
        "clear_cancelled": "Cancelled — the chat is unchanged.",
        "clear_empty": "Memory is already empty — nothing to clear.",
        "busy": "⏳ Please wait, I'm still answering your previous requests.",
        "queued": "⏳ Your previous request is still being processed — I'll answer right after.",
        "btn_stop": "⏹ Stop",
        "stopped": "⏹ Stopped.",
        "stopped_note": "⏹ Answer stopped.",
        "stop_none": "Nothing to stop right now",
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
        "btn_save": "📌 Save",
        "btn_saved": "✅ Saved",
        "save_ok": "📌 Saved to memory. See the list: /saved",
        "save_dup": "This message is already saved",
        "save_limit": "You can keep up to {n} messages. Remove some via /saved",
        "save_gone": "This message can no longer be saved — the chat was cleared.",
        "saved_title": (
            "📌 <b>Saved messages</b> ({n}/{max})\n"
            "They stay in the AI's memory until you clear the history.\n\n{items}"
        ),
        "saved_empty": (
            "📌 No saved messages yet.\n\n"
            "Turn on the “Save” button in ⚙️ Settings — it will appear under the AI's answers."
        ),
        "btn_del_all": "🗑 Delete all",
        "saved_deleted": "Deleted",
        "pinned_hdr": "Messages the user saved as important context (take them into account in your answers):",
        "photo_gone": "The photo is no longer available — please send it again.",
        "photo_fail": "⚠️ Couldn't load the photo: {e}",
        "photo_caption": "Describe what is in the image.",
        "photo_caption_multi": "Describe what is in these images.",
        "album_limit": "📎 I took the first {n} photos — that's the maximum at once.",
        "voice_off": (
            "🎤 Voice messages are not supported yet: the model has no speech recognition. "
            "Please send your question as text or a photo."
        ),
        "voice_fail": "⚠️ Couldn't recognize the voice message: {e}",
        "voice_empty": "Couldn't make out any speech, please try again.",
        "voice_ok": "🎤 Recognized: {text}",
        "settings": (
            "⚙️ <b>Settings</b>\n\n"
            "🌐 <b>Language</b> — bot interface and AI replies.\n"
            "🔁 <b>Regenerate / Continue</b> — buttons under answers.\n"
            "📌 <b>Save</b> — a button under answers that pins the message in memory until you clear "
            "the history (list: /saved).\n"
            "🧠 <b>Answer style</b> — ⚡ Fast: replies right away; 🧠 Thoughtful: reasons longer, better "
            "on hard tasks.\n"
            "✏️ Your own instructions for the AI — /myprompt"
        ),
        "btn_buttons": "🔁 Regenerate/Continue: {state}",
        "btn_savebtn": "📌 “Save” button: {state}",
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
        "export_empty": "Nothing to export — the chat is empty.",
        "export_caption": "📄 Chat: {n} messages (the bot keeps only the last {m}).",
        "export_you": "You",
        "export_ai": "AI",
        "export_saved": "Saved messages",
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
        "desc": {
            "ru": "универсальный помощник для любых вопросов и разговора.",
            "en": "an all-round assistant for any question or chat.",
        },
        "prompt": None,
    },
    "translator": {
        "title": {"ru": "🌍 Переводчик", "en": "🌍 Translator"},
        "desc": {
            "ru": "пришли текст — переведу: русский → английский, остальные языки → русский.",
            "en": "send a text and I'll translate it: English → Russian, any other language → English.",
        },
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
        "desc": {
            "ru": "пишет и объясняет код, ищет ошибки, оформляет решения в блоках кода.",
            "en": "writes and explains code, finds bugs, puts solutions in code blocks.",
        },
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
        "desc": {
            "ru": "исправляет орфографию, пунктуацию и стиль, затем коротко перечисляет правки.",
            "en": "fixes spelling, punctuation and style, then briefly lists the changes.",
        },
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

# Меню команд Telegram (кнопка «/» рядом с полем ввода)
COMMANDS = {
    "ru": [
        ("start", "Запустить бота"),
        ("help", "Справка по командам"),
        ("mode", "Выбрать режим"),
        ("settings", "Настройки"),
        ("saved", "Сохранённые сообщения"),
        ("export", "Выгрузить диалог файлом"),
        ("invite", "Пригласить друзей (бонус)"),
        ("clear", "Очистить диалог"),
        ("myprompt", "Свои инструкции для ИИ"),
    ],
    "en": [
        ("start", "Start the bot"),
        ("help", "Help and commands"),
        ("mode", "Choose a mode"),
        ("settings", "Settings"),
        ("saved", "Saved messages"),
        ("export", "Download the chat as a file"),
        ("invite", "Invite friends (bonus)"),
        ("clear", "Clear the chat"),
        ("myprompt", "Your own AI instructions"),
    ],
}


def t(lang: str, key: str, **kw) -> str:
    s = TEXTS.get(lang, TEXTS["ru"]).get(key) or TEXTS["ru"][key]
    return s.format(**kw) if kw else s


def all_langs(key: str) -> set:
    """Все варианты текста кнопки на всех языках (для фильтров хендлеров)."""
    return {TEXTS[code][key] for code in TEXTS}


def modes_list(lang: str, current: str) -> str:
    """Список режимов с описаниями для сообщения «Выбери режим»."""
    lines = []
    for key, m in MODES.items():
        mark = "👉" if key == current else "•"
        lines.append(f"{mark} <b>{m['title'][lang]}</b> — {m['desc'][lang]}")
    return "\n".join(lines)
# [i18n-end]

BTN_ASK = all_langs("btn_ask")
BTN_MODE = all_langs("btn_mode")
BTN_CLEAR = all_langs("btn_clear")
BTN_INFO = all_langs("btn_info")
BTN_SETTINGS = all_langs("btn_settings")
BTN_INVITE = all_langs("btn_invite")
BTN_HELP = all_langs("btn_help")
MENU_VERSION = 3   # номер версии нижнего меню: при смене все пользователи один раз получают обновлённую клавиатуру


def make_timeout():
    """Таймаут запросов к ИИ. «read» — максимальная пауза между кусками ответа: раньше было 60 с, и
    «долго думающая» модель на сложном запросе падала с ошибкой, ничего не успев написать.
    Библиотеку для HTTP здесь напрямую не импортируем: берём класс из самого openai."""
    timeout_cls = getattr(openai, "Timeout", None)
    if timeout_cls is not None:
        try:
            return timeout_cls(connect=15.0, read=AI_TIMEOUT, write=30.0, pool=30.0)
        except Exception:
            pass
    return AI_TIMEOUT  # обычное число секунд работает везде


bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(
    api_key=AI_API_KEY,
    base_url=AI_BASE_URL,
    timeout=make_timeout(),
    max_retries=1,
)

admin_router = Router()
user_router = Router()

user_locks: dict[int, asyncio.Lock] = {}
waiting: dict[int, int] = {}                   # user_id -> сколько запросов ждут своей очереди
active_gens: dict = {}                         # user_id -> Gen текущей генерации (для кнопки «Стоп»)
last_kb_msg: dict[int, tuple[int, int]] = {}   # user_id -> (chat_id, message_id) с кнопками «Повторить/Продолжить»
last_images: dict[int, list] = {}              # user_id -> data URL фото последнего вопроса (для «Повторить»)
albums: dict[str, dict] = {}                   # media_group_id -> собираемый альбом фото
bg_tasks: set = set()
reasoning_bad: set[str] = set()                # модели, отвергшие параметр reasoning_effort
STATE = {"maintenance": False, "speedtest": False}
db: aiosqlite.Connection


def spawn(coro) -> asyncio.Task:
    """Запускает фоновую задачу и держит на неё ссылку, чтобы её не убрал сборщик мусора."""
    task = asyncio.create_task(coro)
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)
    return task


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
            custom_prompt TEXT,
            save_btn INTEGER DEFAULT 0,
            ref_by INTEGER,
            ref_paid INTEGER DEFAULT 0,
            bonus INTEGER DEFAULT 0,
            src TEXT,
            menu_ver INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id);
        CREATE TABLE IF NOT EXISTS pinned(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content TEXT,
            ts INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_pinned_user ON pinned(user_id);
        CREATE TABLE IF NOT EXISTS answers(
            user_id INTEGER,
            message_id INTEGER,
            content TEXT,
            ts INTEGER,
            PRIMARY KEY (user_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS ads_log(ts INTEGER, user_id INTEGER, ok INTEGER);
        CREATE INDEX IF NOT EXISTS idx_ads_ts ON ads_log(ts);
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
                # все, кто пришёл до появления учёта источников, — отдельная группа
                await db.execute("UPDATE users SET src='old'")
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
        "INSERT INTO users(id, username, first_name, joined, last_seen, menu_ver) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
        "first_name=excluded.first_name, last_seen=excluded.last_seen",
        (user.id, user.username, user.first_name, now, now, MENU_VERSION),
    )
    await db.commit()


async def is_banned(user_id: int) -> bool:
    return bool(await scalar("SELECT banned FROM users WHERE id=?", (user_id,)))


class Prefs(NamedTuple):
    lang: str        # язык интерфейса
    buttons: bool    # кнопки «Повторить / Продолжить» под ответом
    think: bool      # стиль «Вдумчиво»
    save: bool       # кнопка «Сохранить» под ответом


async def get_prefs(user_id: int) -> Prefs:
    cur = await db.execute(
        "SELECT lang, buttons, think, save_btn FROM users WHERE id=?", (user_id,)
    )
    row = await cur.fetchone()
    if not row:
        return Prefs("ru", False, False, False)
    lang = row["lang"] if row["lang"] in LANGS else "ru"
    return Prefs(lang, bool(row["buttons"]), bool(row["think"]), bool(row["save_btn"]))


async def get_lang(user_id: int) -> str:
    return (await get_prefs(user_id)).lang


async def set_user_field(user_id: int, field: str, value):
    if field not in ("mode", "lang", "buttons", "think", "custom_prompt", "save_btn"):
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


async def build_system_prompt(user_id: int, mode: str, lang: str) -> str:
    """Системный промпт режима + личные инструкции пользователя + его сохранённые сообщения."""
    prompt = await get_system_prompt(mode, lang)
    if mode == "default":
        custom = await get_custom_prompt(user_id)
        if custom:
            prompt += "\n\n" + t(lang, "user_instr") + "\n" + custom
    pins = await list_pinned(user_id)
    if pins:
        parts: list[str] = []
        total = 0
        for i, r in enumerate(pins, 1):
            text = r["content"]
            if total + len(text) > PINNED_TOTAL_CHARS:
                text = text[: max(0, PINNED_TOTAL_CHARS - total)]
                if not text:
                    break
            parts.append(f"[{i}] {text}")
            total += len(text)
        prompt += "\n\n" + t(lang, "pinned_hdr") + "\n" + "\n\n".join(parts)
    return prompt


# --- память диалога ---

async def get_memory_len() -> int:
    try:
        n = int(await get_setting("memory_len", str(MEMORY_DEFAULT)))
    except ValueError:
        n = MEMORY_DEFAULT
    return max(MEMORY_MIN, min(MEMORY_MAX, n))


async def get_history_chars() -> int:
    try:
        n = int(await get_setting("history_chars", str(HISTORY_CHARS)))
    except ValueError:
        n = HISTORY_CHARS
    return max(1000, n)


async def apply_memory_len(n: int):
    """Сохраняет новый размер памяти и сразу обрезает чужие истории, если память уменьшили."""
    n = max(MEMORY_MIN, min(MEMORY_MAX, n))
    await set_setting("memory_len", str(n))
    try:
        await db.execute(
            "DELETE FROM history WHERE id IN ("
            " SELECT id FROM ("
            "  SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY id DESC) AS rn FROM history"
            " ) WHERE rn > ?)",
            (n,),
        )
        await db.commit()
    except Exception:
        logging.exception("Не удалось сразу обрезать истории — это произойдёт при следующих сообщениях")


async def load_history(user_id: int, limit: int | None = None) -> list:
    if limit is None:
        limit = await get_memory_len()
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


async def count_history(user_id: int) -> int:
    return await scalar("SELECT COUNT(*) FROM history WHERE user_id=?", (user_id,))


async def pop_last_exchange(user_id: int):
    await db.execute(
        "DELETE FROM history WHERE id IN ("
        " SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT 2)",
        (user_id,),
    )
    await db.commit()


async def save_exchange(user_id: int, question: str, answer: str):
    """Запоминает И вопрос пользователя, И ответ ИИ (две записи), лишнее старое удаляет."""
    await db.executemany(
        "INSERT INTO history(user_id, role, content) VALUES(?,?,?)",
        [(user_id, "user", question), (user_id, "assistant", answer)],
    )
    await db.execute(
        "DELETE FROM history WHERE user_id=? AND id NOT IN ("
        " SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?)",
        (user_id, user_id, await get_memory_len()),
    )
    await db.execute("UPDATE users SET msgs = msgs + 1 WHERE id=?", (user_id,))
    await db.commit()


async def clear_history(user_id: int, *, pinned: bool = True, answers: bool = True):
    """Очищает диалог. pinned=False оставляет сохранённые сообщения, answers=False — кэш для кнопки «Сохранить»."""
    await db.execute("DELETE FROM history WHERE user_id=?", (user_id,))
    if answers:
        await db.execute("DELETE FROM answers WHERE user_id=?", (user_id,))
    if pinned:
        await db.execute("DELETE FROM pinned WHERE user_id=?", (user_id,))
    await db.commit()
    last_images.pop(user_id, None)


# --- сохранённые сообщения ---

def _norm(content: str) -> str:
    return content.strip()[:PINNED_ITEM_CHARS]


async def count_pinned(user_id: int) -> int:
    return await scalar("SELECT COUNT(*) FROM pinned WHERE user_id=?", (user_id,))


async def list_pinned(user_id: int) -> list:
    cur = await db.execute("SELECT id, content FROM pinned WHERE user_id=? ORDER BY id", (user_id,))
    return list(await cur.fetchall())


async def is_pinned(user_id: int, content: str) -> bool:
    return bool(
        await scalar(
            "SELECT COUNT(*) FROM pinned WHERE user_id=? AND content=?", (user_id, _norm(content))
        )
    )


async def add_pinned(user_id: int, content: str) -> str:
    """Возвращает 'ok', 'dup' (уже сохранено) или 'limit' (достигнут максимум)."""
    content = _norm(content)
    if await is_pinned(user_id, content):
        return "dup"
    if await count_pinned(user_id) >= PINNED_MAX:
        return "limit"
    await db.execute(
        "INSERT INTO pinned(user_id, content, ts) VALUES(?,?,?)", (user_id, content, int(time.time()))
    )
    await db.commit()
    return "ok"


async def remember_answer(user_id: int, message_id: int, content: str):
    """Запоминает полный текст ответа по id сообщения — нужно, чтобы кнопка «Сохранить» знала, что сохранять."""
    await db.execute(
        "INSERT OR REPLACE INTO answers(user_id, message_id, content, ts) VALUES(?,?,?,?)",
        (user_id, message_id, content, int(time.time())),
    )
    await db.execute(
        "DELETE FROM answers WHERE user_id=? AND message_id NOT IN ("
        " SELECT message_id FROM answers WHERE user_id=? ORDER BY ts DESC, message_id DESC LIMIT ?)",
        (user_id, user_id, ANSWERS_KEEP),
    )
    await db.commit()


async def get_answer(user_id: int, message_id: int) -> str | None:
    cur = await db.execute(
        "SELECT content FROM answers WHERE user_id=? AND message_id=?", (user_id, message_id)
    )
    row = await cur.fetchone()
    return row["content"] if row else None


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


async def get_bonus(user_id: int) -> int:
    return int(await scalar("SELECT COALESCE(bonus, 0) FROM users WHERE id=?", (user_id,)) or 0)


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
    if per_day > 0:
        per_day += await get_bonus(user_id)
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
    builder.add(types.KeyboardButton(text=t(lang, "btn_invite")))
    builder.add(types.KeyboardButton(text=t(lang, "btn_help")))
    builder.adjust(2, 2, 3)
    return builder.as_markup(resize_keyboard=True)


def answer_kb(lang: str, regen: bool, save_state: str | None):
    """Кнопки под ответом: «Повторить/Продолжить» (regen) и «Сохранить» (save_state: 'save' / 'saved' / None)."""
    if not regen and not save_state:
        return None
    b = InlineKeyboardBuilder()
    sizes = []
    if regen:
        b.button(text=t(lang, "btn_regen"), callback_data="re:regen")
        b.button(text=t(lang, "btn_cont"), callback_data="re:cont")
        sizes.append(2)
    if save_state:
        b.button(
            text=t(lang, "btn_saved" if save_state == "saved" else "btn_save"), callback_data="sv:add"
        )
        sizes.append(1)
    b.adjust(*sizes)
    return b.as_markup()


async def build_answer_kb(user_id: int, message_id: int, lang: str, regen: bool, save_on: bool):
    """Клавиатура уже отправленного ответа с учётом того, сохранён ли он."""
    state = None
    if save_on:
        ans = await get_answer(user_id, message_id)
        state = "saved" if ans is not None and await is_pinned(user_id, ans) else "save"
    return answer_kb(lang, regen, state)


def stop_kb(lang: str):
    b = InlineKeyboardBuilder()
    b.button(text=t(lang, "btn_stop"), callback_data="st:stop")
    return b.as_markup()


def modes_kb(current: str, lang: str):
    b = InlineKeyboardBuilder()
    for key, m in MODES.items():
        b.button(text=("✅ " if key == current else "") + m["title"][lang], callback_data=f"mode:{key}")
    b.adjust(1)
    return b.as_markup()


def settings_kb(lang: str, prefs: Prefs):
    def state(v: bool) -> str:
        return t(lang, "on" if v else "off")

    b = InlineKeyboardBuilder()
    for code, name in LANGS.items():
        b.button(text=("✅ " if code == lang else "") + name, callback_data=f"set:lang:{code}")
    b.button(text=t(lang, "btn_buttons", state=state(prefs.buttons)), callback_data="set:btns")
    b.button(text=t(lang, "btn_savebtn", state=state(prefs.save)), callback_data="set:save")
    b.button(
        text=t(lang, "btn_think", state=t(lang, "think_deep" if prefs.think else "think_fast")),
        callback_data="set:think",
    )
    b.adjust(2, 1, 1, 1)
    return b.as_markup()


def clear_kb(lang: str, has_pinned: bool):
    b = InlineKeyboardBuilder()
    if has_pinned:
        b.button(text=t(lang, "btn_clear_all"), callback_data="cl:all")
        b.button(text=t(lang, "btn_clear_keep"), callback_data="cl:keep")
        b.button(text=t(lang, "btn_cancel"), callback_data="cl:no")
        b.adjust(1)
    else:
        b.button(text=t(lang, "btn_clear_yes"), callback_data="cl:all")
        b.button(text=t(lang, "btn_cancel"), callback_data="cl:no")
        b.adjust(2)
    return b.as_markup()


# ----------------- MIDDLEWARE: регистрация, бан, техработы -----------------

async def refresh_menu(event, user):
    """Один раз присылает старым пользователям обновлённую клавиатуру (reply-клавиатуру нельзя обновить «на месте»)."""
    if hasattr(event, "data") or not hasattr(event, "chat"):
        return  # только для обычных сообщений, не для нажатий inline-кнопок
    try:
        ver = await scalar("SELECT COALESCE(menu_ver, 0) FROM users WHERE id=?", (user.id,))
        if ver >= MENU_VERSION:
            return
        await db.execute("UPDATE users SET menu_ver=? WHERE id=?", (MENU_VERSION, user.id))
        await db.commit()
        if (getattr(event, "text", None) or "").startswith("/start"):
            return  # /start и так присылает новое меню
        lang = await get_lang(user.id)
        await event.answer(t(lang, "menu_updated"), reply_markup=get_main_menu(lang))
    except Exception:
        logging.exception("Не удалось обновить меню (не критично)")


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
            await refresh_menu(event, user)
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
        self.stopped = False          # пользователь нажал «Стоп»
        self.task = None              # asyncio-задача генерации (её отменяет «Стоп»)


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


async def drop_stop_kb(chat_id: int, message_id: int, kb=None):
    """Страховка: после ответа гарантированно убираем кнопку «Стоп» (или ставим нужные кнопки ответа).
    Нужна на случай, если последняя правка из «тикера» долетела до Telegram позже финальной."""
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=kb)
    except Exception:
        pass  # «message is not modified» — кнопки уже такие, как надо


async def run_ai(
    chat_id: int,
    user_id: int,
    question: str,
    images: list | None = None,
    regen: bool = False,
):
    """Отправляет вопрос (с фото или без) модели. Запросы одного человека идут по очереди."""
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
        await _run_locked(chat_id, user_id, question, images, regen)
    finally:
        lock.release()


async def _run_locked(chat_id: int, user_id: int, question: str, images: list | None, regen: bool):
    prefs = await get_prefs(user_id)
    lang = prefs.lang

    denied = await check_limit(user_id, lang)
    if denied:
        await bot.send_message(chat_id, denied)
        return

    # под прошлым ответом убираем «Повторить/Продолжить» (кнопка «Сохранить» остаётся)
    old = last_kb_msg.pop(user_id, None)
    if old:
        try:
            kb = await build_answer_kb(user_id, old[1], lang, regen=False, save_on=prefs.save)
            await bot.edit_message_reply_markup(chat_id=old[0], message_id=old[1], reply_markup=kb)
        except Exception:
            pass

    # заглушка сразу (с кнопкой «Стоп»), всё остальное — потом
    stop_markup = stop_kb(lang)
    sent = await bot.send_message(chat_id, t(lang, "thinking"), reply_markup=stop_markup)

    # считаем попытку сразу, чтобы лимит нельзя было обойти ошибками
    await db.execute("INSERT INTO requests(ts, user_id) VALUES(?,?)", (int(time.time()), user_id))
    await db.commit()

    mode = await get_mode(user_id)
    prompt = await build_system_prompt(user_id, mode, lang)
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
                            await edit_md(chat_id, sent.message_id, shown, stop_markup)
                            last_shown = shown
                        except TelegramRetryAfter as e:
                            await asyncio.sleep(e.retry_after)
                        except Exception:
                            pass
                elif now - gen.started >= 4 and now - last_wait_edit >= 3:
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

    # --- генерация: основная модель, при сбое без единого символа — резервная ---
    attempts = [(model, effort)]
    if fallback and fallback != "off" and fallback != model and not images:
        attempts.append((fallback, None))

    holder = {"err": None}

    async def produce():
        for i, (m, eff) in enumerate(attempts):
            try:
                await asyncio.wait_for(generate(m, messages, eff, gen), TOTAL_TIMEOUT)
                holder["err"] = None
                return
            except Exception as e:
                holder["err"] = e
                logging.warning("Сбой модели %s: %s: %s", m, type(e).__name__, e)
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
        err = None  # остановил сам пользователь — это не сбой

    final = gen.text.strip()

    if not final:
        if gen.stopped:
            msg = t(lang, "stopped")
        elif err is not None:
            msg = friendly_error(lang, err)
        else:
            await log_error(user_id, "Пустой ответ от модели")
            msg = t(lang, "empty")
        try:
            await bot.edit_message_text(msg[:4096], chat_id=chat_id, message_id=sent.message_id)
        except Exception:
            await bot.send_message(chat_id, msg[:4096])
        await drop_stop_kb(chat_id, sent.message_id)
        return

    # ответ обрывался на середине / его остановили: показываем, что успели получить, и пометку
    display = final
    if gen.stopped:
        display += "\n\n" + t(lang, "stopped_note")
    elif err is not None:
        display += "\n\n" + t(lang, "cut_note")

    # --- доставка с форматированием (и кнопками, если включены в настройках) ---
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
        logging.exception("Не удалось доставить ответ")
        await log_error(user_id, "Не удалось доставить ответ в Telegram")
    # у первого сообщения ответа «Стоп» должен пропасть в любом случае
    await drop_stop_kb(chat_id, sent.message_id, kb if len(chunks) == 1 else None)

    # --- сохраняем в память ---
    if prefs.save:
        await remember_answer(user_id, last_id, final)
    if regen:
        await pop_last_exchange(user_id)
    await save_exchange(user_id, stored_question, final)
    if images:
        last_images[user_id] = images
    if not gen.stopped and err is None and not regen:
        await grant_ref_reward(user_id)
        spawn(maybe_show_ad(chat_id, user_id, lang))


# ================= ПРИГЛАШЕНИЯ И ИСТОЧНИКИ =================

_me = {"username": ""}


async def bot_username() -> str:
    if not _me["username"]:
        try:
            _me["username"] = (await bot.get_me()).username or ""
        except Exception:
            logging.warning("Не удалось узнать username бота")
    return _me["username"]


async def get_ref_cfg() -> tuple[int, int]:
    """(бонус в сутки за приглашение, макс. число друзей с бонусом) — меняется в админке."""
    try:
        bonus = max(0, int(await get_setting("ref_bonus", str(REF_BONUS))))
    except ValueError:
        bonus = REF_BONUS
    try:
        mx = max(0, int(await get_setting("ref_max", str(REF_MAX))))
    except ValueError:
        mx = REF_MAX
    return bonus, mx


async def register_start(uid: int, payload: str) -> None:
    """Запоминает, откуда пришёл НОВЫЙ пользователь: по приглашению (ref_<id>) или по метке источника."""
    cur = await db.execute("SELECT src, joined FROM users WHERE id=?", (uid,))
    row = await cur.fetchone()
    if not row or row["src"] is not None:
        return
    if row["joined"] and row["joined"] < int(time.time()) - 600:
        # давно в боте, но источник не записан — не считаем новым
        await db.execute("UPDATE users SET src='old' WHERE id=?", (uid,))
        await db.commit()
        return
    payload = (payload or "").strip()
    src, ref = "direct", None
    if payload.startswith("ref_") and payload[4:].isdigit():
        src = "ref"
        r = int(payload[4:])
        if r != uid and await scalar("SELECT COUNT(*) FROM users WHERE id=?", (r,)):
            ref = r
    elif SRC_RE.match(payload):
        src = payload.lower()
    await db.execute("UPDATE users SET src=?, ref_by=? WHERE id=?", (src, ref, uid))
    await db.commit()


async def grant_ref_reward(uid: int):
    """После ПЕРВОГО успешного ответа приглашённого оба получают бонус к суточному лимиту."""
    cur = await db.execute("SELECT ref_by, ref_paid FROM users WHERE id=?", (uid,))
    row = await cur.fetchone()
    if not row or not row["ref_by"] or row["ref_paid"]:
        return
    ref = row["ref_by"]
    bonus, mx = await get_ref_cfg()
    await db.execute("UPDATE users SET ref_paid=1, bonus=COALESCE(bonus,0)+? WHERE id=?", (bonus, uid))
    paid = await scalar("SELECT COUNT(*) FROM users WHERE ref_by=? AND ref_paid=1", (ref,))
    rewarded = paid <= mx
    if rewarded:
        await db.execute("UPDATE users SET bonus=COALESCE(bonus,0)+? WHERE id=?", (bonus, ref))
    await db.commit()
    if bonus <= 0:
        return
    try:
        await bot.send_message(uid, t(await get_lang(uid), "ref_welcome", b=bonus))
        if rewarded:
            await bot.send_message(ref, t(await get_lang(ref), "ref_notify", b=bonus))
    except Exception:
        pass  # человек мог заблокировать бота — бонус всё равно начислен


# ================= РЕКЛАМА (AdsGram) =================

ads_last = {"why": "", "ts": 0}   # почему AdsGram в последний раз не дал рекламу (видно в админке)
ads_state: dict[int, dict] = {}   # user_id -> {"n": ответов с прошлой рекламы, "last": время последнего показа}


async def ads_enabled() -> bool:
    return bool(ADSGRAM_BLOCK_ID) and (await get_setting("ads_on", "1")) == "1"


async def get_ads_every() -> int:
    try:
        return max(1, int(await get_setting("ads_every", str(ADS_EVERY))))
    except ValueError:
        return max(1, ADS_EVERY)


async def fetch_ad(user_id: int, lang: str) -> dict | None:
    """Просит у AdsGram рекламу для пользователя. Нет рекламы или сбой — None (бот работает как обычно)."""
    params = {"tgid": str(user_id), "blockid": ADSGRAM_BLOCK_ID, "language": lang}
    if ADSGRAM_TOKEN:
        params["token"] = ADSGRAM_TOKEN
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as sess:
            async with sess.get(ADS_API, params=params) as r:
                if r.status != 200:
                    body = ""
                    try:
                        body = (await r.text())[:200]
                    except Exception:
                        pass
                    ads_last.update(why=f"HTTP {r.status} {body}".strip(), ts=int(time.time()))
                    logging.warning("AdsGram: ответ %s %s", r.status, body)
                    return None
                data = await r.json(content_type=None)
    except Exception as e:
        ads_last.update(why=f"{type(e).__name__}: {e}", ts=int(time.time()))
        logging.warning("AdsGram: не удалось получить рекламу: %s: %s", type(e).__name__, e)
        return None
    if not isinstance(data, dict) or not data.get("text_html"):
        ads_last.update(why=f"пустой ответ: {str(data)[:200]}", ts=int(time.time()))
        logging.warning("AdsGram: пустой ответ: %s", str(data)[:200])
        return None
    return data


async def send_ad(chat_id: int, ad: dict):
    """Отправляет рекламу по правилам AdsGram: HTML-текст, картинка, кнопки, запрет пересылки."""
    b = InlineKeyboardBuilder()
    if ad.get("click_url") and ad.get("button_name"):
        b.button(text=str(ad["button_name"])[:64], url=ad["click_url"])
    if ad.get("reward_url") and ad.get("button_reward_name"):
        b.button(text=str(ad["button_reward_name"])[:64], url=ad["reward_url"])
    b.adjust(1)
    kb = b.as_markup() if (ad.get("click_url") or ad.get("reward_url")) else None
    text = str(ad["text_html"])
    img = ad.get("image_url")
    if img and len(text) <= 1000:
        try:
            await bot.send_photo(
                chat_id, img, caption=text, parse_mode="HTML", reply_markup=kb, protect_content=True
            )
            return
        except Exception as e:
            logging.warning("AdsGram: не вышло отправить с картинкой (%s), шлю текстом", e)
    await bot.send_message(
        chat_id, text, parse_mode="HTML", reply_markup=kb, protect_content=True,
        disable_web_page_preview=True,
    )


async def maybe_show_ad(chat_id: int, user_id: int, lang: str):
    """Вызывается после каждого успешного ответа. Реклама идёт ОТДЕЛЬНЫМ сообщением и не задерживает ответ."""
    try:
        if user_id in ADMIN_IDS or not await ads_enabled():
            return
        st = ads_state.setdefault(user_id, {"n": 0, "last": 0.0})
        st["n"] += 1
        if st["n"] < await get_ads_every() or time.time() - st["last"] < ADS_MIN_GAP:
            return
        ad = await fetch_ad(user_id, lang)
        ok = 0
        if ad:
            await send_ad(chat_id, ad)
            ok = 1
            st["n"] = 0
            st["last"] = time.time()
        else:
            st["n"] = max(0, st["n"] - 2)   # рекламы нет — попробуем снова через пару ответов
        await db.execute("INSERT INTO ads_log(ts, user_id, ok) VALUES(?,?,?)", (int(time.time()), user_id, ok))
        await db.commit()
    except Exception:
        logging.exception("AdsGram: ошибка показа рекламы (на работу бота не влияет)")


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


# --- очистка диалога (с подтверждением) ---

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


# --- режимы ---

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
    # прошлый диалог сбрасываем, чтобы он не мешал новому режиму; сохранённые сообщения остаются
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


# --- настройки ---

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
            # у reply-клавиатуры нельзя поменять текст кнопок — присылаем новую
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


# --- инфо ---

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


# --- сохранённые сообщения ---

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


# --- «Повторить» / «Продолжить» ---

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


# --- «Стоп» ---

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


# --- экспорт диалога ---

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


# --- фото и альбомы ---

async def process_photos(chat_id: int, uid: int, photos: list, caption: str, over: int = 0):
    """Скачивает фото и отправляет их ИИ одним запросом."""
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
    """Ждёт, пока доедут все фото альбома, и обрабатывает их одним запросом."""
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

    # Telegram присылает каждое фото альбома отдельным сообщением — собираем их вместе
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


# Админский роутер подключаем первым, чтобы он перехватывал свои команды и ввод
dp.include_router(admin_router)
dp.include_router(user_router)


async def setup_commands():
    """Меню команд Telegram (кнопка «/»): на русском для русского клиента, иначе на английском."""
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS["en"]])
        for code, items in COMMANDS.items():
            await bot.set_my_commands(
                [BotCommand(command=c, description=d) for c, d in items], language_code=code
            )
    except Exception:
        logging.exception("Не удалось задать меню команд (это не критично)")


async def main():
    await init_db()
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS не задан — админ-панель недоступна. Узнай ID командой /myid.")
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_commands()
    logging.info("🚀 Бот запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
