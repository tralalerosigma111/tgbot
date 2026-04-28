import asyncio
import aiohttp
import os
import base64
import json
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, LabeledPrice, PreCheckoutQuery
from aiogram.filters import CommandStart, Command
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("MODEL")

# ====== ФАЙЛ С ПОДПИСКАМИ ======
SUB_FILE = "subs.json"

def load_subs():
    if not os.path.exists(SUB_FILE):
        return {}
    with open(SUB_FILE, "r") as f:
        return json.load(f)

def save_subs(data):
    with open(SUB_FILE, "w") as f:
        json.dump(data, f)

subscriptions = load_subs()

# память
user_memory = {}

# лимиты
user_limits = {}
DAILY_LIMIT = 5


def is_subscribed(user_id):
    user_id = str(user_id)
    if user_id not in subscriptions:
        return False

    expire_date = datetime.fromisoformat(subscriptions[user_id])
    return datetime.now() < expire_date


def add_subscription(user_id):
    expire = datetime.now() + timedelta(days=30)
    subscriptions[str(user_id)] = expire.isoformat()
    save_subs(subscriptions)


def check_limit(user_id):
    if is_subscribed(user_id):
        return True

    today = datetime.now().strftime("%Y-%m-%d")

    if user_id not in user_limits:
        user_limits[user_id] = {"date": today, "count": 0}

    if user_limits[user_id]["date"] != today:
        user_limits[user_id] = {"date": today, "count": 0}

    if user_limits[user_id]["count"] >= DAILY_LIMIT:
        return False

    user_limits[user_id]["count"] += 1
    return True


async def send_long_message(message: Message, text: str):
    for i in range(0, len(text), 4000):
        await message.answer(text[i:i+4000])


# ===== TEXT =====
async def ask_deepseek(user_id, user_message):
    url = "https://openrouter.ai/api/v1/chat/completions"

    history = user_memory.get(user_id, [])

    messages = [{"role": "system", "content": "Ты помогаешь с учебой"}]
    messages += history
    messages.append({"role": "user", "content": user_message})

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 500
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            result = await resp.json()

    try:
        reply = result["choices"][0]["message"]["content"]

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})

        user_memory[user_id] = history[-10:]

        return reply
    except:
        return "Ошибка AI 😢"


# ===== IMAGE =====
async def ask_gemini(image_bytes):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    data = {
        "contents": [
            {
                "parts": [
                    {"text": "Реши тест с картинки и объясни"},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": image_base64
                        }
                    }
                ]
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data) as resp:
            result = await resp.json()

    try:
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except:
        return "Ошибка фото 😢"


# ===== MAIN =====
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    print("🤖 Подключаюсь к Telegram...")
    me = await bot.get_me()
    print(f"✅ Бот @{me.username} запущен")
    print("🚀 Бот работает!")

    @dp.message(CommandStart())
    async def start_handler(message: Message):
        await message.answer(
            "Привет! 🤖\n"
            "5 бесплатных запросов\n"
            "📷 Фото — решу\n"
            "💬 Текст — отвечу\n\n"
            "💎 /buy — подписка (30 дней)"
        )

    # 💰 купить
    @dp.message(Command("buy"))
    async def buy(message: Message):
        prices = [LabeledPrice(label="Подписка 30 дней", amount=100)]

        await bot.send_invoice(
            chat_id=message.chat.id,
            title="Подписка",
            description="30 дней без лимитов",
            payload="sub_30_days",
            currency="XTR",
            prices=prices
        )

    @dp.pre_checkout_query()
    async def pre_checkout(pre_checkout_q: PreCheckoutQuery):
        await pre_checkout_q.answer(ok=True)

    @dp.message(F.successful_payment)
    async def success_payment(message: Message):
        user_id = message.from_user.id
        add_subscription(user_id)

        await message.answer("💎 Подписка активирована на 30 дней!")

    # 🧠 новый чат
    @dp.message(Command("chat"))
    async def new_chat(message: Message):
        user_memory[message.from_user.id] = []
        await message.answer("🆕 Новый чат создан")

    # -------- TEXT --------
    @dp.message(lambda message: message.text)
    async def text_handler(message: Message):
        user_id = message.from_user.id

        if not check_limit(user_id):
            await message.answer("❌ Лимит закончился\n💎 /buy")
            return

        reply = await ask_deepseek(user_id, message.text)
        await send_long_message(message, reply)

    # -------- PHOTO --------
    @dp.message(lambda message: message.photo)
    async def photo_handler(message: Message):
        user_id = message.from_user.id

        if not check_limit(user_id):
            await message.answer("❌ Лимит закончился\n💎 /buy")
            return

        thinking = await message.answer("Смотрю фото... 👀")

        try:
            photo = message.photo[-1]
            file = await bot.get_file(photo.file_id)

            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"

            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    image_bytes = await resp.read()

            reply = await ask_gemini(image_bytes)

            try:
                await thinking.delete()
            except:
                pass

            await send_long_message(message, reply)

        except Exception as e:
            await message.answer("Ошибка фото 😢")
            print(e)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())