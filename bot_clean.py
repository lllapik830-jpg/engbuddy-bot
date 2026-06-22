import os
import logging
import requests
import asyncio
import io
import json
import speech_recognition as sr
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pydub import AudioSegment
import tempfile
import re
from flask import Flask
import threading
import time
from datetime import date

app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 LexDAN — AI English Tutor is running!"

def keep_alive():
    app.run(host='0.0.0.0', port=8080)

threading.Thread(target=keep_alive, daemon=True).start()

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

if not BOT_TOKEN or not OPENROUTER_API_KEY or not ELEVENLABS_API_KEY:
    raise ValueError("Keys missing")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

USER_DATA_FILE = "users.json"
MANAGER_ID = 1809897303

def load_users():
    try:
        with open(USER_DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_users(data):
    with open(USER_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def is_premium(user_id):
    users = load_users()
    user_data = users.get(user_id, {})
    premium_until = user_data.get("premium_until", 0)
    return time.time() < premium_until

def get_system_prompt(user_name="Student", level="A1"):
    return (
        f"You are a strict but friendly English tutor named LexDAN. "
        f"Your student's name is {user_name}. Their level is {level}. "
        f"Respond ONLY in English. Keep responses SHORT (1-2 sentences). "
        f"Always ask a follow-up question to practice speaking. "
        f"If the student makes a grammar mistake, correct it gently and explain briefly."
    )

def ask_gpt(prompt, user_name="Student", level="A1"):
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": get_system_prompt(user_name, level)},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 150
            },
            timeout=15
        )
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logging.error(f"GPT error: {e}")
        return "Sorry, I couldn't process that."

def translate_to_language(text, target_lang):
    if not target_lang or target_lang.lower() == "english":
        return None
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": f"Translate the following English text to NATURAL {target_lang}. Keep the meaning, but make it sound like a friendly tutor explaining to a student. Only output the {target_lang} translation, nothing else."},
                    {"role": "user", "content": text}
                ],
                "max_tokens": 150
            },
            timeout=15
        )
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logging.error(f"Translation error: {e}")
        return None

def elevenlabs_tts(text):
    try:
        url = "https://api.elevenlabs.io/v1/text-to-speech/pNInz6obpgDQGcFmaJgB"
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }
        data = {
            "text": text,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }
        response = requests.post(url, headers=headers, json=data, timeout=20)
        if response.status_code == 200:
            return response.content
        else:
            logging.error(f"ElevenLabs error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logging.error(f"ElevenLabs error: {e}")
        return None

def format_bilingual_response(text_en, lang):
    if not lang or lang.lower() == "english":
        return f"🇬🇧 {text_en}"
    sentences = re.split(r'(?<=[.!?])\s+', text_en.strip())
    parts = []
    for sent in sentences:
        if not sent:
            continue
        trans = translate_to_language(sent, lang)
        if trans:
            parts.append(f"🇬🇧 {sent}\n🌐 {trans}")
        else:
            parts.append(f"🇬🇧 {sent}")
    return "\n\n".join(parts)

def main_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗣️ Общаться"), KeyboardButton(text="📖 Уроки")],
            [KeyboardButton(text="💎 Подписка"), KeyboardButton(text="📊 Прогресс")],
            [KeyboardButton(text="🔄 Сброс"), KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True
    )
    return keyboard

def subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💎 Безлимит (399 ₽)", callback_data="buy_base"),
            InlineKeyboardButton(text="👑 Премиум (799 ₽)", callback_data="buy_premium")
        ]
    ])

def translate_keyboard(lang="Russian"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📖 Перевести на {lang}", callback_data="translate")]
    ])

def lesson_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="A1", callback_data="level_A1")],
        [InlineKeyboardButton(text="A2", callback_data="level_A2")],
        [InlineKeyboardButton(text="B1", callback_data="level_B1")],
        [InlineKeyboardButton(text="B2", callback_data="level_B2")],
        [InlineKeyboardButton(text="C1", callback_data="level_C1")]
    ])
    return keyboard

