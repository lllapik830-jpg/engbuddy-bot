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
import difflib
import random

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

def clear_state(user_id):
    users = load_users()
    if user_id in users:
        users[user_id]["exercise_text"] = ""
        users[user_id]["exercise_answer"] = ""
        users[user_id]["exercise_topic"] = ""
        users[user_id]["exercise_attempt"] = 0
        users[user_id]["vocab_words"] = []
        users[user_id]["vocab_index"] = 0
        users[user_id]["vocab_phase"] = "word"
        users[user_id]["vocab_sentences"] = []
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

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗣️ Общаться"), KeyboardButton(text="📖 Уроки")],
            [KeyboardButton(text="💎 Подписка"), KeyboardButton(text="📊 Прогресс")],
            [KeyboardButton(text="🔄 Сброс"), KeyboardButton(text="❓ Помощь")]
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

def lesson_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="A1", callback_data="level_A1")],
        [InlineKeyboardButton(text="A2", callback_data="level_A2")],
        [InlineKeyboardButton(text="B1", callback_data="level_B1")],
        [InlineKeyboardButton(text="B2", callback_data="level_B2")],
        [InlineKeyboardButton(text="C1", callback_data="level_C1")]
    ])

def level_intro(level):
    if level == "A1":
        return (
            "📘 *Уровень A1 — твой первый шаг к свободе!*\n\n"
            "Привет! Это самый начальный уровень, с которого начинают все, кто хочет говорить по-английски. Здесь нет сложных времён и запутанных правил — только база, которая работает 100%.\n\n"
            "🧩 *Что ты освоишь на этом уровне:*\n"
            "🔤 Алфавит и звуки — научишься читать любые слова с первого взгляда.\n"
            "🔢 Цифры и даты — сможешь называть цены, время и свой возраст.\n"
            "📚 Грамматика — поймёшь, как устроены простые предложения.\n"
            "🗣️ Словарный запас — освоишь 500+ слов на тему: семья, еда, дом, работа, одежда, погода.\n"
            "🎯 Вопросы и приветствия — научишься знакомиться, спрашивать и отвечать.\n\n"
            "👇 *Куда идём?*"
        )
    return f"📚 *Уровень {level}*\n\nВыбери раздел для изучения:"

def level_menu(level):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔤 Алфавит", callback_data=f"section_{level}_alphabet")],
        [InlineKeyboardButton(text="🔢 Цифры", callback_data=f"section_{level}_numbers")],
        [InlineKeyboardButton(text="📚 Грамматика", callback_data=f"section_{level}_grammar")],
        [InlineKeyboardButton(text="🗣️ Вокабуляр", callback_data=f"section_{level}_vocabulary")],
        [InlineKeyboardButton(text="📖 Чтение", callback_data=f"section_{level}_reading")],
        [InlineKeyboardButton(text="🎧 Аудирование", callback_data=f"section_{level}_listening")],
        [InlineKeyboardButton(text="💬 Общение", callback_data=f"section_{level}_speaking")]
    ])

def grammar_submenu(level):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧩 Глагол to be", callback_data=f"grammar_{level}_tobe")],
        [InlineKeyboardButton(text="🔄 Present Simple", callback_data=f"grammar_{level}_presentsimple")],
        [InlineKeyboardButton(text="📍 Предлоги", callback_data=f"grammar_{level}_prepositions")],
        [InlineKeyboardButton(text="❓ Вопросы и приветствия", callback_data=f"grammar_{level}_questions")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"level_back_{level}")]
    ])

