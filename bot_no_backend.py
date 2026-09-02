import asyncio
import csv
import json
import os
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from dotenv import load_dotenv


# =========================
# НАСТРОЙКИ
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://miraisuenagi.github.io/109/").strip()

DATA_DIR = "data"
CSV_FILE = os.path.join(DATA_DIR, "appeals.csv")
JSONL_FILE = os.path.join(DATA_DIR, "appeals.jsonl")


# =========================
# ПРОВЕРКА НАСТРОЕК
# =========================

def validate_settings():
    if not BOT_TOKEN:
        raise ValueError("Укажи BOT_TOKEN от BotFather в файле .env")

    if not WEBAPP_URL or WEBAPP_URL == "ВСТАВЬ_ССЫЛКУ_НА_GITHUB_PAGES":
        raise ValueError("Укажи WEBAPP_URL — ссылку на Mini App")

    if not WEBAPP_URL.startswith("https://"):
        raise ValueError("WEBAPP_URL должен начинаться с https://")


# =========================
# КЛАВИАТУРА
# =========================

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🚨 СООБЩИТЬ О ПРОБЛЕМЕ",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Нажмите большую кнопку ниже"
    )


# =========================
# ФАЙЛЫ
# =========================

def ensure_storage():
    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow([
                "appeal_id",
                "created_at",
                "telegram_user_id",
                "telegram_username",
                "full_name",
                "category",
                "district",
                "urgency",
                "address",
                "description",
                "phone",
                "location_lat",
                "location_lng",
                "photos_count",
                "raw_json"
            ])


def get_value(data: dict, *keys, default=""):
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def normalize_location(data: dict):
    location = data.get("location")

    if isinstance(location, dict):
        lat = location.get("lat") or location.get("latitude") or ""
        lng = location.get("lng") or location.get("lon") or location.get("longitude") or ""
        return lat, lng

    lat = get_value(data, "lat", "latitude", "location_lat")
    lng = get_value(data, "lng", "lon", "longitude", "location_lng")

    return lat, lng


def normalize_photos_count(data: dict):
    photos = data.get("photos")

    if isinstance(photos, list):
        return len(photos)

    return get_value(
        data,
        "photos_count",
        "photosCount",
        "files_count",
        "attachments_count",
        default=0
    )


def save_appeal(appeal: dict, user: types.User):
    ensure_storage()

    now = datetime.now()
    appeal_id = f"SA-{now.strftime('%Y%m%d-%H%M%S')}"

    category = get_value(appeal, "category", "categoryName", "type")
    crm_category = get_value(appeal, "crmCategory", "crm_category", default=category)
    district = get_value(appeal, "district", "region", "city")
    urgency = get_value(appeal, "urgency", "priority")
    address = get_value(appeal, "address", "locationText")
    description = get_value(appeal, "description", "text", "comment")
    phone = get_value(appeal, "phone", "contact", "phoneNumber")

    lat, lng = normalize_location(appeal)
    photos_count = normalize_photos_count(appeal)

    full_name = " ".join(
        part for part in [
            user.first_name,
            user.last_name
        ] if part
    )

    row = {
        "appeal_id": appeal_id,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "telegram_user_id": user.id,
        "telegram_username": user.username or "",
        "full_name": full_name,
        "category": category,
        "crm_category": crm_category,
        "district": district,
        "urgency": urgency,
        "address": address,
        "description": description,
        "phone": phone,
        "location_lat": lat,
        "location_lng": lng,
        "photos_count": photos_count,
        "raw_json": json.dumps(appeal, ensure_ascii=False)
    }

    with open(CSV_FILE, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow([
            row["appeal_id"],
            row["created_at"],
            row["telegram_user_id"],
            row["telegram_username"],
            row["full_name"],
            row["category"],
            row["district"],
            row["urgency"],
            row["address"],
            row["description"],
            row["phone"],
            row["location_lat"],
            row["location_lng"],
            row["photos_count"],
            row["raw_json"]
        ])

    with open(JSONL_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")

    return row


# =========================
# БОТ
# =========================

dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer(
        "Добро пожаловать в <b>Digital Aqmola 109</b>.\n\n"
        "Здесь можно быстро сообщить о проблеме:\n"
        "• мусор\n"
        "• дороги\n"
        "• освещение\n"
        "• вода\n"
        "• отопление\n"
        "• другие вопросы\n\n"
        "👇 Нажмите большую кнопку внизу экрана:\n\n"
        "<b>🚨 СООБЩИТЬ О ПРОБЛЕМЕ</b>",
        reply_markup=main_keyboard()
    )


@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "Чтобы подать обращение, нажмите большую кнопку внизу экрана:\n\n"
        "<b>🚨 СООБЩИТЬ О ПРОБЛЕМЕ</b>",
        reply_markup=main_keyboard()
    )


@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    raw_data = message.web_app_data.data

    try:
        appeal = json.loads(raw_data)
    except json.JSONDecodeError:
        appeal = {
            "raw_text": raw_data
        }

    saved = save_appeal(appeal, message.from_user)

    category = escape(str(saved["category"] or "Не указано"))
    address = escape(str(saved["address"] or "Не указано"))
    description = escape(str(saved["description"] or "Не указано"))
    urgency = escape(str(saved["urgency"] or "Не указано"))
    district = escape(str(saved["district"] or "Не указано"))

    await message.answer(
        "✅ <b>Обращение принято</b>\n\n"
        f"<b>Номер:</b> {saved['appeal_id']}\n"
        f"<b>Категория:</b> {category}\n"
        f"<b>Район/город:</b> {district}\n"
        f"<b>Срочность:</b> {urgency}\n"
        f"<b>Адрес:</b> {address}\n"
        f"<b>Описание:</b> {description}\n\n"
        "Данные сохранены на компьютере в файле:\n"
        f"<code>{CSV_FILE}</code>\n\n"
        "Чтобы подать новое обращение, снова нажмите кнопку внизу.",
        reply_markup=main_keyboard()
    )


@dp.message(F.text)
async def any_text(message: types.Message):
    await message.answer(
        "👇 Чтобы сообщить о проблеме, нажмите большую кнопку внизу экрана:\n\n"
        "<b>🚨 СООБЩИТЬ О ПРОБЛЕМЕ</b>",
        reply_markup=main_keyboard()
    )


async def main():
    validate_settings()
    ensure_storage()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    print("======================================")
    print("Digital Aqmola 109 bot запущен")
    print("Mini App URL:", WEBAPP_URL)
    print("CSV:", CSV_FILE)
    print("======================================")
    print("Окно не закрывать. Пока оно открыто — бот работает.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
