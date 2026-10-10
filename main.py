import telebot
from openai import OpenAI

# ----------------- НАСТРОЙКИ -----------------
TELEGRAM_BOT_TOKEN = "8333666916:AAFt73Hx_FIoc6Nkmqm-PK2umipQBtrWIpc"
AI_API_KEY = "sk-star-d96f2d0cf9336d89f79fd495f85f0e0bd292bca2324d180ad5d686272b84d40d"[cite: 1]

# Инициализация бота
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Инициализация клиента ИИ с учетом твоего провайдера
ai_client = OpenAI(
    api_key=AI_API_KEY,
    base_url="https://ai.starimg.ru/v1"  # Точный Base URL со скриншота
)

# Хранилище истории диалогов в памяти
user_histories = {}
MAX_HISTORY_LEN = 10  # Максимальное количество сообщений в контексте


def get_ai_response(user_id: int, text: str) -> str:
    """Формирует запрос к ИИ с учётом истории контекста."""
    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты полезный и вежливый AI-ассистент."}
        ]

    # Добавляем сообщение пользователя в историю
    user_histories[user_id].append({"role": "user", "content": text})

    # Ограничиваем историю, чтобы не переполнять контекст
    if len(user_histories[user_id]) > MAX_HISTORY_LEN + 1:
        user_histories[user_id] = [user_histories[user_id][0]] + user_histories[user_id][-MAX_HISTORY_LEN:]

    try:
        response = ai_client.chat.completions.create(
            model="deepseek-v4.1-flash",  # Выставили модель из твоего личного кабинета
            messages=user_histories[user_id],
            temperature=0.7
        )
        ai_message = response.choices[0].message.content
        
        # Сохраняем ответ ИИ в историю
        user_histories[user_id].append({"role": "assistant", "content": ai_message})
        return ai_message

    except Exception as e:
        print(f"Ошибка API: {e}")
        return f"Произошла ошибка при обращении к ИИ: {e}"


# ----------------- ХЭНДЛЕРЫ КОМАНД -----------------

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        "Привет! Я твой бот на базе deepseek-v4.1-flash. Задай мне любой вопрос!"
    )


@bot.message_handler(commands=['clear', 'reset'])
def clear_history(message):
    """Сброс контекста диалога."""
    user_id = message.from_user.id
    user_histories[user_id] = [
        {"role": "system", "content": "Ты полезный и вежливый AI-ассистент."}
    ]
    bot.reply_to(message, "История диалога очищена! Начнем с чистого листа.")


# ----------------- ОБРАБОТКА ТЕКСТА -----------------

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_message(message):
    user_id = message.from_user.id
    
    # Показываем статус "печатает..."
    bot.send_chat_action(message.chat.id, 'typing')

    # Получаем ответ от ИИ
    ai_reply = get_ai_response(user_id, message.text)
    
    # Отправляем ответ
    bot.reply_to(message, ai_reply)


if __name__ == '__main__':
    print("Бот запущен и ждет сообщения...")
    bot.infinity_polling()