import asyncio
import csv
import json
import os
from datetime import datetime
from html import escape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from dotenv import load_dotenv

from crm import CRMClient, CRMError


# =========================
# НАСТРОЙКИ
# =========================

load_dotenv(override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://miraisuenagi.github.io/109/").strip()

DATA_DIR = "data"
CSV_FILE = os.path.join(DATA_DIR, "appeals.csv")
JSONL_FILE = os.path.join(DATA_DIR, "appeals.jsonl")
IDEAS_FILE = os.path.join(DATA_DIR, "citizen_ideas.jsonl")

TEXT = {
    "ru": {
        "choose_language": "Выберите язык / Тілді таңдаңыз:",
        "welcome": (
            "Добро пожаловать в <b>Digital Aqmola 109</b>.\n\n"
            "Здесь вы можете оставить обращение в службу 109."
        ),
        "open_app": "Оставить обращение",
        "ideas": "Идеи граждан",
        "ideas_prompt": "Напишите вашу идею или предложение для города одним сообщением.",
        "ideas_saved": "Спасибо! Ваша идея сохранена и будет рассмотрена.",
        "guide": "Как подать обращение",
        "guide_text": (
            "Как подать обращение:\n\n"
            "1. Нажмите «Оставить обращение».\n"
            "2. Выберите категорию проблемы.\n"
            "3. Укажите район, адрес и описание.\n"
            "4. При необходимости прикрепите фото.\n"
            "5. Укажите имя, фамилию и телефон, затем отправьте обращение."
        ),
        "placeholder": "Открыть форму обращения",
        "help": "Для смены языка используйте команду /language.",
    },
    "kk": {
        "choose_language": "Тілді таңдаңыз / Выберите язык:",
        "welcome": (
            "<b>Digital Aqmola 109</b> жүйесіне қош келдіңіз.\n\n"
            "Мұнда 109 қызметіне өтініш қалдыра аласыз."
        ),
        "open_app": "Өтініш қалдыру",
        "ideas": "Азаматтар идеясы",
        "ideas_prompt": "Қалаға қатысты идеяңызды немесе ұсынысыңызды бір хабарламада жазыңыз.",
        "ideas_saved": "Рақмет! Идеяңыз сақталды және қарастырылады.",
        "guide": "Өтінішті қалай жіберу керек?",
        "guide_text": (
            "Өтінішті қалай жіберуге болады:\n\n"
            "1. «Өтініш қалдыру» батырмасын басыңыз.\n"
            "2. Мәселе санатын таңдаңыз.\n"
            "3. Ауданды, мекенжайды және сипаттаманы жазыңыз.\n"
            "4. Қажет болса, фото тіркеңіз.\n"
            "5. Аты-жөніңіз бен телефон нөміріңізді көрсетіп, өтінішті жіберіңіз."
        ),
        "placeholder": "Өтініш формасын ашу",
        "help": "Тілді өзгерту үшін /language пәрменін қолданыңыз.",
    },
}
USER_LANGUAGES: dict[int, str] = {}
IDEA_WAITING_USERS: set[int] = set()
KK_CATEGORY_NAMES = {
    "Вывоз мусора": "Қоқыс шығару", "Дороги": "Жолдар", "Электроснабжение": "Жарықтандыру",
    "Водоснабжение": "Сумен жабдықтау", "Отопление": "Жылумен жабдықтау", "Канализация": "Кәріз",
    "Благоустройство двора": "Ауланы абаттандыру", "Другое": "Басқа",
}
KK_DISTRICT_NAMES = {
    "г. Кокшетау": "Көкшетау қ.", "г. Степногорск": "Степногорск қ.", "г. Косшы": "Қосшы қ.",
    "г. Щучинск": "Щучинск қ.", "Аккольский район": "Ақкөл ауданы", "Аршалынский район": "Аршалы ауданы",
    "Астраханский район": "Астрахан ауданы", "Атбасарский район": "Атбасар ауданы", "Буландынский район": "Бұланды ауданы",
    "Бурабайский район": "Бурабай ауданы", "Егиндыкольский район": "Егіндікөл ауданы", "Ерейментауский район": "Ерейментау ауданы",
    "Есильский район": "Есіл ауданы", "Жаксынский район": "Жақсы ауданы", "Жаркаинский район": "Жарқайын ауданы",
    "Зерендинский район": "Зеренді ауданы", "Коргалжынский район": "Қорғалжын ауданы", "Сандыктауский район": "Сандықтау ауданы",
    "Целиноградский район": "Целиноград ауданы", "Шортандинский район": "Шортанды ауданы",
}


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

def user_language(user_id: int) -> str:
    return USER_LANGUAGES.get(user_id, "ru")


def localized_appeal_value(value: str, language: str, values: dict[str, str]) -> str:
    return values.get(value, value) if language == "kk" else value


def webapp_url(language: str) -> str:
    parsed = urlparse(WEBAPP_URL)
    query = dict(parse_qsl(parsed.query))
    query["lang"] = language
    return urlunparse(parsed._replace(query=urlencode(query)))


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="language:ru"),
        InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="language:kk"),
    ]])


def main_keyboard(language: str):
    text = TEXT[language]
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=text["open_app"],
                    web_app=WebAppInfo(url=webapp_url(language))
                )
            ],
            [KeyboardButton(text=text["guide"])]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder=text["placeholder"]
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
crm = CRMClient()


@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer(
        TEXT["ru"]["choose_language"],
        reply_markup=language_keyboard()
    )