def generate_lesson_data(level, topic, user_name):
    prompt = f"""
    Generate a short English lesson for level {level} on the topic "{topic}".
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
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        logging.error(f"Lesson generation error: {e}")
        return None

def generate_vocab_words(level, user_name):
    if level != "A1":
        level = "A1"
    prompt = f"""
    Generate 4 simple English words for a BEGINNER (A1 level). The words must be very common and easy (e.g., mother, school, apple, dog, book, cat, house, friend, family, etc.).
    Student's name is {user_name}.
    Return ONLY a JSON array:
    [{{"word": "word1", "definition": "simple definition in English", "translation": "translation in Russian", "example": "simple example sentence"}}, ...]
    Do not include any other text, only the JSON array.
    """
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800
            },
            timeout=30
        )
        content = response.json()["choices"][0]["message"]["content"]
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        logging.error(f"Vocab generation error: {e}")
        return None

def generate_match_exercise(words):
    prompt = f"""
    Generate 4 sentences with gaps for the following words: {[w['word'] for w in words]}.
    Each sentence must have a gap (____) where one of the words fits.
    The sentences should be SIMPLE and for A1 level.
    Return ONLY a JSON array of 4 sentences in the same order as the words:
    ["Sentence 1 with ____", "Sentence 2 with ____", "Sentence 3 with ____", "Sentence 4 with ____"]
    Do not include any other text, only the JSON array.
    """
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 600
            },
            timeout=30
        )
        content = response.json()["choices"][0]["message"]["content"]
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        logging.error(f"Match exercise error: {e}")
        return None

def generate_one_exercise(level, topic, user_name):
    prompt = f"""
    Generate ONE English sentence for level {level} on the topic "{topic}" with a gap (____) where the student must fill in the correct word.
    Topic "{topic}" grammar rules:
    - For "tobe": use am, is, are
    - For "presentsimple": use the correct verb form (add -s/-es for he/she/it, or keep base form for I/you/we/they)
    Student's name is {user_name}.
    Return ONLY a JSON object:
    {{"text": "I ____ a student.", "answer": "am"}}
    Do not include any other text, only the JSON.
    """
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300
            },
            timeout=15
        )
        content = response.json()["choices"][0]["message"]["content"]
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        logging.error(f"Exercise generation error: {e}")
        return None

def section_content(level, section):
    if section == "alphabet":
        return (
            "🔤 *Алфавит (Alphabet)*\n\n"
            "A - eɪ\nB - biː\nC - siː\nD - diː\nE - iː\n"
            "F - ɛf\nG - dʒiː\nH - eɪtʃ\nI - aɪ\nJ - dʒeɪ\n"
            "K - keɪ\nL - ɛl\nM - ɛm\nN - ɛn\nO - oʊ\n"
            "P - piː\nQ - kjuː\nR - ɑːr\nS - ɛs\nT - tiː\n"
            "U - juː\nV - viː\nW - ˈdʌbəljuː\nX - ɛks\nY - waɪ\nZ - ziː\n\n"
            "👇 *Напиши любую букву, и я произнесу её голосом!*"
        )
    elif section == "numbers":
        return (
            "🔢 *Цифры (Numbers)*\n\n"
            "1 - one [wʌn]\n2 - two [tuː]\n3 - three [θriː]\n4 - four [fɔːr]\n"
            "5 - five [faɪv]\n6 - six [sɪks]\n7 - seven [ˈsɛvən]\n8 - eight [eɪt]\n"
            "9 - nine [naɪn]\n10 - ten [tɛn]\n\n"
            "👇 *Напиши цифру от 1 до 10, и я произнесу её голосом!*"
        )
    elif section == "tobe":
        return (
            "🧩 *Глагол to be — основа английского!*\n\n"
            "Это самый важный глагол в языке. Он означает «быть», «являться», «находиться».\n\n"
            "📌 *Формы в настоящем времени:*\n"
            "• I **am** — я есть / я являюсь\n"
            "• You **are** — ты есть / вы есть\n"
            "• He/She/It **is** — он/она/оно есть\n"
            "• We **are** — мы есть\n"
            "• They **are** — они есть\n\n"
            "💡 *Запомни:*\n"
            "Am → только для I\n"
            "Is → для he, she, it\n"
            "Are → для you, we, they\n\n"
            "✅ *Примеры:*\n"
            "• I am Danil.\n"
            "• She is my sister.\n"
            "• We are friends.\n"
            "• They are at home.\n\n"
            "👇 *Теперь попробуй сам!*"
        )
    elif section == "presentsimple":
        return (
            "🔄 *Present Simple (настоящее простое)*\n\n"
            "Используется для:\n"
            "• Фактов (The sun rises in the east)\n"
            "• Привычек (I drink coffee every morning)\n"
            "• Расписаний (The train leaves at 8 pm)\n\n"
            "📌 *Формула:*\n"
            "I/You/We/They + глагол (без окончания)\n"
            "He/She/It + глагол + **s** (или **es**)\n\n"
            "❌ *Отрицание:* don't / doesn't\n"
            "❓ *Вопрос:* Do / Does\n\n"
            "✅ *Примеры:*\n"
            "• I work in an office.\n"
            "• She works from home.\n"
            "• Do you like music?\n"
            "• He doesn't drink coffee.\n\n"
            "👇 *Теперь попробуй сам!*"
        )
    elif section == "prepositions":
        return (
            "📍 *Предлоги (Prepositions)*\n\n"
            "🕒 *Время:*\n"
            "• at 5 o'clock, at night\n"
            "• on Monday, on July 5th\n"
            "• in May, in summer, in 2025\n\n"
            "📍 *Место:*\n"
            "• in the room, in Russia\n"
            "• on the table, on the street\n"
            "• at home, at work\n\n"
            "✅ *Примеры:*\nI wake up at 7 am.\nShe is in the kitchen.\nWe meet on Friday."
        )
    elif section == "questions":
        return (
            "❓ *Вопросы и приветствия*\n\n"
            "👋 *Приветствия:*\n"
            "Hello! / Hi! — Привет!\n"
            "Good morning! — Доброе утро!\n"
            "How are you? — Как дела?\n\n"
            "❓ *Вопросы:*\n"
            "What is your name? — Как тебя зовут?\n"
            "Where are you from? — Откуда ты?\n"
            "How old are you? — Сколько тебе лет?\n"
            "What do you do? — Чем ты занимаешься?\n\n"
            "💬 *Пример диалога:*\n"
            "- Hello! What is your name?\n"
            "- My name is Danil.\n"
            "- Nice to meet you, Danil!"
        )
    return f"📚 *Раздел «{section}» для уровня {level}*\n\nСкоро здесь появится контент! 🚀"

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
    users[user_id] = {"name": None, "language": None, "level": "A1", "step": "name"}
    save_users(users)
    await m.reply("🤖 *Hello! I'm LexDAN, your AI English tutor.*\n\n📝 *What is your name?*", parse_mode="Markdown")

@dp.message(Command("reset"))
async def reset_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id in users:
        clear_state(user_id)
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
    clear_state(user_id)
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
    clear_state(user_id)
    await m.reply("📚 *Выбери свой уровень:*", parse_mode="Markdown", reply_markup=lesson_menu())

@dp.message(Command("buy"))
async def buy_cmd(m: Message):
    user_id = str(m.from_user.id)
    users = load_users()
    if user_id not in users:
        await m.reply("Please use /start first.")
        return
    clear_state(user_id)
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
    clear_state(target_user_id)
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
    level = user_data.get("current_level", "A1")

    if callback.data == "translate":
        translation = user_translations.get(user_id, {}).get("translation")
        if translation:
            await callback.message.reply(f"🌐 {translation}")
        else:
            await callback.message.reply("❌ Перевод не найден.")
        await callback.answer()
        return

    if callback.data == "buy_base" or callback.data == "buy_premium":
        clear_state(user_id)
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
        clear_state(user_id)
        level = callback.data.split("_")[1]
        user_data["current_level"] = level
        save_users(users)
        await callback.message.reply(level_intro(level), parse_mode="Markdown", reply_markup=level_menu(level))
        await callback.answer()
        return

    if callback.data.startswith("section_"):
        clear_state(user_id)
        parts = callback.data.split("_")
        level = parts[1]
        section = parts[2]
        if section == "grammar":
            await callback.message.reply("📚 *Выбери тему по грамматике:*", parse_mode="Markdown", reply_markup=grammar_submenu(level))
            await callback.answer()
            return
        elif section in ["alphabet", "numbers"]:
            content = section_content(level, section)
            await callback.message.reply(content, parse_mode="Markdown")
            user_data["last_section"] = section
            save_users(users)
            await callback.answer()
            return
        elif section in ["tobe", "presentsimple"]:
            content = section_content(level, section)
            await callback.message.reply(content, parse_mode="Markdown")
            exercise = generate_one_exercise(level, section, user_name)
            if exercise and "text" in exercise and "answer" in exercise:
                user_data["exercise_text"] = exercise["text"]
                user_data["exercise_answer"] = exercise["answer"]
                user_data["exercise_topic"] = section
                user_data["exercise_attempt"] = 0
                save_users(users)
                await callback.message.reply(
                    f"📝 *Задание*\n\n{exercise['text']}\n\n_Напиши правильный ответ:_",
                    parse_mode="Markdown"
                )
            else:
                await callback.message.reply("❌ Не удалось сгенерировать задание. Попробуйте позже.")
            await callback.answer()
            return
        elif section == "vocabulary":
            user_data["vocab_words"] = []
            user_data["vocab_index"] = 0
            user_data["vocab_phase"] = "word"
            user_data["vocab_sentences"] = []
            words = generate_vocab_words(level, user_name)
            if words:
                user_data["vocab_words"] = words
                save_users(users)
                await send_next_vocab_word(callback.message, user_id)
            else:
                await callback.message.reply("❌ Не удалось сгенерировать слова. Попробуйте позже.")
            await callback.answer()
            return
        elif section in ["reading", "listening", "speaking"]:
            await callback.message.reply(f"⏳ *Генерирую раздел «{section}» для уровня {level}...*", parse_mode="Markdown")
            lesson_data = generate_lesson_data(level, section, user_name)
            if lesson_data:
                if section == "reading":
                    text = f"📖 *Чтение*\n\n{lesson_data.get('text', 'Текст не сгенерирован.')}"
                    await callback.message.reply(text, parse_mode="Markdown")
                elif section == "listening":
                    phrases = lesson_data.get("phrases", [])
                    text = "🎧 *Аудирование*\n\nПрослушай фразы и напиши, что услышал:\n"
                    for i, p in enumerate(phrases[:3], 1):
                        text += f"{i}. ...\n"
                    await callback.message.reply(text, parse_mode="Markdown")
                    for p in phrases[:3]:
                        audio_bytes = elevenlabs_tts(p)
                        if audio_bytes:
                            try:
                                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                                    tmp.write(audio_bytes)
                                    path = tmp.name
                                await callback.message.reply_voice(FSInputFile(path))
                                os.unlink(path)
                            except Exception as e:
                                logging.error(f"TTS error: {e}")
                elif section == "speaking":
                    await callback.message.reply(
                        f"🗣️ *Общение*\n\nТема: *{lesson_data.get('topic', 'твои любимые занятия')}*\n\n"
                        "Расскажи мне о теме в нескольких предложениях. Ответь голосовым сообщением.",
                        parse_mode="Markdown"
                    )
            else:
                await callback.message.reply("❌ Не удалось сгенерировать контент. Попробуйте позже.")
            await callback.answer()
            return

    if callback.data.startswith("grammar_"):
        clear_state(user_id)
        parts = callback.data.split("_")
        level = parts[1]
        topic = parts[2]
        content = section_content(level, topic)
        await callback.message.reply(content, parse_mode="Markdown")
        exercise = generate_one_exercise(level, topic, user_name)
        if exercise and "text" in exercise and "answer" in exercise:
            user_data["exercise_text"] = exercise["text"]
            user_data["exercise_answer"] = exercise["answer"]
            user_data["exercise_topic"] = topic
            user_data["exercise_attempt"] = 0
            save_users(users)
            await callback.message.reply(
                f"📝 *Задание*\n\n{exercise['text']}\n\n_Напиши правильный ответ:_",
                parse_mode="Markdown"
            )
        else:
            await callback.message.reply("❌ Не удалось сгенерировать задание. Попробуйте позже.")
        await callback.answer()
        return

    if callback.data.startswith("vocab_next_"):
        user_data = users.get(user_id, {})
        idx = user_data.get("vocab_index", 0) + 1
        user_data["vocab_index"] = idx
        save_users(users)
        await send_next_vocab_word(callback.message, user_id)
        await callback.answer()
        return

    if callback.data.startswith("vocab_help_"):
        user_data = users.get(user_id, {})
        words = user_data.get("vocab_words", [])
        idx = user_data.get("vocab_index", 0)
        if idx < len(words):
            word = words[idx]
            example = word.get("example", "")
            await callback.message.reply(f"💡 *Пример предложения:*\n{example}")
            await callback.message.reply("📝 *Теперь попробуй сам составить предложение с этим словом.*")
        await callback.answer()
        return

    if callback.data.startswith("vocab_done_"):
        user_data = users.get(user_id, {})
        words = user_data.get("vocab_words", [])
        if len(words) < 4:
            await callback.message.reply("❌ Недостаточно слов для задания. Попробуйте позже.")
            await callback.answer()
            return
        sentences = generate_match_exercise(words)
        if sentences and len(sentences) == 4:
            user_data["vocab_sentences"] = sentences
            user_data["vocab_phase"] = "match"
            save_users(users)
            text = "🧩 *Задание на соответствие*\n\n"
            for i, w in enumerate(words, 1):
                text += f"{i}. {w['word']}\n"
            text += "\n"
            for i, s in enumerate(sentences, 1):
                text += f"{chr(64+i)}. {s}\n"
            text += "\n📝 *Введи 4 цифры в порядке соответствия (например, 1432)*"
            await callback.message.reply(text, parse_mode="Markdown")
        else:
            await callback.message.reply("❌ Не удалось сгенерировать задание. Попробуйте позже.")
        await callback.answer()
        return

    if callback.data.startswith("vocab_final"):
        user_data = users.get(user_id, {})
        user_data["vocab_phase"] = "final"
        save_users(users)
        await callback.message.reply(
            "🗣️ *Финальное задание*\n\n"
            "Произнеси любое предложение с одним из изученных слов.\n"
            "Отправь голосовое сообщение.",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if callback.data.startswith("level_back_"):
        clear_state(user_id)
        level = callback.data.split("_")[2]
        await callback.message.reply(level_intro(level), parse_mode="Markdown", reply_markup=level_menu(level))
        await callback.answer()
        return

    await callback.message.reply("⚠️ Неизвестная команда.")
    await callback.answer()

async def send_next_vocab_word(message: types.Message, user_id: str):
    users = load_users()
    user_data = users.get(user_id, {})
    words = user_data.get("vocab_words", [])
    idx = user_data.get("vocab_index", 0)
    lang = user_data.get("language", "Russian")

    if idx >= len(words):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧩 Задание на соответствие", callback_data="vocab_done_")]
        ])
        await message.reply(
            "📚 *Все слова изучены!*\n\n"
            "Теперь проверь себя — выполни задание на соответствие.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

    word = words[idx]
    text = (
        f"📚 *Слово {idx+1}/{len(words)}*\n\n"
        f"🇬🇧 {word['word']}\n"
        f"🌐 {word.get('translation', '')}\n"
        f"📖 {word.get('definition', '')}\n"
        f"💬 *Пример:* {word.get('example', '')}\n\n"
        f"_Составь своё предложение с этим словом._"
    )

    translation = translate_to_language(text, lang)
    if translation:
        user_translations[user_id] = {"translation": translation}

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Перевести", callback_data="translate")],
        [InlineKeyboardButton(text="💡 Помоги составить предложение", callback_data=f"vocab_help_{idx}")],
        [InlineKeyboardButton(text="➡️ Следующее слово", callback_data="vocab_next_")]
    ])

    await message.reply(text, parse_mode="Markdown", reply_markup=keyboard)

    audio_text = f"{word['word']}. {word.get('example', '')}"
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
        clear_state(user_id)
        await m.reply("🗣️ *Я готов!* Отправь мне текст или голосовое сообщение.", parse_mode="Markdown", reply_markup=main_menu())
        return
    if m.text == "📖 Уроки":
        clear_state(user_id)
        await lesson_cmd(m)
        return
    if m.text == "💎 Подписка":
        clear_state(user_id)
        await upgrade_cmd(m)
        return
    if m.text == "📊 Прогресс":
        clear_state(user_id)
        if is_premium(user_id):
            await m.reply("📊 *Твой прогресс:*\n\n✅ Премиум активен\n📚 Уроков пройдено: 0\n🎯 Следующий уровень: A1", parse_mode="Markdown", reply_markup=main_menu())
        else:
            await m.reply("📊 *Ты на бесплатном тарифе.*\n\n🎤 Осталось голосовых на сегодня: 20\n💎 Купи подписку, чтобы снять лимиты.", parse_mode="Markdown", reply_markup=main_menu())
        return
    if m.text == "🔄 Сброс":
        clear_state(user_id)
        await reset_cmd(m)
        return
    if m.text == "❓ Помощь":
        clear_state(user_id)
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

    # --- Грамматическое задание (текст) ---
    if user_data.get("exercise_text") and user_data.get("exercise_answer"):
        if m.text and not m.text.startswith("/"):
            user_answer = m.text.strip().lower()
            correct_answer = user_data["exercise_answer"].strip().lower()
            attempt = user_data.get("exercise_attempt", 0)
            if user_answer == correct_answer:
                await m.reply(f"✅ *Правильно!* {correct_answer.upper()} — верно! 🎉")
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➡️ Следующий раздел", callback_data=f"level_back_{user_data.get('current_level', 'A1')}")]
                ])
                await m.reply("👇 *Выбери следующий раздел:*", parse_mode="Markdown", reply_markup=keyboard)
                user_data["exercise_text"] = ""
                user_data["exercise_answer"] = ""
                save_users(users)
                return True
            else:
                attempt += 1
                user_data["exercise_attempt"] = attempt
                save_users(users)
                if attempt >= 2:
                    await m.reply(f"❌ *Неправильно.* Правильный ответ: **{correct_answer.upper()}**")
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="➡️ Следующий раздел", callback_data=f"level_back_{user_data.get('current_level', 'A1')}")]
                    ])
                    await m.reply("👇 *Выбери следующий раздел:*", parse_mode="Markdown", reply_markup=keyboard)
                    user_data["exercise_text"] = ""
                    user_data["exercise_answer"] = ""
                    save_users(users)
                    return True
                else:
                    await m.reply("❌ *Неправильно.* Попробуй ещё раз.\n\n_Напиши правильный ответ:_", parse_mode="Markdown")
                    return True
            return True

    # --- Задание на соответствие ---
    if user_data.get("vocab_sentences") and user_data.get("vocab_phase") == "match":
        if m.text and not m.text.startswith("/"):
            answer = m.text.strip()
            if not answer.isdigit() or len(answer) != 4:
                await m.reply("❌ *Неверный формат.* Введи 4 цифры (например, 1432).", parse_mode="Markdown")
                return True
            digits = [int(d) for d in answer]
            if set(digits) != {1, 2, 3, 4}:
                await m.reply("❌ *Неверный формат.* Используй цифры от 1 до 4 без повторений.", parse_mode="Markdown")
                return True
            words = user_data.get("vocab_words", [])
            sentences = user_data.get("vocab_sentences", [])
            correct = all(words[i]['word'].lower() in sentences[digits[i]-1].lower() for i in range(4))
            if correct:
                await m.reply("✅ *Отлично! Все слова подобраны верно!* 🎉")
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗣️ Финальное задание", callback_data="vocab_final")]
                ])
                await m.reply(
                    "📣 *Финальное задание:* произнеси любое предложение с одним из изученных слов.\n\n"
                    "Отправь голосовое сообщение.",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            else:
                errors = []
                for i in range(4):
                    if words[i]['word'].lower() not in sentences[digits[i]-1].lower():
                        errors.append(f"{words[i]['word']} → предложение {chr(64+digits[i])}")
                if errors:
                    await m.reply(f"❌ *Не все слова подобраны верно.*\nОшибки в: {', '.join(errors)}")
                else:
                    await m.reply("❌ *Попробуй ещё раз.*")
            return True

    # --- Вокабуляр: финальное задание (голос) ---
    if user_data.get("vocab_words") and user_data.get("vocab_phase") == "final":
        if m.voice:
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
                words = [w['word'] for w in user_data.get("vocab_words", [])]
                found = [w for w in words if w.lower() in text.lower()]
                if found:
                    await m.reply(f"✅ *Отлично! Ты использовал слова: {', '.join(found)}* 🎉")
                else:
                    await m.reply("❌ *Ты не использовал изученные слова. Попробуй ещё раз.*")
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➡️ Следующий раздел", callback_data=f"level_back_{user_data.get('current_level', 'A1')}")]
                ])
                await m.reply("👇 *Выбери следующий раздел:*", parse_mode="Markdown", reply_markup=keyboard)
                user_data["vocab_words"] = []
                user_data["vocab_phase"] = ""
                save_users(users)
                return True
            except Exception as e:
                logging.error(f"Voice error: {e}")
                await m.reply("❌ Не удалось распознать речь. Попробуй ещё раз.")
                return True
        else:
            await m.reply("🗣️ *Отправь голосовое сообщение с предложением.*")
            return True

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
    last_section = user_data.get("last_section", "")

    # --- АЛФАВИТ ---
    if last_section == "alphabet" and m.text and not m.text.startswith("/"):
        letter = m.text.strip().upper()
        if len(letter) == 1 and letter.isalpha():
            audio_bytes = elevenlabs_tts(letter)
            if audio_bytes:
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                        tmp.write(audio_bytes)
                        path = tmp.name
                    await m.reply_voice(FSInputFile(path))
                    os.unlink(path)
                    await m.reply(f"🔊 *Буква {letter}* — произнесена! Попробуй другую букву или выбери другой раздел.", parse_mode="Markdown")
                except Exception as e:
                    logging.error(f"TTS error: {e}")
            else:
                await m.reply("❌ Не удалось произнести букву. Попробуй другую.")
            return
        else:
            await m.reply("❌ Напиши **одну букву** (например, A, B, C).", parse_mode="Markdown")
            return

    # --- ЦИФРЫ ---
    if last_section == "numbers" and m.text and not m.text.startswith("/"):
        try:
            number = int(m.text.strip())
            if 1 <= number <= 10:
                number_words = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
                word = number_words[number - 1]
                audio_bytes = elevenlabs_tts(word)
                if audio_bytes:
                    try:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                            tmp.write(audio_bytes)
                            path = tmp.name
                        await m.reply_voice(FSInputFile(path))
                        os.unlink(path)
                        await m.reply(f"🔊 *Число {number}* — произнесено! Попробуй другую цифру.", parse_mode="Markdown")
                    except Exception as e:
                        logging.error(f"TTS error: {e}")
                else:
                    await m.reply("❌ Не удалось произнести число. Попробуй ещё раз.")
                return
            else:
                await m.reply("❌ Напиши цифру **от 1 до 10**.")
                return
        except ValueError:
            await m.reply("❌ Напиши **цифру** (например, 5).")
            return

    # --- ОБЫЧНОЕ ОБЩЕНИЕ ---
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