def topic_menu(level):
    topics = {
        "A1": ["Семья", "Еда", "Дом", "Одежда", "Путешествия", "Хобби"],
        "A2": ["Работа", "Учёба", "Город", "Погода", "Магазины", "Здоровье"],
        "B1": ["Путешествия", "Работа", "Здоровье", "Технологии", "Образование", "Искусство"],
        "B2": ["Бизнес", "Психология", "Политика", "Экология", "Наука", "Культура"],
        "C1": ["Лидерство", "Инновации", "Глобализация", "Философия", "Экономика", "Право"]
    }
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for topic in topics.get(level, []):
        keyboard.inline_keyboard.append([InlineKeyboardButton(text=topic, callback_data=f"topic_{level}_{topic}")])
    return keyboard

def generate_lesson_data(level, topic, user_name):
    prompt = f"""
    Generate a 15-minute English lesson for level {level} on the topic "{topic}".
    Student's name is {user_name}.
    Return ONLY a JSON object with the following structure:
    {{
        "words": [
            {{"word": "word1", "definition": "definition1", "example": "example1"}},
            ...
        ],
        "text": "short reading text (3-5 sentences)",
        "phrases": ["phrase1", "phrase2", "phrase3"]
    }}
    Do not include any other text, only the JSON.
    """
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1200
            },
            timeout=30
        )
        content = response.json()["choices"][0]["message"]["content"]
        # Извлекаем JSON из ответа (на случай, если будут лишние символы)
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        logging.error(f"Lesson generation error: {e}")
        return None

