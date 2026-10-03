import os
import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from openai import OpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)

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
    user_history[message.from_user.id] = [SYSTEM_PROMPT]

    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я бот на базе **{ID_MODEL}**! 🚀\n"
        "Создатель: @ashoton\n\n"
        "Я запоминаю историю диалога и отвечаю на лету в режиме реального времени.\n\n"
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
            f"• **Модель:** {ID_MODEL}\n"
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

    # Создаем «заглушку» сообщения, которую будем редактировать на лету
    sent_message = await message.answer("🤔 Запрос обрабатывается...")

    try:
        logging.info(f"Отправка стриминг-запроса в OpenRouter {ID_MODEL} для {user_id}...")

        # Функция для синхронного получения потока от OpenRouter
        def get_stream():
            return client.chat.completions.create(
                model=ID_MODEL,
                messages=user_history[user_id],
                stream=True,  # Включаем стриминг!
                timeout=30.0
            )

        loop = asyncio.get_event_loop()
        stream = await loop.run_in_executor(None, get_stream)

        ai_text = ""
        last_updated_text = ""
        last_update_time = time.time()
        
        # Читаем токены по мере их генерации моделью
        for chunk in stream:
            token = chunk.choices[0].delta.content
            if token:
                ai_text += token
                
                # Защита от лимитов Telegram: обновляем сообщение не чаще раза в 0.6 секунд
                # И только если текст реально изменился
                current_time = time.time()
                if current_time - last_update_time > 0.6 and ai_text.strip() != last_updated_text:
                    # Чистим текст от системных артефактов OpenRouter на лету
                    display_text = ai_text.replace("User Safety: safe", "").replace("Response Safety: safe", "").strip()
                    
                    if display_text:
                        try:
                            await bot.edit_message_text(
                                text=display_text,
                                chat_id=message.chat.id,
                                message_id=sent_message.message_id
                            )
                            last_updated_text = ai_text.strip()
                            last_update_time = current_time
                        except Exception:
                            # Пропускаем ошибки, если Telegram не успел обработать или текст совпал
                            pass

        # Финальная очистка и форматирование полученного текста
        clean_text = ai_text.replace("User Safety: safe", "").replace("Response Safety: safe", "").replace("**", "").strip()

        if not clean_text:
            clean_text = "Извините, не удалось сформировать ответ. Попробуйте ещё раз!"

        # Делаем финальное обновление сообщения, чтобы вывести весь текст до конца
        try:
            await bot.edit_message_text(
                text=clean_text[:4000],  # Лимит одного сообщения в TG
                chat_id=message.chat.id,
                message_id=sent_message.message_id
            )
        except Exception:
            # Если финальный текст совпал с последним чанком, TG выкинет ошибку "message is not modified", игнорируем её
            pass

        # Если ответ оказался гигантским (больше 4000 символов), досылаем остатки обычными сообщениями
        if len(clean_text) > 4000:
            for i in range(4000, len(clean_text), 4000):
                await message.answer(clean_text[i:i + 4000])

        # Записываем полный ответ в историю контекста
        user_history[user_id].append({"role": "assistant", "content": clean_text})
        logging.info(f"Успешно получен стрим-ответ для {user_id}")

    except Exception as e:
        logging.exception("Ошибка при обращении к OpenRouter API:")
        try:
            await bot.edit_message_text(
                text=f"⚠️ Произошла ошибка при обращении к ИИ: {e}",
                chat_id=message.chat.id,
                message_id=sent_message.message_id
            )
        except Exception:
            await message.answer(f"⚠️ Произошла ошибка при обращении к ИИ: {e}")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Стрим-бот от @ashoton успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
