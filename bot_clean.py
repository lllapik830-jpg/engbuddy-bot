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

LESSONS = {}

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

def clear_state(user_id):
    users = load_users()
    if user_id in users:
        users[user_id]["current_lesson"] = None
        save_users(users)

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
                "max_tokens": 800
            },
            timeout=20
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

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗣️ Общаться"), KeyboardButton(text="📚 Уроки")],
            [KeyboardButton(text="💎 Подписка"), KeyboardButton(text="📊 Прогресс")],
            [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="🔄 Сброс")]
        ],
        resize_keyboard=True
    )

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

@dp.message(Command("start"))
async def start_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id in users:
        clear_state(user_id)
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
    users[user_id] = {"name": None, "language": None, "level": "A1", "step": "name", "lessons_done": 0, "words_learned": 0}
    save_users(users)
    await m.reply("🤖 *Hello! I'm LexDAN, your AI English tutor.*\n\n📝 *What is your name?*", parse_mode="Markdown")

@dp.message(Command("reset"))
async def reset_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id in users:
        name = users[user_id].get("name", "Student")
        language = users[user_id].get("language", "Russian")
        premium_until = users[user_id].get("premium_until", 0)
        users[user_id] = {
            "name": name,
            "language": language,
            "level": "A1",
            "step": "ready",
            "premium_until": premium_until,
            "lessons_done": 0,
            "words_learned": 0,
            "voice_count": 0,
            "voice_date": date.today().isoformat()
        }
        save_users(users)
        await m.reply(
            f"🔄 *Данные сброшены.*\n"
            f"Твои регистрационные данные сохранены ✅\n"
            f"Имя: {name}\n"
            f"Язык: {language}\n\n"
            "Прогресс (уроки и слова) обнулён. Подписка активна.",
            parse_mode="Markdown",
            reply_markup=main_menu()
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
            "✅ Слова, тексты, вопросы\n\n"
            "Нажми /upgrade, чтобы купить.",
            parse_mode="Markdown"
        )
        return
    if not LESSONS:
        await m.reply(
            "📚 *Уроки скоро появятся!*\n\n"
            "Я добавляю новые материалы каждый день. Загляни позже! 🚀",
            parse_mode="Markdown"
        )
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="A1", callback_data="level_A1")],
        [InlineKeyboardButton(text="A2", callback_data="level_A2")],
        [InlineKeyboardButton(text="B1", callback_data="level_B1")],
        [InlineKeyboardButton(text="B2", callback_data="level_B2")],
        [InlineKeyboardButton(text="C1", callback_data="level_C1")]
    ])
    await m.reply("📚 *Выбери свой уровень:*", parse_mode="Markdown", reply_markup=keyboard)

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
        return

    if callback.data == "buy_base" or callback.data == "buy_premium":
        price = "399 ₽" if callback.data == "buy_base" else "799 ₽"
        await callback.message.reply(
            f"💎 *Вы выбрали подписку за {price}*\n\n"
            f"Переведите {price} на карту:\n"
            f"`1234 5678 9012 3456`\n\n"
            f"После перевода пришлите сюда скриншот или напишите «Оплатил».",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if callback.data.startswith("level_"):
        level = callback.data.split("_")[1]
        user_data["current_level"] = level
        save_users(users)
        if level in LESSONS:
            topics = list(LESSONS[level].keys())
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            for topic in topics:
                keyboard.inline_keyboard.append([InlineKeyboardButton(text=topic, callback_data=f"topic_{level}_{topic}")])
            await callback.message.reply(f"📚 *Уровень {level}*\n\nВыбери тему:", parse_mode="Markdown", reply_markup=keyboard)
        else:
            await callback.message.reply("📚 *Уроки для этого уровня скоро появятся!*", parse_mode="Markdown")
        await callback.answer()
        return

    if callback.data.startswith("topic_"):
        parts = callback.data.split("_")
        level = parts[1]
        topic = parts[2]
        lesson = LESSONS.get(level, {}).get(topic)
        if not lesson:
            await callback.message.reply("❌ Урок не найден.")
            await callback.answer()
            return
        text = f"📚 *{topic} ({level})*\n\n"
        text += "📝 *Слова:*\n"
        for w in lesson["words"]:
            text += f"• {w['en']} — {w['ru']}\n"
        text += f"\n📖 *Текст:*\n{lesson['text']}\n\n"
        text += "❓ *Вопросы:*\n"
        for i, q in enumerate(lesson["questions"], 1):
            text += f"{i}. {q['q']}\n"
        await callback.message.reply(text, parse_mode="Markdown")
        await callback.answer()
        return

    await callback.message.reply("⚠️ Неизвестная команда.")
    await callback.answer()

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
    if m.text == "📚 Уроки":
        await lesson_cmd(m)
        return
    if m.text == "💎 Подписка":
        await upgrade_cmd(m)
        return
    if m.text == "📊 Прогресс":
        if is_premium(user_id):
            await m.reply(f"📊 *Твой прогресс:*\n\n✅ Премиум активен\n📚 Уроков пройдено: {user_data.get('lessons_done', 0)}\n📝 Слов выучено: {user_data.get('words_learned', 0)}", parse_mode="Markdown", reply_markup=main_menu())
        else:
            await m.reply("📊 *Ты на бесплатном тарифе.*\n\n🎤 Осталось голосовых на сегодня: 20\n💎 Купи подписку, чтобы снять лимиты.", parse_mode="Markdown", reply_markup=main_menu())
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
    if m.text == "🔄 Сброс":
        await reset_cmd(m)
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
        
        welcome_en = (
            f"🌟 *Welcome, {user_data['name']}!*\n\n"
            f"🌐 Your native language: *{user_data['language']}*\n\n"
            "🎯 *Your AI English tutor is ready to help you:*\n\n"
            "💬 *Chat in English* — practice anytime, get instant feedback.\n"
            "🎤 *Voice messages* — speak, listen, and improve your pronunciation.\n"
            "📚 *Structured lessons* — learn with proven methods, not random topics.\n"
            "✨ *Grammar check & correction* — I'll fix your mistakes and explain them.\n\n"
            "🔥 *Why LexDAN?*\n"
            "• No need to pay a tutor — I'm here 24/7.\n"
            "• Learn at your own pace with real materials.\n"
            "• Level up from A1 to C1 with clear progress.\n\n"
            "💎 You get *20 free voice messages* every day.\n"
            "Unlock unlimited learning with /upgrade.\n\n"
            "👇 *Choose what to do using the buttons below.*"
        )
        
        translation_ru = translate_to_language(welcome_en, user_data["language"])
        if translation_ru:
            user_translations[user_id] = {"translation": translation_ru}
        
        await m.reply(welcome_en, parse_mode="Markdown", reply_markup=main_menu())
        await m.reply("📖 *Нажми кнопку ниже, чтобы прочитать это сообщение на русском.*", parse_mode="Markdown", reply_markup=translate_keyboard(user_data["language"]))
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