@dp.message(Command("start"))
async def start_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id in users:
        user_data = users[user_id]
        if user_data.get("name") is None:
            user_data["step"] = "name"
            save_users(users)
            await m.reply("🤖 *Hello! I'm LexDAN, your AI English tutor.*\n\n📝 *What is your name?*", parse_mode="Markdown")
            return
        if user_data.get("language") is None:
            user_data["step"] = "language"
            save_users(users)
            await m.reply("🌐 *What is your native language?*\nType your language (e.g., Russian)", parse_mode="Markdown")
            return
        await m.reply(
            f"👋 Welcome back, *{user_data['name']}*!\n🌐 Language: *{user_data['language']}*\n\nChoose an option:",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
        return
    users[user_id] = {"name": None, "language": None, "level": "A1", "step": "name"}
    save_users(users)
    await m.reply("🤖 *Hello! I'm LexDAN, your AI English tutor.*\n\n📝 *What is your name?*", parse_mode="Markdown")

@dp.message(Command("reset"))
async def reset_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id in users:
        premium_until = users[user_id].get("premium_until", 0)
        del users[user_id]
        users[user_id] = {
            "name": None,
            "language": None,
            "level": "A1",
            "step": "name",
            "premium_until": premium_until
        }
        save_users(users)
        await m.reply(
            "🔄 Данные сброшены.\n"
            "Ваша подписка сохранена ✅\n"
            "Используйте /start, чтобы начать заново."
        )
    else:
        await m.reply("❌ Нет данных для сброса.")

@dp.message(Command("upgrade"))
async def upgrade_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id not in users:
        await m.reply("Please use /start first.")
        return
    await m.reply(
        "💎 *Выберите подписку:*\n\n"
        "🔹 Безлимит (399 ₽) — голосовые без ограничений + исправление ошибок\n"
        "🔹 Премиум (799 ₽) — всё из безлимита + уроки по уровням\n\n"
        "Нажмите кнопку ниже:",
        parse_mode="Markdown",
        reply_markup=subscription_keyboard()
    )

@dp.message(Command("lesson"))
async def lesson_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id not in users:
        await m.reply("Please use /start first.")
        return
    if not is_premium(user_id):
        await m.reply(
            "📚 *Уроки доступны только в Премиум-подписке!*\n\n"
            "💰 799 ₽/мес\n"
            "✅ Уроки по уровням (A1–C1)\n"
            "✅ Тесты и обратная связь\n\n"
            "Нажми /upgrade, чтобы купить.",
            parse_mode="Markdown"
        )
        return
    await m.reply("📚 *Выбери свой уровень:*", parse_mode="Markdown", reply_markup=lesson_menu())

@dp.message(Command("buy"))
async def buy_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id not in users:
        await m.reply("Please use /start first.")
        return
    await m.reply(
        f"💎 *Как купить подписку:*\n\n"
        f"1️⃣ Переведите нужную сумму на карту:\n"
        f"`1234 5678 9012 3456`\n\n"
        f"2️⃣ После перевода напишите «Оплатил» и пришлите скриншот.\n"
        f"3️⃣ Мы проверим и активируем подписку в течение 5–10 минут.\n\n"
        f"✅ Подписка действует 30 дней.",
        parse_mode="Markdown"
    )

@dp.message(Command("activate"))
async def activate_cmd(m: Message):
    if m.from_user.id != MANAGER_ID:
        await m.reply("❌ У вас нет прав для этой команды.")
        return
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("❌ Укажите ID пользователя: /activate 123456789")
        return
    target_user_id = parts[1]
    users = load_users()
    if target_user_id not in users:
        await m.reply(f"❌ Пользователь с ID {target_user_id} не найден.")
        return
    users[target_user_id]["premium_until"] = time.time() + 30 * 24 * 60 * 60
    save_users(users)
    await m.reply(f"✅ Подписка активирована для пользователя {target_user_id}!")
    try:
        await bot.send_message(
            target_user_id,
            f"🎉 *Подписка Premium активирована!*\n\n"
            f"✅ Доступ открыт на 30 дней.\n"
            f"Наслаждайтесь всеми функциями бота! 🚀",
            parse_mode="Markdown"
        )
    except Exception as e:
        await m.reply(f"⚠️ Не удалось отправить уведомление пользователю: {e}")

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    users = load_users()
    user_data = users.get(user_id, {})
    user_name = user_data.get("name", "Student")
    lang = user_data.get("language", "Russian")

    if callback.data == "translate":
        translation = user_translations.get(user_id, {}).get("translation")
        if translation:
            await callback.message.reply(f"🌐 {translation}")
        else:
            await callback.message.reply("❌ Перевод не найден.")
        await callback.answer()

    elif callback.data == "buy_base" or callback.data == "buy_premium":
        price = "399 ₽" if callback.data == "buy_base" else "799 ₽"
        await callback.message.reply(
            f"💎 *Вы выбрали подписку за {price}*\n\n"
            f"Переведите {price} на карту:\n"
            f"`1234 5678 9012 3456`\n\n"
            f"После перевода пришлите сюда скриншот или напишите «Оплатил».",
            parse_mode="Markdown"
        )
        await callback.answer()

    elif callback.data.startswith("level_"):
        level = callback.data.split("_")[1]
        user_data["current_level"] = level
        save_users(users)
        await callback.message.reply(
            f"📚 *Ты выбрал уровень {level}.*\n\nТеперь выбери тему:",
            parse_mode="Markdown",
            reply_markup=topic_menu(level)
        )
        await callback.answer()

    elif callback.data.startswith("topic_"):
        parts = callback.data.split("_")
        level = parts[1]
        topic = parts[2]

        await callback.message.reply(f"⏳ *Генерирую урок на тему «{topic}» ({level})...*\n\nЭто займёт 10–15 секунд.", parse_mode="Markdown")

        lesson_data = generate_lesson_data(level, topic, user_name)
        if not lesson_data:
            await callback.message.reply("❌ Не удалось сгенерировать урок. Попробуйте позже.")
            await callback.answer()
            return

        user_data["lesson_words"] = lesson_data.get("words", [])
        user_data["lesson_text"] = lesson_data.get("text", "")
        user_data["lesson_phrases"] = lesson_data.get("phrases", [])
        user_data["lesson_topic"] = topic
        user_data["lesson_level"] = level
        user_data["lesson_step"] = "vocabulary"
        user_data["lesson_word_index"] = 0
        save_users(users)

        await send_next_word(callback.message, user_id)
        await callback.answer()

    elif callback.data == "vocab_next":
        user_data = users.get(user_id, {})
        idx = user_data.get("lesson_word_index", 0) + 1
        user_data["lesson_word_index"] = idx
        save_users(users)
        await send_next_word(callback.message, user_id)
        await callback.answer()

    elif callback.data == "vocab_done":
        user_data = users.get(user_id, {})
        user_data["lesson_step"] = "exercise"
        save_users(users)
        await send_exercise(callback.message, user_id)
        await callback.answer()

    elif callback.data.startswith("ex_ans_"):
        choice = int(callback.data.split("_")[2])
        user_data = users.get(user_id, {})
        correct = user_data.get("exercise_correct", 0)
        if choice == correct:
            await callback.message.reply("✅ Правильно! Молодец! 🎉")
        else:
            await callback.message.reply(f"❌ Неправильно. Правильный ответ был: {user_data.get('exercise_correct_word', '')}")
        await callback.answer()

    elif callback.data == "next_section":
        user_data = users.get(user_id, {})
        step = user_data.get("lesson_step", "vocabulary")
        if step == "exercise":
            user_data["lesson_step"] = "reading"
            save_users(users)
            await send_reading(callback.message, user_id)
        elif step == "reading":
            user_data["lesson_step"] = "listening"
            save_users(users)
            await send_listening(callback.message, user_id)
        elif step == "listening":
            user_data["lesson_step"] = "speaking"
            save_users(users)
            await send_speaking(callback.message, user_id)
        elif step == "speaking":
            await callback.message.reply("🎉 *Поздравляю! Ты прошёл урок!*\n\nОтличная работа! Ты прокачал английский 💪")
        await callback.answer()

async def send_next_word(message: types.Message, user_id: str):
    users = load_users()
    user_data = users.get(user_id, {})
    words = user_data.get("lesson_words", [])
    idx = user_data.get("lesson_word_index", 0)
    lang = user_data.get("language", "Russian")

    if idx >= len(words):
        user_data["lesson_step"] = "exercise"
        save_users(users)
        await send_exercise(message, user_id)
        return

    word_data = words[idx]
    word = word_data.get("word", "")
    definition = word_data.get("definition", "")
    example = word_data.get("example", "")

    text = f"📚 *Слово {idx+1}/{len(words)}*\n\n🇬🇧 {word}\n📖 {definition}\n💬 *Пример:* {example}"
    translation = translate_to_language(f"{word}: {definition}. Пример: {example}", lang)

    await message.reply(text, parse_mode="Markdown")
    if translation:
        user_translations[user_id] = {"translation": translation}
        await message.reply("🌐 Нажми, чтобы перевести это слово.", reply_markup=translate_keyboard(lang))

    audio_text = f"{word}. {example}"
    audio_bytes = elevenlabs_tts(audio_text)
    if audio_bytes:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(audio_bytes)
                path = tmp.name
            await message.reply_voice(FSInputFile(path))
            os.unlink(path)
        except Exception as e:
            logging.error(f"TTS error: {e}")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Дальше", callback_data="vocab_next")]
    ])
    await message.reply("Нажми «Дальше», чтобы продолжить.", reply_markup=keyboard)

