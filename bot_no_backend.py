import asyncio
import csv
import json
import os
import sqlite3
import logging
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
from crm_upload import CRMUploader, UploadError
from photo_server import ticket, batch_files, start_server


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
DELIVERY_FILE = os.path.join(DATA_DIR, 'delivery.jsonl')


def read_records(path):
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding='utf-8-sig') as stream:
        for line in stream:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    records.append(value)
            except json.JSONDecodeError:
                logging.warning('Skipped damaged history record')
    return records


def record_delivery(local_id, result, error):
    with open(DELIVERY_FILE, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps({'appeal_id': local_id, 'crm_id': result.appeal_id if result else None,
                                'number': result.number if result else None,
                                'status': result.status if result else None,
                                'failed': bool(error)}, ensure_ascii=False) + '\n')


def photo_targets():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(os.path.join(DATA_DIR, 'photo_targets.db'))
    conn.execute('CREATE TABLE IF NOT EXISTS targets (chat_id INTEGER, message_id INTEGER, user_id INTEGER, crm_id INTEGER, number TEXT, language TEXT, PRIMARY KEY(chat_id,message_id))')
    return conn

TEXT = {
    "ru": {
        "choose_language": "Выберите язык / Тілді таңдаңыз:",
        "welcome": (
            "Добро пожаловать в <b>Digital Aqmola 109</b>.\n\n"
            "Здесь вы можете оставить обращение в службу 109."
        ),
        "open_app": "Оставить обращение",
        "history": "История обращений",
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
        "history": "Өтініштер тарихы",
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


def webapp_url(language: str, user_id: int) -> str:
    parsed = urlparse(WEBAPP_URL)
    query = dict(parse_qsl(parsed.query))
    query["lang"] = language
    query['v'] = 'map-photo-remove-20261005'
    endpoint = os.getenv('PHOTO_UPLOAD_URL', '').rstrip('/')
    if endpoint:
        query['upload'] = endpoint
        query['ticket'] = ticket(user_id, BOT_TOKEN)
    return urlunparse(parsed._replace(query=urlencode(query)))


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="language:ru"),
        InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="language:kk"),
    ]])


def main_keyboard(language: str, user_id: int):
    text = TEXT[language]
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=text["open_app"],
                    web_app=WebAppInfo(url=webapp_url(language, user_id))
                )
            ],
            [KeyboardButton(text=text['history'])],
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
        reply_markup=main_keyboard(language, callback.from_user.id)
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
        reply_markup=main_keyboard(language, message.from_user.id)
    )


@dp.message(F.text.in_([TEXT["ru"]["guide"], TEXT["kk"]["guide"]]))
async def guide(message: types.Message):
    language = user_language(message.from_user.id)
    await message.answer(TEXT[language]["guide_text"], reply_markup=main_keyboard(language, message.from_user.id))


@dp.message(Command('history'))
@dp.message(F.text.in_([TEXT['ru']['history'], TEXT['kk']['history']]))
async def history(message: types.Message):
    language = user_language(message.from_user.id)
    if message.chat.type != 'private':
        await message.answer('Откройте историю в личном чате с ботом.' if language == 'ru' else 'Тарихты ботпен жеке чатта ашыңыз.')
        return
    rows = [r for r in read_records(JSONL_FILE) if str(r.get('telegram_user_id')) == str(message.from_user.id)][-10:][::-1]
    if not rows:
        await message.answer('У вас пока нет сохранённых обращений.' if language == 'ru' else 'Сақталған өтініштеріңіз әзірге жоқ.', reply_markup=main_keyboard(language, message.from_user.id))
        return
    deliveries = {r.get('appeal_id'): r for r in read_records(DELIVERY_FILE)}
    await message.answer('Последние обращения:' if language == 'ru' else 'Соңғы өтініштер:', reply_markup=main_keyboard(language, message.from_user.id))
    status_names = {
        'ru': {'1':'Новое','2':'Принято оператором','3':'Назначено исполнителю','4':'В работе','5':'Ожидает уточнения','6':'Возвращено на доработку','7':'Исполнено','8':'Закрыто','9':'Отклонено'},
        'kk': {'1':'Жаңа','2':'Оператор қабылдады','3':'Орындаушыға тағайындалды','4':'Орындалуда','5':'Нақтылауды күтуде','6':'Толықтыруға қайтарылды','7':'Орындалды','8':'Жабылды','9':'Қабылданбады'}
    }
    async def render(row):
        local_id = row.get('appeal_id') or row.get('appealNumber') or '—'
        delivery = deliveries.get(local_id, {})
        number = delivery.get('number') or local_id
        label = 'Статус отправки не сохранён' if language == 'ru' else 'Жіберу мәртебесі сақталмаған'
        if delivery.get('failed'):
            label = 'Не передано в CRM' if language == 'ru' else 'CRM-ге жіберілмеген'
        if delivery.get('crm_id'):
            try:
                detail = await asyncio.wait_for(crm.get_appeal(delivery['crm_id']), timeout=8)
                label = status_names[language].get(str(detail.get('status')), 'Статус обновлён в CRM' if language == 'ru' else 'Мәртебе CRM-де жаңартылған')
                if language == 'ru':
                    label = detail.get('status_name') or label
            except (CRMError, TimeoutError):
                label = 'Передано в CRM; текущий статус недоступен' if language == 'ru' else 'CRM-ге жіберілді; ағымдағы мәртебе қолжетімсіз'
        district = localized_appeal_value(str(row.get('district', '')), language, KK_DISTRICT_NAMES)
        category = localized_appeal_value(str(row.get('category', '')), language, KK_CATEGORY_NAMES)
        values = [f'№ {number} · {label}', str(row.get('created_at') or row.get('saved_at') or row.get('createdAt') or ''), category, district, str(row.get('address') or '')[:500]]
        return '\n'.join(escape(v) for v in values if v)
    for text in await asyncio.gather(*(render(row) for row in rows)):
        await message.answer(text)


