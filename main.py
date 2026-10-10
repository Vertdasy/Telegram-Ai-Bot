import os
import re
import asyncio
import base64
import logging
import time
import html

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware, types
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)


# ----------------- НАСТРОЙКИ (всё через переменные окружения) -----------------
def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
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

MAX_HISTORY_LEN = 10      # сколько последних сообщений помнить
EDIT_INTERVAL = 0.7       # как часто обновлять сообщение при стриминге (сек)
CHUNK = 3500              # размер куска текста для одного сообщения Telegram
PHOTO_TAG = "[Фото]"      # пометка фото-вопроса в истории
CONTINUE_TEXT = "Продолжи свой предыдущий ответ с того места, где он оборвался. Не повторяй уже написанное."

DEFAULT_PROMPT = (
    "Ты полезный и вежливый AI-ассистент. "
    "Отвечай строго на русском языке, независимо от языка обращения."
)

# Режимы / персонажи: ключ -> (название, системный промпт; None = промпт по умолчанию из админки)
MODES = {
    "default": ("🤖 Обычный", None),
    "translator": (
        "🌍 Переводчик",
        "Ты профессиональный переводчик. Если текст на русском — переводи его на английский, "
        "во всех остальных случаях переводи на русский. Сохраняй смысл, тон и форматирование. "
        "Выводи только перевод без пояснений, если не просят иное.",
    ),
    "programmer": (
        "💻 Программист",
        "Ты опытный программист и наставник. Пиши чистый, рабочий код и оформляй его в блоках кода "
        "с указанием языка. Кратко объясняй решение, указывай на возможные ошибки и крайние случаи. "
        "Отвечай на русском языке.",
    ),
    "editor": (
        "✍️ Редактор",
        "Ты внимательный редактор и корректор. Исправляй орфографию, пунктуацию и стиль присланного "
        "текста, сохраняя авторский смысл и голос. Сначала дай исправленный текст, затем коротко "
        "перечисли главные правки. Отвечай на русском языке.",
    ),
}

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)

admin_router = Router()
user_router = Router()

user_locks: dict[int, asyncio.Lock] = {}
last_kb_msg: dict[int, tuple[int, int]] = {}   # user_id -> (chat_id, message_id) с кнопками под ответом
last_image: dict[int, str] = {}                # user_id -> data URL последнего фото (для «Повторить»)
bg_tasks: set = set()
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
            mode TEXT DEFAULT 'default'
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
    # миграция для баз, созданных прошлой версией бота
    cur = await db.execute("PRAGMA table_info(users)")
    if "mode" not in [r["name"] for r in await cur.fetchall()]:
        await db.execute("ALTER TABLE users ADD COLUMN mode TEXT DEFAULT 'default'")
    await db.commit()
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


async def get_mode(user_id: int) -> str:
    mode = await scalar("SELECT mode FROM users WHERE id=?", (user_id,))
    return mode if mode in MODES else "default"


async def set_mode(user_id: int, mode: str):
    await db.execute("UPDATE users SET mode=? WHERE id=?", (mode, user_id))
    await db.commit()


async def load_history(user_id: int, limit: int = MAX_HISTORY_LEN) -> list:
    cur = await db.execute(
        "SELECT role, content FROM ("
        " SELECT id, role, content FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?"
        ") ORDER BY id",
        (user_id, limit),
    )
    return [{"role": r["role"], "content": r["content"]} for r in await cur.fetchall()]


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


def fmt_wait(sec: int) -> str:
    sec = max(sec, 1)
    if sec < 60:
        return f"{sec} сек."
    if sec < 3600:
        return f"{sec // 60 + 1} мин."
    return f"{sec // 3600} ч {sec % 3600 // 60} мин."