async def send_exercise(message: types.Message, user_id: str):
    users = load_users()
    user_data = users.get(user_id, {})
    words = user_data.get("lesson_words", [])
    if len(words) < 4:
        await message.reply("❌ Недостаточно слов для упражнения.")
        return

    selected = words[:4]
    correct_word = selected[0]["word"]
    user_data["exercise_correct"] = 0
    user_data["exercise_correct_word"] = correct_word
    save_users(users)

    text = "✍️ *Задание: подставь слово в предложение*\n\n"
    text += f"Выбери правильное слово:\n"
    for i, w in enumerate(selected):
        text += f"{i+1}. {w['word']}\n"
    text += f"\n➡️ *Какое слово подходит в предложение:* «{selected[0]['example']}»"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"1️⃣ {selected[0]['word']}", callback_data=f"ex_ans_0"),
            InlineKeyboardButton(text=f"2️⃣ {selected[1]['word']}", callback_data=f"ex_ans_1")
        ],
        [
            InlineKeyboardButton(text=f"3️⃣ {selected[2]['word']}", callback_data=f"ex_ans_2"),
            InlineKeyboardButton(text=f"4️⃣ {selected[3]['word']}", callback_data=f"ex_ans_3")
        ]
    ])

    await message.reply(text, parse_mode="Markdown", reply_markup=keyboard)

