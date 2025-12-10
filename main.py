import os
import logging
import json
import base64
import asyncio
from io import BytesIO

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramNetworkError

from config import BOT_TOKEN, KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY

# --------- Логирование ----------
logging.basicConfig(level=logging.INFO)

# --------- Бот ----------
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ---------- FSM ----------
class Form(StatesGroup):
    choosing_model = State()
    waiting_for_prompt = State()

# ---------- Меню ----------
main_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("Меню")]],
    resize_keyboard=True
)

second_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🖼 Создать изображение")],
        [KeyboardButton("ℹ️ Помощь"), KeyboardButton("👤 Профиль")]
    ],
    resize_keyboard=True
)

model_keyboard = InlineKeyboardMarkup(row_width=1)
model_keyboard.add(
    InlineKeyboardButton("🖌 Кандинский", callback_data="model_kandinsky")
)

API_URL = "https://api-key.fusionbrain.ai/"

def kandinsky_headers():
    return {
        "X-Key": f"Key {KANDINSKY_API_KEY}",
        "X-Secret": f"Secret {KANDINSKY_SECRET_KEY}"
    }

# ---------- Кеш последних изображений ----------
last_images = {}  # user_id -> BytesIO

# ---------- Генерация Кандинского ----------
async def generate_kandinsky(prompt: str) -> BytesIO | None:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(API_URL + "key/api/v1/pipelines", headers=kandinsky_headers()) as resp:
                pipelines = await resp.json()
                pipeline_id = pipelines[0]["id"]

            params = {
                "type": "GENERATE",
                "numImages": 1,
                "width": 1024,
                "height": 1024,
                "generateParams": {"query": prompt}
            }

            data = aiohttp.FormData()
            data.add_field("pipeline_id", pipeline_id)
            data.add_field("params", json.dumps(params), content_type="application/json")

            async with session.post(API_URL + "key/api/v1/pipeline/run", headers=kandinsky_headers(), data=data) as run_resp:
                run = await run_resp.json()
                uuid = run["uuid"]

            for _ in range(30):
                async with session.get(f"{API_URL}key/api/v1/pipeline/status/{uuid}", headers=kandinsky_headers()) as status_resp:
                    status = await status_resp.json()
                    if status["status"] == "DONE":
                        file_base64 = status["result"]["files"][0]
                        return BytesIO(base64.b64decode(file_base64))
                await asyncio.sleep(2)

        except Exception as e:
            logging.error(f"Kandinsky error: {e}")
            return None

    return None

# ---------- Отправка ----------
async def safe_send_photo(chat, img, kb=None):
    for _ in range(3):
        try:
            await chat.send_photo(img, caption="Готово!", reply_markup=kb)
            return
        except TelegramNetworkError:
            await asyncio.sleep(2)
    await chat.send_message("Ошибка при отправке изображения. Попробуй позже.")

async def send_image_with_actions(chat, img, user_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔄 Повторить", callback_data="repeat"),
        InlineKeyboardButton("✨ Новая генерация", callback_data="new")
    )
    last_images[user_id] = BytesIO(img.getvalue())
    await safe_send_photo(chat, img, kb)

# ---------- Хэндлеры ----------
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Привет! Я Nevara — ИИ для генерации изображений ✨\nНажми кнопку «Меню» 👇",
        reply_markup=main_menu
    )

@dp.message(Text("Меню"))
async def show_menu(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=second_menu)

@dp.message(Text("🖼 Создать изображение"))
async def create(message: types.Message, state: FSMContext):
    await message.answer("Выбери модель:", reply_markup=model_keyboard)
    await state.set_state(Form.choosing_model)

@dp.message(Text("ℹ️ Помощь"))
async def help_msg(message: types.Message):
    await message.answer("Чтобы создать изображение, выбери модель и введи описание ✨")

@dp.message(Text("👤 Профиль"))
async def profile(message: types.Message):
    await message.answer("Профиль скоро появится ❤️")

@dp.callback_query(lambda c: c.data == "model_kandinsky", state=Form.choosing_model)
async def choose_model(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(model="model_kandinsky")
    await state.set_state(Form.waiting_for_prompt)
    await callback.message.answer("Напиши описание изображения ✨")
    await callback.answer()

@dp.message(state=Form.waiting_for_prompt)
async def gen(message: types.Message, state: FSMContext):
    data = await state.get_data()
    model = data["model"]
    prompt = message.text

    await message.answer("Генерирую... ⏳")

    img = await generate_kandinsky(prompt)

    if img:
        await send_image_with_actions(message.chat, img, message.from_user.id)
    else:
        await message.answer("Ошибка генерации 😥")

    await state.clear()

@dp.callback_query(lambda c: c.data in ["repeat", "new"])
async def repeat_or_new(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    if callback.data == "repeat":
        img = last_images.get(user_id)
        if not img:
            await callback.message.answer("Нет данных для повторения 😥")
            return
        await callback.answer("Отправляю...")
        await send_image_with_actions(callback.message.chat, img, user_id)

    else:
        await state.set_state(Form.choosing_model)
        await callback.message.answer("Выбери модель:", reply_markup=model_keyboard)
        await callback.answer()

# ---------- RUN ----------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
