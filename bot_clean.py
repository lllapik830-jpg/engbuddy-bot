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

# --- Создаем Flask приложение для keep_alive ---
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Mr. EngBuddy is running!"

def keep_alive():
    app.run(host='0.0.0.0', port=8080)

# --- Запускаем keep_alive в отдельном потоке ---
threading.Thread(target=keep_alive, daemon=True).start()

# --- Основной код бота ---
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

if not BOT_TOKEN or not OPENROUTER_API_KEY or not ELEVENLABS_API_KEY:
    raise ValueError("Keys missing")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

USER_DATA_FILE = "users.json"

def load_users():
    try:
        with open(USER_DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_users(data):
    with open(USER_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_system_prompt(user_name="Student", level="A1"):
    return (
        f"You are a strict but friendly English tutor named Mr. EngBuddy. "
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
                "max_tokens": 80
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

# --- КНОПКА ПЕРЕВОДА ---
def translate_keyboard(lang="Russian"):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"📖 Перевести на {lang}", callback_data="translate")
        ]
    ])
    return keyboard

# --- КНОПКИ ВНИЗУ ---
def main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📚 Continue learning"),
                KeyboardButton(text="🔄 Start over")
            ],
            [
                KeyboardButton(text="📊 My level")
            ]
        ],
        resize_keyboard=True
    )
    return keyboard

# --- ХРАНИЛИЩЕ ДЛЯ ПЕРЕВОДОВ ---
user_translations = {}

@dp.message(Command("start"))
async def start_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    
    if user_id in users:
        user_data = users[user_id]
        if user_data.get("name") is None:
            user_data["step"] = "name"
            save_users(users)
            await m.reply(
                "🤖 *Hello! I'm Mr. EngBuddy.* 🇬🇧\n\n"
                "📝 *What is your name?*",
                parse_mode="Markdown"
            )
            return
        if user_data.get("language") is None:
            user_data["step"] = "language"
            save_users(users)
            await m.reply(
                "🌐 *What is your native language?*\nType your language (e.g., Russian)",
                parse_mode="Markdown"
            )
            return
        await m.reply(
            f"👋 Welcome back, *{user_data['name']}*!\n"
            f"🌐 Language: *{user_data['language']}*\n\n"
            "Choose an option:",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        return
    
    users[user_id] = {"name": None, "language": None, "level": "A1", "step": "name"}
    save_users(users)
    await m.reply(
        "🤖 *Hello! I'm Mr. EngBuddy.* 🇬🇧\n\n"
        "📝 *What is your name?*",
        parse_mode="Markdown"
    )

@dp.message(Command("reset"))
async def reset_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id in users:
        del users[user_id]
        save_users(users)
        await m.reply("🔄 Reset complete. Use /start to begin again.")
        

@dp.message(Command("upgrade"))
async def upgrade_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    
    if user_id not in users:
        await m.reply("Please use /start first.")
        return
    
    try:
        invoice_link = await bot.create_invoice_link(
            title="EngBuddy Premium — 1 month",
            description="Full access: unlimited messages, voice replies, lessons and tests!",
            payload=f"premium_{user_id}",
            provider_token="",
            currency="XTR",
            prices=[{"label": "Premium (1 month)", "amount": 15}],
            need_name=True,
            need_phone_number=True
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="💎 Купить Premium за 15 Stars", url=invoice_link)
            ]
        ])
        
        await m.reply(
            f"💎 *Upgrade to Premium!*\n\n"
            f"Get unlimited access:\n"
            f"• 🎤 Unlimited voice messages\n"
            f"• 🔊 Unlimited voice replies\n"
            f"• 📚 Lessons and tests\n"
            f"• 📈 Personal progress tracking\n\n"
            f"💰 Price: *15 Stars*\n\n"
            f"Press the button below to pay 👇",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"Stars invoice error: {e}")
        await m.reply("❌ Payment system error. Please try again later.")

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    
    if callback.data == "translate":
        translation = user_translations.get(user_id, {}).get("translation")
        if translation:
            await callback.message.reply(f"🌐 {translation}")
        else:
            await callback.message.reply("❌ Translation not found. Please try again.")
        await callback.answer()

@dp.message()
async def catch_all(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id not in users:
        await m.reply("Please use /start first.")
        return
            # --- ЛОГИРОВАНИЕ КАЖДОГО СООБЩЕНИЯ ---
    user_name = users.get(user_id, {}).get("name", "Unknown")
    logging.info(f"📩 [{user_name}] (ID: {user_id}) | Type: {m.content_type} | Text: {m.text if m.text else 'Voice/Media'}")
    
    user_data = users[user_id]
    step = user_data.get("step", "ready")
    
    # --- КНОПКИ ВНИЗУ ---
    if m.text == "📚 Continue learning":
        await m.reply("Send me a message! 🇬🇧", reply_markup=main_keyboard())
        return
    if m.text == "🔄 Start over":
        del users[user_id]
        save_users(users)
        await m.reply("Reset complete. Use /start to begin again.")
        return
    if m.text == "📊 My level":
        await m.reply(f"📊 Your level: **{user_data.get('level', 'A1')}**", parse_mode="Markdown", reply_markup=main_keyboard())
        return
    
    # --- РЕГИСТРАЦИЯ ---
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
        
        welcome_msg = (
            f"✅ Registration complete, {user_data['name']}! 🎉\n\n"
            f"Your language: {user_data['language']}\n\n"
            "What I can do:\n"
            "  • 💬 Chat with you in English and translate to your language\n"
            "  • 🎤 Listen to your voice messages and reply\n"
            "  • 🔊 Reply with voice messages (real human-like voice!)\n"
            "  • 📚 Help you practice speaking and grammar\n"
            "  • ❓ Ask questions to keep the conversation going\n\n"
            "🚀 The bot is constantly evolving!\n"
            "Soon I'll have lessons, tests, and personalized tasks. Stay tuned! 📈\n\n"
            "Now send me a message — text or voice! 🇬🇧"
        )
        
        welcome_ru = translate_to_language(welcome_msg, user_data["language"])
        user_translations[user_id] = {"translation": welcome_ru}
        
        await m.reply(welcome_msg, reply_markup=translate_keyboard(user_data["language"]))
        return
    
    # --- ОСНОВНАЯ ЛОГИКА ---
    user_name = user_data["name"]
    lang = user_data["language"]
    level = user_data.get("level", "A1")
    
    # --- ТЕКСТ ---
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
    
    # --- ГОЛОС ---
    if m.voice:
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
                await m.reply("Could not understand audio.", reply_markup=main_keyboard())
        except Exception as e:
            logging.error(f"Voice error: {e}")
            await m.reply("Error processing voice.", reply_markup=main_keyboard())
        return

async def main():
    print("🤖 Mr. EngBuddy is ready!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