async def send_reading(message: types.Message, user_id: str):
    users = load_users()
    user_data = users.get(user_id, {})
    text = user_data.get("lesson_text", "Короткий текст для чтения. Прочитай его вслух в микрофон.")
    await message.reply(f"📖 *Чтение*\n\n{text}\n\n🎤 Прочитай текст в микрофон. Я проверю твоё произношение.", parse_mode="Markdown")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Прочитал", callback_data="next_section")]
    ])
    await message.reply("Нажми «Прочитал», когда будешь готов к следующему блоку.", reply_markup=keyboard)

async def send_listening(message: types.Message, user_id: str):
    users = load_users()
    user_data = users.get(user_id, {})
    phrases = user_data.get("lesson_phrases", ["Hello, how are you?", "I like learning English."])
    text = "🎧 *Аудирование*\n\nЯ произнесу несколько фраз. Напиши, что ты услышал.\n"
    for i in range(len(phrases[:3])):
        text += f"{i+1}. ...\n"
    await message.reply(text, parse_mode="Markdown")
    for phrase in phrases[:3]:
        audio_bytes = elevenlabs_tts(phrase)
        if audio_bytes:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    tmp.write(audio_bytes)
                    path = tmp.name
                await message.reply_voice(FSInputFile(path))
                os.unlink(path)
            except Exception as e:
                logging.error(f"TTS error: {e}")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Прослушал", callback_data="next_section")]
    ])
    await message.reply("Нажми «Прослушал», чтобы перейти к общению.", reply_markup=keyboard)

async def send_speaking(message: types.Message, user_id: str):
    users = load_users()
    user_data = users.get(user_id, {})
    topic = user_data.get("lesson_topic", "твои любимые занятия")
    await message.reply(
        f"🗣️ *Общение*\n\n"
        f"Тема: *{topic}*\n\n"
        f"Расскажи мне о {topic} в нескольких предложениях. Ответь голосовым сообщением.\n\n"
        f"Я проверю твою грамматику и произношение.",
        parse_mode="Markdown"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ответил", callback_data="next_section")]
    ])
    await message.reply("Нажми «Ответил», когда закончишь говорить.", reply_markup=keyboard)

