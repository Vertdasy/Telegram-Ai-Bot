import os
import asyncio
import logging
import time

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)

# ----------------- НАСТРОЙКИ -----------------
# На Amvera лучше задать их в переменных окружения, значения ниже — запасные.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8333666916:AAFt73Hx_FIoc6Nkmqm-PK2umipQBtrWIpc")
AI_API_KEY = os.getenv("AI_API_KEY", "sk-star-d96f2d0cf9336d89f79fd495f85f0e0bd292bca2324d180ad5d686272b84d40d")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://ai.starimg.ru/v1")
MODEL = os.getenv("ID_MODEL", "deepseek-v4.1-flash")

MAX_HISTORY_LEN = 10      # сколько последних сообщений помнить
EDIT_INTERVAL = 0.7       # как часто обновлять сообщение при стриминге (сек)
TG_LIMIT = 4000           # лимит Telegram на длину сообщения

SYSTEM_PROMPT = {
    "role": "system",
    "content": "Ты полезный и вежливый AI-ассистент. Отвечай строго на русском языке, независимо от языка обращения.",
}

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)

user_history: dict[int, list] = {}
user_locks: dict[int, asyncio.Lock] = {}


def get_main_menu():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🤖 Задать вопрос ИИ"))
    builder.add(types.KeyboardButton(text="📝 Очистить диалог"))
    builder.add(types.KeyboardButton(text="ℹ Инфо"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def reset_history(user_id: int):
    user_history[user_id] = [SYSTEM_PROMPT]


def trim_history(user_id: int):
    h = user_history[user_id]
    if len(h) > MAX_HISTORY_LEN + 1:
        user_history[user_id] = [h[0]] + h[-MAX_HISTORY_LEN:]


def clean(text: str) -> str:
    return text.replace("**", "").strip()


# ----------------- КОМАНДЫ И КНОПКИ -----------------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    reset_history(message.from_user.id)
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я бот на базе <b>{MODEL}</b> 🚀\n\n"
        "Я помню историю диалога и отвечаю в реальном времени.\n"
        "Используй меню или просто пиши мне текст!",
        parse_mode="HTML",
        reply_markup=get_main_menu(),
    )


@dp.message(Command("clear", "reset"))
@dp.message(F.text == "📝 Очистить диалог")
async def cmd_clear(message: types.Message):
    reset_history(message.from_user.id)
    await message.answer("🧹 История диалога очищена! Начнём с чистого листа.")


@dp.message(F.text == "ℹ Инфо")
async def cmd_info(message: types.Message):
    await message.answer(
        "ℹ <b>Информация о боте:</b>\n"
        f"• <b>Модель:</b> {MODEL}\n"
        "• <b>Язык:</b> Русский\n"
        f"• <b>Память:</b> последние {MAX_HISTORY_LEN} сообщений",
        parse_mode="HTML",
    )


@dp.message(F.text == "🤖 Задать вопрос ИИ")
async def cmd_ask(message: types.Message):
    await message.answer("💬 Задавай любой вопрос, я готов!")


# ----------------- ОБРАБОТКА ТЕКСТА -----------------

@dp.message(F.text)
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    lock = user_locks.setdefault(user_id, asyncio.Lock())

    async with lock:  # не даём одному пользователю запускать запросы параллельно
        if user_id not in user_history:
            reset_history(user_id)

        user_history[user_id].append({"role": "user", "content": message.text})
        trim_history(user_id)

        await bot.send_chat_action(message.chat.id, "typing")
        sent = await message.answer("🤔 Запрос обрабатывается...")

        try:
            stream = await client.chat.completions.create(
                model=MODEL,
                messages=user_history[user_id],
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
                    shown = clean(ai_text)[:TG_LIMIT]
                    if shown and shown != last_shown:
                        try:
                            await bot.edit_message_text(
                                shown, chat_id=message.chat.id, message_id=sent.message_id
                            )
                            last_shown = shown
                        except Exception:
                            pass
                        last_edit = now

            final = clean(ai_text) or "Извините, не удалось сформировать ответ. Попробуйте ещё раз!"

            if final[:TG_LIMIT] != last_shown:
                try:
                    await bot.edit_message_text(
                        final[:TG_LIMIT], chat_id=message.chat.id, message_id=sent.message_id
                    )
                except Exception:
                    pass

            for i in range(TG_LIMIT, len(final), TG_LIMIT):
                await message.answer(final[i:i + TG_LIMIT])

            user_history[user_id].append({"role": "assistant", "content": final})
            trim_history(user_id)

        except Exception as e:
            logging.exception("Ошибка при обращении к ИИ:")
            # убираем вопрос из истории, чтобы он не застрял без ответа
            if user_history[user_id] and user_history[user_id][-1]["role"] == "user":
                user_history[user_id].pop()
            try:
                await bot.edit_message_text(
                    f"⚠️ Произошла ошибка при обращении к ИИ: {e}",
                    chat_id=message.chat.id,
                    message_id=sent.message_id,
                )
            except Exception:
                await message.answer(f"⚠️ Произошла ошибка при обращении к ИИ: {e}")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
