import os
import logging
import requests
import json
import base64
import asyncio
from io import BytesIO

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.utils.exceptions import NetworkError

# --------- Логирование ----------
logging.basicConfig(level=logging.INFO)

# --------- Токены из Render переменных ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
KANDINSKY_API_KEY = os.environ.get("KANDINSKY_API_KEY")
KANDINSKY_SECRET_KEY = os.environ.get("KANDINSKY_SECRET_KEY")
HUGGINGFACE_API_TOKEN = os.environ.get("HUGGINGFACE_API_TOKEN")

# --------- Бот ----------
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=storage)


# ---------- FSM ----------
class Form(StatesGroup):
    choosing_model = State()
    waiting_for_prompt = State()


# ---------- Главное меню ----------
main_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("Меню")]],
    resize_keyboard=True
)

# ---------- Второй уровень ----------
second_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🖼 Создать изображение")],
        [KeyboardButton("ℹ️ Помощь"), KeyboardButton("👤 Профиль")]
    ],
    resize_keyboard=True
)

# ---------- Кнопки выбора модели ----------
model_keyboard = InlineKeyboardMarkup(row_width=2)
model_keyboard.add(
    InlineKeyboardButton("🖌 Кандинский", callback_data="model_kandinsky"),
    InlineKeyboardButton("🎨 Stable Diffusion", callback_data="model_sd")
)


# ---------- Кандинский API ----------
API_URL = "https://api-key.fusionbrain.ai/"


def kandinsky_headers():
    return {
        "X-Key": f"Key {KANDINSKY_API_KEY}",
        "X-Secret": f"Secret {KANDINSKY_SECRET_KEY}"
    }


def generate_kandinsky(prompt):
    try:
        pipeline = requests.get(API_URL + "key/api/v1/pipelines", headers=kandinsky_headers()).json()[0]["id"]

        params = {
            "type": "GENERATE",
            "numImages": 1,
            "width": 1024,
            "height": 1024,
            "generateParams": {"query": prompt}
        }

        data = {
            "pipeline_id": (None, pipeline),
            "params": (None, json.dumps(params), "application/json")
        }

        run = requests.post(API_URL + "key/api/v1/pipeline/run", headers=kandinsky_headers(), files=data).json()
        uuid = run["uuid"]

        import time
        for _ in range(30):
            status = requests.get(API_URL + "key/api/v1/pipeline/status/" + uuid,
                                  headers=kandinsky_headers()).json()
            if status["status"] == "DONE":
                file_base64 = status["result"]["files"][0]
                return base64.b64decode(file_base64)
            time.sleep(2)
    except:
        return None

    return None


# ---------- Stable Diffusion ----------
def generate_sd(prompt):
    try:
        url = "https://api-inference.huggingface.co/models/CompVis/stable-diffusion-v1-4"
        headers = {"Authorization": f"Bearer {HUGGINGFACE_API_TOKEN}"}
        payload = {"inputs": prompt}

        r = requests.post(url, headers=headers, json=payload)
        if r.status_code == 200:
            return BytesIO(r.content)
        else:
            return None
    except:
        return None


# ---------- Безопасная отправка ----------
async def safe_send_photo(message, img, kb=None):
    for _ in range(3):
        try:
            await message.answer_photo(img, caption="Готово!", reply_markup=kb)
            return
        except NetworkError:
            await asyncio.sleep(2)
    await message.answer("Ошибка при отправке изображения. Попробуй позже.")


# ---------- Отправка картинок с кнопками ----------
async def send_image_with_actions(message, img):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔄 Повторить", callback_data="repeat"),
        InlineKeyboardButton("✨ Новая генерация", callback_data="new")
    )
    await safe_send_photo(message, img, kb)


# ---------- START ----------
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.answer(
        "Привет! Я Nevara — ИИ для генерации изображений ✨\n"
        "Нажми кнопку «Меню» 👇",
        reply_markup=main_menu
    )


# ---------- Второй уровень ----------
@dp.message_handler(lambda m: m.text == "Меню")
async def show_menu(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=second_menu)


@dp.message_handler(lambda m: m.text == "🖼 Создать изображение")
async def create(message: types.Message):
    await message.answer("Выбери модель:", reply_markup=model_keyboard)
    await Form.choosing_model.set()


# ---------- Помощь и профиль ----------
@dp.message_handler(lambda m: m.text == "ℹ️ Помощь")
async def help_msg(message: types.Message):
    await message.answer("Чтобы создать изображение, выбери модель и введи описание.")


@dp.message_handler(lambda m: m.text == "👤 Профиль")
async def profile(message: types.Message):
    await message.answer("Профиль в разработке ❤️")


# ---------- Обработка выбора модели ----------
@dp.callback_query_handler(lambda c: c.data in ["model_kandinsky", "model_sd"], state=Form.choosing_model)
async def choose_model(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(model=callback.data)
    await Form.waiting_for_prompt.set()
    await bot.send_message(callback.from_user.id, "Напиши описание изображения ✨")
    await callback.answer()


# ---------- Генерация ----------
@dp.message_handler(state=Form.waiting_for_prompt)
async def gen(message: types.Message, state: FSMContext):
    data = await state.get_data()
    model = data["model"]
    prompt = message.text

    await state.update_data(last_model=model, last_prompt=prompt)

    await message.answer("Генерирую... ⏳")

    if model == "model_kandinsky":
        img = generate_kandinsky(prompt)
    else:
        img = generate_sd(prompt)

    if img:
        await send_image_with_actions(message, img)
    else:
        await message.answer("Ошибка генерации 😥")

    await state.finish()


# ---------- Повторить / Новая ----------
@dp.callback_query_handler(lambda c: c.data in ["repeat", "new"])
async def repeat_or_new(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    if callback.data == "repeat":
        data = await state.get_data()
        model = data.get("last_model")
        prompt = data.get("last_prompt")

        if not model or not prompt:
            await bot.send_message(user_id, "Нет данных для повторения 😥")
            return

        await callback.answer("Генерирую снова...")

        if model == "model_kandinsky":
            img = generate_kandinsky(prompt)
        else:
            img = generate_sd(prompt)

        if img:
            await send_image_with_actions(callback.message, img)
        else:
            await callback.message.answer("Ошибка генерации 😥")

    else:  # new
        await Form.choosing_model.set()
        await bot.send_message(user_id, "Выбери модель:", reply_markup=model_keyboard)
        await callback.answer()


# ---------- RUN ----------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)