@dp.message()
async def catch_all(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id not in users:
        await m.reply("Please use /start first.")
        return
    user_data = users[user_id]
    step = user_data.get("step", "ready")
    user_name = users.get(user_id, {}).get("name", "Unknown")
    logging.info(f"📩 [{user_name}] (ID: {user_id}) | Type: {m.content_type} | Text: {m.text if m.text else 'Voice/Media'}")

    if m.text == "🗣️ Общаться":
        await m.reply("🗣️ *Я готов!* Отправь мне текст или голосовое сообщение.", parse_mode="Markdown", reply_markup=main_menu())
        return
    if m.text == "📖 Уроки":
        await lesson_cmd(m)
        return
    if m.text == "💎 Подписка":
        await upgrade_cmd(m)
        return
    if m.text == "📊 Прогресс":
        if is_premium(user_id):
            await m.reply("📊 *Твой прогресс:*\n\n✅ Премиум активен\n📚 Уроков пройдено: 0\n🎯 Следующий уровень: A1", parse_mode="Markdown", reply_markup=main_menu())
        else:
            await m.reply("📊 *Ты на бесплатном тарифе.*\n\n🎤 Осталось голосовых на сегодня: 20\n💎 Купи подписку, чтобы снять лимиты.", parse_mode="Markdown", reply_markup=main_menu())
        return
    if m.text == "🔄 Сброс":
        await reset_cmd(m)
        return
    if m.text == "❓ Помощь":
        await m.reply(
            "❓ *Как пользоваться ботом:*\n\n"
            "1️⃣ Зарегистрируйся через /start\n"
            "2️⃣ Общайся с репетитором текстом или голосом\n"
            "3️⃣ У тебя 20 бесплатных голосовых в день\n"
            "4️⃣ Купи подписку, чтобы снять лимиты\n"
            "5️⃣ В Премиуме доступны уроки по уровням\n\n"
            "Вопросы — пиши в поддержку.",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
        return

    if step == "name":
        user_data["name"] = m.text.strip()
        user_data["step"] = "language"
        save_users(users)
        await m.reply(
            f"Nice to meet you, *{user_data['name']}*! 🎉\n\n"
            "🌐 *What is your native language?*\nType your language (e.g., Russian)",
            parse_mode="Markdown"
        )
        return
    if step == "language":
        user_data["language"] = m.text.strip()
        user_data["step"] = "ready"
        save_users(users)
        await m.reply(
            f"✅ *Registration complete, {user_data['name']}!*\n\n"
            f"🌐 Language: {user_data['language']}\n\n"
            "🎯 *What I can do:*\n"
            "• 💬 Chat in English with translation\n"
            "• 🎤 Listen and reply to voice messages\n"
            "• 🔊 Reply with real human-like voice\n"
            "• 📚 Help with grammar and speaking\n\n"
            "💎 20 free voice messages per day.\n"
            "Use /upgrade to unlock unlimited access!",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
        return

    user_name = user_data["name"]
    lang = user_data["language"]
    level = user_data.get("level", "A1")

    if m.text and not m.text.startswith("/"):
        await m.reply("💬 Thinking...")
        answer_en = ask_gpt(m.text, user_name, level)
        answer_ru = translate_to_language(answer_en, lang)
        user_translations[user_id] = {"translation": answer_ru}
        await m.reply(f"🇬🇧 {answer_en}", reply_markup=translate_keyboard(lang))
        audio_bytes = elevenlabs_tts(answer_en)
        if audio_bytes:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    tmp.write(audio_bytes)
                    path = tmp.name
                await m.reply_voice(FSInputFile(path))
                os.unlink(path)
            except Exception as e:
                logging.error(f"TTS error: {e}")
        return

    if m.voice:
        if not is_premium(user_id):
            today = date.today().isoformat()
            if user_data.get("voice_date") != today:
                user_data["voice_date"] = today
                user_data["voice_count"] = 0
                save_users(users)
            if user_data.get("voice_count", 0) >= 20:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Купить безлимит (399 ₽)", callback_data="buy_base")]
                ])
                await m.reply(
                    "🎤 *Ты исчерпал лимит на сегодня.*\n\n"
                    "Купи безлимит за 399 ₽ и продолжай заниматься!",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                return
        await m.reply("🎧 Processing voice...")
        try:
            file = await bot.get_file(m.voice.file_id)
            voice_data = await bot.download_file(file.file_path)
            audio = AudioSegment.from_file(io.BytesIO(voice_data.read()), format="ogg")
            wav_bytes = io.BytesIO()
            audio.export(wav_bytes, format="wav")
            wav_bytes.seek(0)
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_bytes) as source:
                audio_data = recognizer.record(source)
                text = recognizer.recognize_google(audio_data, language="en-US")
            if text:
                if not is_premium(user_id):
                    user_data["voice_count"] = user_data.get("voice_count", 0) + 1
                    save_users(users)
                answer_en = ask_gpt(text, user_name, level)
                answer_ru = translate_to_language(answer_en, lang)
                user_translations[user_id] = {"translation": answer_ru}
                await m.reply(f"🗣️ You said: {text}\n\n🇬🇧 {answer_en}", reply_markup=translate_keyboard(lang))
                audio_bytes = elevenlabs_tts(answer_en)
                if audio_bytes:
                    try:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                            tmp.write(audio_bytes)
                            path = tmp.name
                        await m.reply_voice(FSInputFile(path))
                        os.unlink(path)
                    except Exception as e:
                        logging.error(f"TTS error: {e}")
            else:
                await m.reply("Could not understand audio.", reply_markup=main_menu())
        except Exception as e:
            logging.error(f"Voice error: {e}")
            await m.reply("Error processing voice.", reply_markup=main_menu())
        return

user_translations = {}

async def main():
    print("🤖 LexDAN is ready!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