async def check_limit(user_id: int) -> str | None:
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
            return f"⏳ Слишком часто: не больше {per_min} запросов в минуту. Подожди {fmt_wait(oldest + 60 - now)}"
    if per_day > 0:
        cnt = await scalar(
            "SELECT COUNT(*) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 86400)
        )
        if cnt >= per_day:
            oldest = await scalar(
                "SELECT MIN(ts) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 86400)
            )
            return f"📅 Дневной лимит ({per_day} запросов) исчерпан. Новый запрос будет доступен через {fmt_wait(oldest + 86400 - now)}"
    return None


# ----------------- КЛАВИАТУРЫ -----------------

def get_main_menu():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🤖 Задать вопрос ИИ"))
    builder.add(types.KeyboardButton(text="🎭 Режим"))
    builder.add(types.KeyboardButton(text="📝 Очистить диалог"))
    builder.add(types.KeyboardButton(text="ℹ Инфо"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def answer_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Повторить", callback_data="re:regen")
    b.button(text="➡️ Продолжить", callback_data="re:cont")
    b.adjust(2)
    return b.as_markup()


def modes_kb(current: str):
    b = InlineKeyboardBuilder()
    for key, (title, _) in MODES.items():
        b.button(text=("✅ " if key == current else "") + title, callback_data=f"mode:{key}")
    b.adjust(1)
    return b.as_markup()


# ----------------- MIDDLEWARE: регистрация и бан -----------------

class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and not user.is_bot:
            await touch_user(user)
            if user.id not in ADMIN_IDS and await is_banned(user.id):
                await event.answer("🚫 Доступ к боту ограничен.")
                return
        return await handler(event, data)


dp.message.outer_middleware(UserMiddleware())
dp.callback_query.outer_middleware(UserMiddleware())


# ================= ЯДРО: запрос к ИИ =================

async def run_ai(
    chat_id: int,
    user_id: int,
    question: str,
    image_url: str | None = None,
    regen: bool = False,
):
    lock = user_locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        await bot.send_message(chat_id, "⏳ Подожди, я ещё отвечаю на предыдущий запрос.")
        return

    async with lock:
        denied = await check_limit(user_id)
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

        # считаем попытку сразу, чтобы лимит нельзя было обойти ошибками
        await db.execute("INSERT INTO requests(ts, user_id) VALUES(?,?)", (int(time.time()), user_id))
        await db.commit()

        mode = await get_mode(user_id)
        prompt = MODES[mode][1] or await get_setting("system_prompt", DEFAULT_PROMPT)
        model = VISION_MODEL if image_url else await get_setting("model", MODEL)

        history = await load_history(user_id, MAX_HISTORY_LEN + (2 if regen else 0))
        if regen:
            history = history[:-2]

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

        await bot.send_chat_action(chat_id, "typing")
        sent = await bot.send_message(chat_id, "🤔 Запрос обрабатывается...")

        # --- генерация ---
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                stream=True,
                timeout=60.0,
            )

            ai_text = ""
            last_shown = ""
            last_edit = time.time()

            async for chunk in stream:
                if not chunk.choices:
                    continue
                token = chunk.choices[0].delta.content
                if not token:
                    continue
                ai_text += token

                now = time.time()
                if now - last_edit > EDIT_INTERVAL:
                    shown = split_markdown(ai_text.strip())[0]
                    if shown and shown != last_shown:
                        try:
                            await edit_md(chat_id, sent.message_id, shown)
                            last_shown = shown
                        except Exception:
                            pass
                        last_edit = now

        except Exception as e:
            logging.exception("Ошибка при обращении к ИИ:")
            await log_error(user_id, f"{type(e).__name__}: {e}")
            err = f"⚠️ Произошла ошибка при обращении к ИИ: {e}"
            try:
                await bot.edit_message_text(err[:4096], chat_id=chat_id, message_id=sent.message_id)
            except Exception:
                await bot.send_message(chat_id, err[:4096])
            return

        final = ai_text.strip() or "Извините, не удалось сформировать ответ. Попробуйте ещё раз!"

        # --- доставка с форматированием и кнопками ---
        try:
            chunks = split_markdown(final)
            kb = answer_kb()
            await with_retry(
                edit_md, chat_id, sent.message_id, chunks[0], kb if len(chunks) == 1 else None
            )
            last_id = sent.message_id
            for i, part in enumerate(chunks[1:], start=1):
                msg = await with_retry(send_md, chat_id, part, kb if i == len(chunks) - 1 else None)
                last_id = msg.message_id
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


# ================= АДМИН-ПАНЕЛЬ =================

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
    b.button(text="✏️ Изменить", callback_data="adm:prompt_edit")
    b.button(text="♻️ Сбросить", callback_data="adm:prompt_reset")
    b.button(text="⬅️ Назад", callback_data="adm:menu")
    b.adjust(2, 1)
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


def fmt_user(r) -> str:
    name = html.escape(r["first_name"] or "—")
    uname = f" @{html.escape(r['username'])}" if r["username"] else ""
    return f"{name}{uname}"


def fmt_limit(n: int) -> str:
    return str(n) if n > 0 else "без лимита"


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
            f"👥 Пользователей: <b>{total}</b> (забанено: {banned})\n"
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
            f"<code>{time.strftime('%d.%m %H:%M', time.localtime(r['ts']))}</code> "
            f"({r['user_id']}): {html.escape(r['text'][:200])}"
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
        current = await get_setting("system_prompt", DEFAULT_PROMPT)
        await show(
            cb,
            "📝 <b>Системный промпт</b> (режим «Обычный»):\n\n"
            f"<code>{html.escape(current[:3000])}</code>",
            prompt_kb(),
        )

    elif action == "prompt_edit":
        await state.set_state(Admin.waiting)
        await state.update_data(action="prompt")
        await show(cb, "Пришли новый системный промпт.\n/cancel — отмена")

    elif action == "prompt_reset":
        await del_setting("system_prompt")
        await show(cb, "♻️ Промпт сброшен на стандартный.", prompt_kb())

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

    elif action == "model":
        await set_setting("model", text)
        await state.clear()
        await message.answer(
            f"✅ Модель изменена: <code>{html.escape(text)}</code>",
            parse_mode="HTML",
            reply_markup=admin_kb(),
        )

    elif action == "prompt":
        await set_setting("system_prompt", text)
        await state.clear()
        await message.answer("✅ Системный промпт обновлён.", reply_markup=admin_kb())

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
    await message.answer(f"Твой Telegram ID: {message.from_user.id}")


@user_router.message(Command("admin"))
async def cmd_admin_denied(message: types.Message):
    if ADMIN_IDS:
        await message.answer("⛔ Нет доступа.")
    else:
        await message.answer(
            "Админ не настроен. Задай переменную окружения ADMIN_IDS "
            f"со своим ID (твой ID: {message.from_user.id}) и перезапусти бота."
        )


@user_router.message(Command("start"))
async def cmd_start(message: types.Message):
    model = await get_setting("model", MODEL)
    await message.answer(
        f"👋 Привет, {html.escape(message.from_user.first_name or 'друг')}!\n\n"
        f"Я бот на базе <b>{html.escape(model)}</b> 🚀\n\n"
        "Я помню историю диалога, умею разные режимы (🎭 Режим) и понимаю фото.\n"
        "Используй меню или просто пиши мне текст!",
        parse_mode="HTML",
        reply_markup=get_main_menu(),
    )


@user_router.message(Command("clear", "reset"))
@user_router.message(F.text == "📝 Очистить диалог")
async def cmd_clear(message: types.Message):
    await clear_history(message.from_user.id)
    await message.answer("🧹 История диалога очищена! Начнём с чистого листа.")


@user_router.message(Command("mode"))
@user_router.message(F.text == "🎭 Режим")
async def cmd_mode(message: types.Message):
    current = await get_mode(message.from_user.id)
    await message.answer("🎭 Выбери режим работы:", reply_markup=modes_kb(current))


@user_router.callback_query(F.data.startswith("mode:"))
async def cb_mode(cb: types.CallbackQuery):
    key = cb.data.split(":", 1)[1]
    if key not in MODES:
        await cb.answer()
        return
    await set_mode(cb.from_user.id, key)
    await clear_history(cb.from_user.id)  # чтобы контекст прошлого режима не мешал
    try:
        await cb.message.edit_text(
            f"✅ Режим: {MODES[key][0]}\nДиалог очищен, можно писать.",
            reply_markup=modes_kb(key),
        )
    except TelegramBadRequest:
        pass
    await cb.answer()


@user_router.message(F.text == "ℹ Инфо")
async def cmd_info(message: types.Message):
    model = await get_setting("model", MODEL)
    mode = await get_mode(message.from_user.id)
    per_min, per_day = await get_limits()
    await message.answer(
        "ℹ <b>Информация о боте:</b>\n"
        f"• <b>Модель:</b> {html.escape(model)}\n"
        f"• <b>Режим:</b> {MODES[mode][0]}\n"
        f"• <b>Память:</b> последние {MAX_HISTORY_LEN} сообщений\n"
        f"• <b>Лимиты:</b> {fmt_limit(per_min)}/мин, {fmt_limit(per_day)}/сутки\n"
        "• <b>Фото:</b> поддерживаются",
        parse_mode="HTML",
    )


@user_router.message(F.text == "🤖 Задать вопрос ИИ")
async def cmd_ask(message: types.Message):
    await message.answer("💬 Задавай любой вопрос или пришли фото, я готов!")


@user_router.callback_query(F.data.in_({"re:regen", "re:cont"}))
async def cb_retry(cb: types.CallbackQuery):
    uid = cb.from_user.id
    hist = await load_history(uid)
    if len(hist) < 2 or hist[-1]["role"] != "assistant" or hist[-2]["role"] != "user":
        await cb.answer("Нечего повторять", show_alert=True)
        return

    if cb.data == "re:regen":
        q = hist[-2]["content"]
        image = None
        if q.startswith(PHOTO_TAG):
            image = last_image.get(uid)
            if not image:
                await cb.answer("Фото больше недоступно — отправь его заново.", show_alert=True)
                return
            q = q[len(PHOTO_TAG):].strip()
        await cb.answer()
        await run_ai(cb.message.chat.id, uid, q, image_url=image, regen=True)
    else:
        await cb.answer()
        await run_ai(cb.message.chat.id, uid, CONTINUE_TEXT)


@user_router.message(F.photo)
async def handle_photo(message: types.Message):
    try:
        buf = await bot.download(message.photo[-1])
        data = buf.read()
    except Exception as e:
        await message.answer(f"⚠️ Не удалось загрузить фото: {e}")
        return
    url = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    caption = (message.caption or "").strip() or "Опиши, что на изображении."
    await run_ai(message.chat.id, message.from_user.id, caption, image_url=url)


@user_router.message(F.voice)
async def handle_voice(message: types.Message):
    if not STT_MODEL:
        await message.answer(
            "🎤 Голосовые пока не поддерживаются: у используемой модели нет распознавания речи. "
            "Отправь вопрос текстом или фото."
        )
        return
    uid = message.from_user.id
    try:
        buf = await bot.download(message.voice)
        tr = await client.audio.transcriptions.create(
            model=STT_MODEL, file=("voice.ogg", buf.read(), "audio/ogg")
        )
        text = (tr.text or "").strip()
    except Exception as e:
        logging.exception("Ошибка распознавания голоса:")
        await log_error(uid, f"STT {type(e).__name__}: {e}")
        await message.answer(f"⚠️ Не удалось распознать голосовое: {e}")
        return
    if not text:
        await message.answer("Не удалось разобрать речь, попробуй ещё раз.")
        return
    await message.answer(f"🎤 Распознано: {text[:3500]}")
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