@dp.message(F.text.in_([TEXT["ru"]["ideas"], TEXT["kk"]["ideas"]]))
async def start_idea(message: types.Message):
    language = user_language(message.from_user.id)
    IDEA_WAITING_USERS.add(message.from_user.id)
    await message.answer(TEXT[language]["ideas_prompt"], reply_markup=main_keyboard(language, message.from_user.id))


@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    raw_data = message.web_app_data.data

    try:
        appeal = json.loads(raw_data)
    except json.JSONDecodeError:
        appeal = {
            "raw_text": raw_data
        }

    staged_photos = []
    if appeal.get('photoBatch'):
        try:
            staged_photos = batch_files(os.path.join(DATA_DIR, 'photo_batches'), str(appeal['photoBatch']), message.from_user.id)
        except (ValueError, OSError, KeyError):
            await message.answer('Не удалось найти фотографии. Откройте форму заново.' if user_language(message.from_user.id) == 'ru' else 'Фотолар табылмады. Форманы қайта ашыңыз.')
            return
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
                "latitude": saved["location_lat"],
                "longitude": saved["location_lng"],
            })
        except CRMError as exc:
            crm_error = str(exc)
    else:
        crm_error = "CRM не настроена"

    record_delivery(saved['appeal_id'], crm_result, crm_error)
    photo_failures = 0
    if crm_result:
        for path, mime in staged_photos:
            try:
                await CRMUploader().upload(crm_result.appeal_id, path.read_bytes(), path.name, mime)
            except (UploadError, OSError) as exc:
                photo_failures += 1
                logging.warning('Inline photo failed for CRM %s: %s', crm_result.appeal_id, type(exc).__name__)
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

    if staged_photos and crm_result:
        details_text += ('\nФотографии прикреплены.' if language == 'ru' else '\nФотолар тіркелді.') if not photo_failures else ('\nЧасть фото не загрузилась. Можно отправить их ответом на следующее сообщение.' if language == 'ru' else '\nКейбір фотолар жүктелмеді. Оларды келесі хабарламаға жауап ретінде жіберіңіз.')
    await message.answer(result_text + details_text, reply_markup=main_keyboard(language, message.from_user.id))
    if crm_result and (not staged_photos or photo_failures):
        prompt = (f'Чтобы прикрепить фото к обращению № {crm_result.number}, отправьте фото ответом на это сообщение. До 10 МБ на файл.' if language == 'ru' else f'№ {crm_result.number} өтінішке фото тіркеу үшін осы хабарламаға жауап ретінде фото жіберіңіз. Бір файл 10 МБ-тан аспауы тиіс.')
        sent = await message.answer(prompt, reply_markup=types.ForceReply(selective=True))
        with photo_targets() as conn:
            conn.execute('INSERT OR REPLACE INTO targets VALUES (?,?,?,?,?,?)', (sent.chat.id, sent.message_id, message.from_user.id, crm_result.appeal_id, crm_result.number, language))


@dp.message(F.photo | F.document.mime_type.startswith('image/'))
async def attach_photo(message: types.Message):
    reply_id = message.reply_to_message.message_id if message.reply_to_message else 0
    with photo_targets() as conn:
        target = conn.execute('SELECT crm_id,number,language FROM targets WHERE chat_id=? AND message_id=? AND user_id=?', (message.chat.id, reply_id, message.from_user.id)).fetchone()
    if not target:
        lang = user_language(message.from_user.id)
        await message.answer('Отправьте фото ответом на сообщение бота с предложением прикрепить фото к нужной заявке.' if lang == 'ru' else 'Фотоны қажетті өтінішке фото тіркеу туралы бот хабарламасына жауап ретінде жіберіңіз.')
        return
    crm_id, number, language = target
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    try:
        await CRMUploader().upload_telegram(message.bot, crm_id, file_id)
    except UploadError as exc:
        logging.warning('Photo upload failed for CRM appeal %s: %s', crm_id, exc)
        await message.answer('Фото не загрузилось. Проверьте формат (JPEG, PNG, WebP) и размер до 10 МБ. Можно повторить отправку ответом на то же сообщение.' if language == 'ru' else 'Фото жүктелмеді. Форматы JPEG, PNG, WebP және көлемі 10 МБ-тан аспауы тиіс. Сол хабарламаға жауап ретінде қайта жіберуге болады.')
        return
    await message.answer(f'Фото прикреплено к обращению № {number}.' if language == 'ru' else f'Фото № {number} өтінішке тіркелді.')


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
            await message.answer(TEXT[language]["ideas_saved"], reply_markup=main_keyboard(language, message.from_user.id))
            return
    await message.answer(
        TEXT[language]["help"],
        reply_markup=main_keyboard(language, message.from_user.id)
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

    runner = await start_server(BOT_TOKEN, DATA_DIR)
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