@dp.callback_query(F.data.startswith("language:"))
async def select_language(callback: types.CallbackQuery):
    language = callback.data.rsplit(":", maxsplit=1)[-1]
    if language not in TEXT:
        await callback.answer()
        return
    USER_LANGUAGES[callback.from_user.id] = language
    await callback.answer()
    await callback.message.answer(
        TEXT[language]["welcome"],
        reply_markup=main_keyboard(language)
    )


@dp.message(Command("language"))
async def language_command(message: types.Message):
    await message.answer(
        TEXT[user_language(message.from_user.id)]["choose_language"],
        reply_markup=language_keyboard()
    )


@dp.message(Command("help"))
async def help_command(message: types.Message):
    language = user_language(message.from_user.id)
    await message.answer(
        TEXT[language]["help"],
        reply_markup=main_keyboard(language)
    )


@dp.message(F.text.in_([TEXT["ru"]["guide"], TEXT["kk"]["guide"]]))
async def guide(message: types.Message):
    language = user_language(message.from_user.id)
    await message.answer(TEXT[language]["guide_text"], reply_markup=main_keyboard(language))


@dp.message(F.text.in_([TEXT["ru"]["ideas"], TEXT["kk"]["ideas"]]))
async def start_idea(message: types.Message):
    language = user_language(message.from_user.id)
    IDEA_WAITING_USERS.add(message.from_user.id)
    await message.answer(TEXT[language]["ideas_prompt"], reply_markup=main_keyboard(language))


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

    crm_result = None
    crm_error = None
    if crm.configured:
        try:
            crm_result = await crm.create_appeal({
                "applicant_phone": saved["phone"],
                "applicant_name": get_value(appeal, "name", "applicant_name", default=saved["full_name"]),
                "telegram_user_id": saved["telegram_user_id"],
                "category": saved["crm_category"] or saved["category"],
                "district": saved["district"],
                "address": saved["address"],
                "description": saved["description"],
            })
        except CRMError as exc:
            crm_error = str(exc)
    else:
        crm_error = "CRM не настроена"

    language = user_language(message.from_user.id)
    category_value = localized_appeal_value(str(saved["category"] or ""), language, KK_CATEGORY_NAMES)
    district_value = localized_appeal_value(str(saved["district"] or ""), language, KK_DISTRICT_NAMES)
    category = escape(category_value or ("Көрсетілмеген" if language == "kk" else "Не указано"))
    address = escape(str(saved["address"] or ("Көрсетілмеген" if language == "kk" else "Не указано")))
    description = escape(str(saved["description"] or ("Көрсетілмеген" if language == "kk" else "Не указано")))
    urgency = escape("Қалыпты" if language == "kk" else str(saved["urgency"] or "Не указано"))
    district = escape(district_value or ("Көрсетілмеген" if language == "kk" else "Не указано"))

    if language == "kk":
        result_text = (
            "✅ <b>Өтініш қабылданып, CRM 109 жүйесіне жіберілді</b>\n\n"
            f"<b>CRM нөмірі:</b> {escape(crm_result.number)}\n"
            if crm_result else
            "⚠️ <b>Өтініш сақталды, бірақ CRM-ге әлі жіберілмеді</b>\n\n"
            f"<b>Жергілікті нөмір:</b> {saved['appeal_id']}\n"
        )
        if crm_error:
            result_text += "<b>Мәртебесі:</b> қайта жіберуді күтуде\n"
        details_text = (
            f"<b>Санат:</b> {category}\n"
            f"<b>Аудан/қала:</b> {district}\n"
            f"<b>Маңыздылығы:</b> {urgency}\n"
            f"<b>Мекенжай:</b> {address}\n"
            f"<b>Сипаттама:</b> {description}\n\n"
            "Жаңа өтініш қалдыру үшін төмендегі батырманы басыңыз."
        )
    else:
        result_text = (
            "✅ <b>Обращение принято и передано в CRM 109</b>\n\n"
            f"<b>Номер CRM:</b> {escape(crm_result.number)}\n"
            if crm_result else
            "⚠️ <b>Обращение сохранено, но пока не передано в CRM</b>\n\n"
            f"<b>Локальный номер:</b> {saved['appeal_id']}\n"
        )
        if crm_error:
            result_text += "<b>Статус:</b> ожидает повторной отправки\n"
        details_text = (
            f"<b>Категория:</b> {category}\n"
            f"<b>Район/город:</b> {district}\n"
            f"<b>Срочность:</b> {urgency}\n"
            f"<b>Адрес:</b> {address}\n"
            f"<b>Описание:</b> {description}\n\n"
            "Чтобы подать новое обращение, снова нажмите кнопку внизу."
        )

    await message.answer(result_text + details_text, reply_markup=main_keyboard(language))


@dp.message(F.text)
async def any_text(message: types.Message):
    language = user_language(message.from_user.id)
    if message.from_user.id in IDEA_WAITING_USERS:
        idea = message.text.strip()
        if idea:
            ensure_storage()
            with open(IDEAS_FILE, "a", encoding="utf-8") as file:
                file.write(json.dumps({
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "telegram_user_id": message.from_user.id,
                    "language": language,
                    "text": idea,
                }, ensure_ascii=False) + "\n")
            IDEA_WAITING_USERS.discard(message.from_user.id)
            await message.answer(TEXT[language]["ideas_saved"], reply_markup=main_keyboard(language))
            return
    await message.answer(
        TEXT[language]["help"],
        reply_markup=main_keyboard(language)
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
