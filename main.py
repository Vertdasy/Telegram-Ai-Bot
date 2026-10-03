import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from openai import OpenAI

# Получаем токены из переменных окружения Render
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ID_MODEL = os.getenv("ID_MODEL")

# Проверка, что ключи заданы
if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Ошибка: TELEGRAM_TOKEN или OPENROUTER_API_KEY не заданы в переменных окружения!")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = OpenAI(
    base_url="https://openrouter.ai",
    api_key=OPENROUTER_API_KEY,
    default_headers={
        "HTTP-Referer": "https://t.me",
        "X-Title": "Telegram AI Bot by @ashoton"
    }
)

user_history = {}

# Системная инструкция для русского языка
SYSTEM_PROMPT = {
    "role": "system",
    "content": "Отвечай всегда строго на русском языке, независимо от языка обращения."
}


def get_main_menu():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🤖 Задать вопрос ИИ"))
    builder.add(types.KeyboardButton(text="📝 Очистить диалог"))
    builder.add(types.KeyboardButton(text="ℹ Инфо"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    # При старте задаем системный промпт
    user_history[message.from_user.id] = [SYSTEM_PROMPT]

    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я бот на базе **nemotron-3.5-lightning**! 🚀\n"
        "Создатель: @ashoton\n\n"
        "Я запоминаю историю диалога и отвечаю всегда на русском языке.\n\n"
        "Используй меню или просто пиши мне текст!",
        parse_mode="Markdown",
        reply_markup=get_main_menu()
    )


@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id

    if user_id not in user_history or not user_history[user_id]:
        user_history[user_id] = [SYSTEM_PROMPT]

    if message.text == "ℹ Инфо":
        await message.answer(
            "ℹ **Информация о боте:**\n"
            "• **Создатель:** @ashoton\n"
            "• **Модель:**" + ID_MODEL + "\n"
            "• **Язык:** Русский\n"
            "• **Память:** Сохраняет историю сообщений",
            parse_mode="Markdown"
        )
        return

    elif message.text == "📝 Очистить диалог":
        user_history[user_id] = [SYSTEM_PROMPT]
        await message.answer("🧹 История общения сброшена! ИИ готов к новой теме.")
        return

    elif message.text == "🤖 Задать вопрос ИИ":
        await message.answer("💬 Задавай любой вопрос на русском языке, я готов!")
        return

    if not message.text:
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    user_history[user_id].append({"role": "user", "content": message.text})

    try:
        logging.info(f"Отправка запроса в OpenRouter "+ ID_MODEL + " для {user_id}...")

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=ID_MODEL,
                messages=user_history[user_id],
                timeout=30.0
            )
        )

        ai_text = response.choices.message.content

        if not ai_text:
            ai_text = "Извините, не удалось сформировать ответ. Попробуйте ещё раз!"

        clean_text = ai_text.replace("User Safety: safe", "").replace("Response Safety: safe", "").replace("**",
                                                                                                           "").strip()

        user_history[user_id].append({"role": "assistant", "content": clean_text})
        logging.info(f"Успешно получен ответ для {user_id}")

        if len(clean_text) > 4000:
            for i in range(0, len(clean_text), 4000):
                await message.answer(clean_text[i:i + 4000])
        else:
            await message.answer(clean_text)

    except Exception as e:
        logging.exception("Ошибка при обращении к OpenRouter API:")
        await message.answer(f"⚠️ Произошла ошибка при обращении к ИИ: {e}")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот от @ashoton успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
