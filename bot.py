#!/usr/bin/env python3
"""
Qulay Bot — напарник к Mini App (app.html)

Работает с той же Firebase Realtime Database и той же схемой заявок,
что и Mini App:
    status: open -> taken -> arrived -> picked -> done  (или cancelled)
    поля:   house, entrance, floor, flat, note, bags, when,
            clientId, clientName, clientPhone,
            volunteerId, volunteerName, volunteerPhone,
            createdAt, takenAt, arrivedAt, pickedAt, doneAt

Живая синхронизация в обе стороны через db.reference("orders").listen():
  - Жилец создаёт заявку в Mini App  -> волонтёры "на связи" в боте получают пуш
  - Волонтёр берёт заявку в боте      -> жилец в Mini App видит "волонтёр в пути"
  - Жилец создаёт заявку в боте       -> волонтёры в Mini App видят её как обычно
  - Волонтёр берёт заявку в Mini App  -> если жилец из Telegram, бот пришлёт ему статус

Переменные окружения (уже настроены на Railway):
    BOT_TOKEN, FIREBASE_DB_URL, FIREBASE_SERVICE_ACCOUNT_JSON

requirements.txt должен содержать:
    python-telegram-bot>=20.0
    firebase-admin
"""

import asyncio
import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime

import base64
import io

import firebase_admin
from firebase_admin import credentials, db

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo,
    MenuButtonWebApp
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_URL = os.environ["FIREBASE_DB_URL"]
FIREBASE_CREDS_JSON = os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"]
# Базовая ссылка на Mini App (GitHub Pages). Роль передаём через ?role=volunteer
APP_URL = os.environ.get("APP_URL", "https://karivv.github.io/qulay-app/")
# Telegram ID организаторов через запятую (узнать свой ID — команда /myid).
# Только они могут выпускать коды приглашения для волонтёров.
ADMIN_IDS = {x.strip() for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
POINTS_PER_ORDER = 20  # очков волонтёру за одну закрытую заявку

cred = credentials.Certificate(json.loads(FIREBASE_CREDS_JSON))
firebase_admin.initialize_app(cred, {"databaseURL": DB_URL})

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

HOUSE, ENTRANCE, FLOOR, FLAT, NOTE, BAGS = range(6)

# ================= ЯЗЫКИ =================
# Все тексты, которые видит обычный пользователь, живут здесь — по одному
# ключу на фразу, ru и uz рядом. Консоль организатора (/admin, /stats,
# /orders, /user, /chats) намеренно осталась русской: её видит только
# организатор, и переводить её — лишний код без пользы.
LANGS = ("ru", "uz")
DEFAULT_LANG = "ru"

T = {
"ru": {
    # выбор языка
    "lang_saved": "Готово! Язык — русский.",
    "lang_ask": "Выберите язык / Tilni tanlang",
    "btn_lang_ru": "Русский",
    "btn_lang_uz": "O‘zbekcha",
    "lang_hint": "Сменить язык можно командой /lang",
    # старт и регистрация
    "welcome_back": "С возвращением, {name}!",
    "app_cta": "Заявки и статус — прямо в приложении:",
    "hello": "Привет! Это Qulay — вывоз мусора с помощью волонтёров.\n\n"
             "Поделитесь номером, чтобы продолжить:",
    "btn_share_phone": "📱 Поделиться номером",
    "share_own_phone": "Пожалуйста, поделитесь именно своим номером.",
    "ask_name": "Как вас записать? Жильцы и волонтёры увидят именно это имя.",
    "name_short": "Имя слишком короткое, напишите ещё раз.",
    "ask_house": "В каком доме вы живёте? (номер или название)",
    "ask_district": "В каком районе/махалле вы обычно волонтёрите?",
    "place_short": "Напишите хотя бы коротко.",
    "reg_done": "Готово, {name}! Роль: {role}",
    "role_volunteer": "Волонтёр",
    "role_client": "Жилец",
    "open_here": "Открывайте заявки прямо здесь:",
    "btn_open_app": "📦 Открыть приложение",
    "btn_open_app_vol": "🚶 Открыть приложение",
    # приглашения
    "already_volunteer": "Вы уже волонтёр — код не нужен.",
    "code_bad": "Код приглашения недействителен или уже использован.\n"
                "Чтобы стать волонтёром, попросите новый код у организатора и наберите:\n"
                "/start vol_КОД",
    "code_ok_upgrade": "Код принят! Теперь вы волонтёр — история ваших заявок как жителя "
                       "никуда не денется, просто интерфейс переключится на волонтёрский.\n\n"
                       "В каком районе/махалле вы обычно будете волонтёрить?",
    "upgrade_done": "Готово! Теперь вы волонтёр в районе «{district}».",
    "someone_joined": "🏡 По вашей ссылке зарегистрировался {name} · {phone}",
    # кнопки меню
    "btn_new_order": "📦 Оставить заявку",
    "btn_my_orders": "📋 Мои заявки",
    "btn_onair": "🟢 Вы на связи",
    "btn_orders_near": "🗺 Заявки рядом",
    "btn_my_jobs": "📦 Мои заявки",
    # создание заявки
    "closed_now": "🌙 Сейчас закрыто. {hours}.\n"
                  "Оставьте заявку утром — с {open_h}:00 волонтёры снова на связи.",
    "ask_house_num": "Номер дома?",
    "ask_entrance": "Подъезд?",
    "ask_floor": "Этаж?",
    "ask_flat": "Квартира?",
    "ask_note": "Комментарий (например, код домофона). Если нет — «-»",
    "ask_bags": "Что выносим?",
    "order_sent": "Заявка отправлена волонтёрам ✅",
    "cancelled": "Отменено.",
    "no_orders": "Пока нет заявок.",
    "no_jobs": "Пока нет принятых заявок.",
    # виды мусора
    "bag_one": "Один пакет",
    "bag_few": "Два-три пакета",
    "bag_big": "Крупный мусор",
    "bag_glass": "Стекло / банки",
    # волонтёр
    "onair_on": "Вы на связи 🟢 — пришлём уведомление о новой заявке",
    "onair_off": "Уведомления выключены 🔕",
    "no_open_orders": "Открытых заявок сейчас нет.",
    "btn_take": "✅ Взять заявку",
    "took_it": "Заявка ваша ✓",
    "too_late": "Заявку уже взял другой волонтёр",
    "taken_note": "❌ Уже занято",
    "btn_arrived": "🚪 Я на месте",
    "btn_picked": "📦 Пакет забрал",
    "btn_drop": "↩️ Не смогу выполнить",
    "btn_done": "✅ Готово",
    "dropped": "Заявка возвращена",
    "dropped_note": "↩️ Вы вернули заявку другим волонтёрам",
    "drop_too_late": "Эту заявку уже нельзя вернуть",
    "done_ok": "Готово ✓",
    "done_note": "✅ Заявка закрыта. Спасибо!",
    # адрес заявки
    "addr_short": "Дом {house}",
    "addr_full": "Дом {house}, кв. {flat}",
    "addr_entrance": "Подъезд {entrance}, этаж {floor}",
    "addr_locked": "🔒 Точный адрес — после того, как возьмёте заявку",
    # статусы
    "st_open": "🟡 Ищем волонтёра",
    "st_taken": "🟢 Волонтёр в пути",
    "st_arrived": "🚪 Волонтёр у двери",
    "st_picked": "📦 Несёт до контейнера",
    "st_done": "✅ Готово",
    "st_cancelled": "❌ Отменена",
    # уведомления
    "new_order": "🔔 Новая заявка рядом",
    "order_free": "🔁 Заявка снова свободна",
    "order_due": "🕓 Скоро время заявки",
    "order_help": "🔔 Заявку никто не взял — нужна помощь",
    "vol_gone": "🔎 Волонтёр не смог прийти — ищем другого.",
    "client_cancelled": "❌ Житель отменил заявку — идти не нужно.",
    "btn_open_order": "📱 Открыть заявку",
    "closed_cancel": "🌙 Извините, волонтёр не нашёлся до {close_h}:00.\n"
                     "{hours} — оставьте заявку утром, с утра волонтёров обычно больше.",
    "orphan_cancel": "⌛️ Заявку закрыли — за сутки никто не смог её взять.\n"
                     "Попробуйте оставить новую — волонтёров бывает больше по вечерам.",
    "hours_text": "Сервис работает с {open_h}:00 до {close_h}:00",
    # связь с организатором и между сторонами
    "msg_from": "💬 {who} {name}:",
    "who_client": "Житель",
    "who_volunteer": "Волонтёр",
    "admin_says": "✉️ Организатор Qulay:",
    "admin_says_reply": "✉️ Организатор Qulay:\n\n{text}\n\n"
                        "— Можете ответить прямо здесь, просто напишите сообщение.",
    "admin_online": "✉️ Организатор Qulay на связи — напишите, что случилось. "
                    "Просто отправьте сообщение сюда.",
    "admin_closed": "✅ Организатор завершил разговор. Спасибо!",
    "passed_on": "Передал организатору ✓",
    "no_session": "Я передаю сообщения организатору только когда разговор открыт.\n"
                  "Если что-то случилось во время заявки — нажмите «Что-то не так» "
                  "в приложении, и организатор напишет вам сюда.",
    # блокировка
    "blocked": "⛔️ Организатор временно закрыл вам доступ к Qulay.",
    "unblocked": "✅ Доступ к Qulay возвращён.",
    "admin_cancelled": "❌ Организатор отменил заявку.",
    "code_ready": "✅ Код для {name} готов.\nПерешлите ему эту ссылку:\n{link}",
    "code_declined": "Организатор пока не выдал код для {name}.",
    "your_id": "Ваш Telegram ID: `{id}`",
},
"uz": {
    "lang_saved": "Tayyor! Til — o‘zbekcha.",
    "lang_ask": "Выберите язык / Tilni tanlang",
    "btn_lang_ru": "Русский",
    "btn_lang_uz": "O‘zbekcha",
    "lang_hint": "Tilni /lang buyrug‘i bilan o‘zgartirsa bo‘ladi",
    "welcome_back": "Xush kelibsiz, {name}!",
    "app_cta": "Buyurtmalar va holat — ilovada:",
    "hello": "Salom! Bu Qulay — ko‘ngillilar yordamida chiqindi chiqarish.\n\n"
             "Davom etish uchun raqamingizni yuboring:",
    "btn_share_phone": "📱 Raqamni yuborish",
    "share_own_phone": "Iltimos, aynan o‘z raqamingizni yuboring.",
    "ask_name": "Ismingizni qanday yozamiz? Yashovchilar va ko‘ngillilar shu ismni ko‘radi.",
    "name_short": "Ism juda qisqa, yana bir bor yozing.",
    "ask_house": "Qaysi uyda yashaysiz? (raqami yoki nomi)",
    "ask_district": "Odatda qaysi tuman/mahallada ko‘ngillilik qilasiz?",
    "place_short": "Hech bo‘lmasa qisqacha yozing.",
    "reg_done": "Tayyor, {name}! Rol: {role}",
    "role_volunteer": "Ko‘ngilli",
    "role_client": "Yashovchi",
    "open_here": "Buyurtmalarni shu yerdan oching:",
    "btn_open_app": "📦 Ilovani ochish",
    "btn_open_app_vol": "🚶 Ilovani ochish",
    "already_volunteer": "Siz allaqachon ko‘ngillisiz — kod kerak emas.",
    "code_bad": "Taklif kodi yaroqsiz yoki allaqachon ishlatilgan.\n"
                "Ko‘ngilli bo‘lish uchun tashkilotchidan yangi kod so‘rang va yozing:\n"
                "/start vol_KOD",
    "code_ok_upgrade": "Kod qabul qilindi! Endi siz ko‘ngillisiz — yashovchi sifatidagi "
                       "buyurtmalar tarixingiz saqlanib qoladi, faqat interfeys "
                       "ko‘ngillinikiga o‘zgaradi.\n\n"
                       "Odatda qaysi tuman/mahallada ko‘ngillilik qilasiz?",
    "upgrade_done": "Tayyor! Endi siz «{district}» hududida ko‘ngillisiz.",
    "someone_joined": "🏡 Havolangiz orqali {name} ro‘yxatdan o‘tdi · {phone}",
    "btn_new_order": "📦 Buyurtma qoldirish",
    "btn_my_orders": "📋 Mening buyurtmalarim",
    "btn_onair": "🟢 Aloqadasiz",
    "btn_orders_near": "🗺 Yaqindagi buyurtmalar",
    "btn_my_jobs": "📦 Mening ishlarim",
    "closed_now": "🌙 Hozir yopiq. {hours}.\n"
                  "Buyurtmani ertalab qoldiring — soat {open_h}:00 dan ko‘ngillilar yana aloqada.",
    "ask_house_num": "Uy raqami?",
    "ask_entrance": "Podez?",
    "ask_floor": "Qavat?",
    "ask_flat": "Xonadon?",
    "ask_note": "Izoh (masalan, domofon kodi). Bo‘lmasa — «-»",
    "ask_bags": "Nima chiqaramiz?",
    "order_sent": "Buyurtma ko‘ngillilarga yuborildi ✅",
    "cancelled": "Bekor qilindi.",
    "no_orders": "Hozircha buyurtma yo‘q.",
    "no_jobs": "Hozircha olingan buyurtma yo‘q.",
    "bag_one": "Bitta paket",
    "bag_few": "Ikki-uchta paket",
    "bag_big": "Yirik chiqindi",
    "bag_glass": "Shisha / bankalar",
    "onair_on": "Siz aloqadasiz 🟢 — yangi buyurtma haqida xabar yuboramiz",
    "onair_off": "Bildirishnomalar o‘chirildi 🔕",
    "no_open_orders": "Hozir ochiq buyurtmalar yo‘q.",
    "btn_take": "✅ Buyurtmani olish",
    "took_it": "Buyurtma sizniki ✓",
    "too_late": "Buyurtmani boshqa ko‘ngilli olib ulgurdi",
    "taken_note": "❌ Allaqachon band",
    "btn_arrived": "🚪 Yetib keldim",
    "btn_picked": "📦 Paketni oldim",
    "btn_drop": "↩️ Bajara olmayman",
    "btn_done": "✅ Tayyor",
    "dropped": "Buyurtma qaytarildi",
    "dropped_note": "↩️ Buyurtmani boshqa ko‘ngillilarga qaytardingiz",
    "drop_too_late": "Bu buyurtmani endi qaytarib bo‘lmaydi",
    "done_ok": "Tayyor ✓",
    "done_note": "✅ Buyurtma yopildi. Rahmat!",
    "addr_short": "{house}-uy",
    "addr_full": "{house}-uy, {flat}-xonadon",
    "addr_entrance": "{entrance}-podez, {floor}-qavat",
    "addr_locked": "🔒 Aniq manzil — buyurtmani olganingizdan keyin",
    "st_open": "🟡 Ko‘ngilli qidiryapmiz",
    "st_taken": "🟢 Ko‘ngilli yo‘lda",
    "st_arrived": "🚪 Ko‘ngilli eshik oldida",
    "st_picked": "📦 Konteynerga olib ketyapti",
    "st_done": "✅ Tayyor",
    "st_cancelled": "❌ Bekor qilingan",
    "new_order": "🔔 Yaqinda yangi buyurtma",
    "order_free": "🔁 Buyurtma yana bo‘sh",
    "order_due": "🕓 Buyurtma vaqti yaqinlashdi",
    "order_help": "🔔 Buyurtmani hech kim olmadi — yordam kerak",
    "vol_gone": "🔎 Ko‘ngilli kela olmadi — boshqasini qidiryapmiz.",
    "client_cancelled": "❌ Yashovchi buyurtmani bekor qildi — borish shart emas.",
    "btn_open_order": "📱 Buyurtmani ochish",
    "closed_cancel": "🌙 Uzr, soat {close_h}:00 gacha ko‘ngilli topilmadi.\n"
                     "{hours} — buyurtmani ertalab qoldiring, ertalab ko‘ngillilar ko‘proq bo‘ladi.",
    "orphan_cancel": "⌛️ Buyurtma yopildi — bir kun davomida uni hech kim ola olmadi.\n"
                     "Yangisini qoldirib ko‘ring — kechqurun ko‘ngillilar ko‘proq bo‘ladi.",
    "hours_text": "Xizmat {open_h}:00 dan {close_h}:00 gacha ishlaydi",
    "msg_from": "💬 {who} {name}:",
    "who_client": "Yashovchi",
    "who_volunteer": "Ko‘ngilli",
    "admin_says": "✉️ Qulay tashkilotchisi:",
    "admin_says_reply": "✉️ Qulay tashkilotchisi:\n\n{text}\n\n"
                        "— Shu yerda javob yozishingiz mumkin, shunchaki xabar yuboring.",
    "admin_online": "✉️ Qulay tashkilotchisi aloqada — nima bo‘lganini yozing. "
                    "Shunchaki shu yerga xabar yuboring.",
    "admin_closed": "✅ Tashkilotchi suhbatni yakunladi. Rahmat!",
    "passed_on": "Tashkilotchiga yetkazdim ✓",
    "no_session": "Men tashkilotchiga xabarlarni faqat suhbat ochiq bo‘lganda yetkazaman.\n"
                  "Buyurtma paytida biror narsa bo‘lsa — ilovada «Nimadir noto‘g‘ri» "
                  "tugmasini bosing, tashkilotchi shu yerga yozadi.",
    "blocked": "⛔️ Tashkilotchi Qulay’ga kirishingizni vaqtincha yopdi.",
    "unblocked": "✅ Qulay’ga kirish qaytarildi.",
    "admin_cancelled": "❌ Tashkilotchi buyurtmani bekor qildi.",
    "code_ready": "✅ {name} uchun kod tayyor.\nUnga shu havolani yuboring:\n{link}",
    "code_declined": "Tashkilotchi hozircha {name} uchun kod bermadi.",
    "your_id": "Telegram ID raqamingiz: `{id}`",
},
}

def user_lang(uid: str) -> str:
    """Язык человека. Хранится в users/{uid}/lang — тот же профиль, что читает
    Mini App, так что выбор языка у бота и в приложении общий."""
    v = db.reference(f"users/{uid}/lang").get()
    return v if v in LANGS else DEFAULT_LANG

def t(lang: str, key: str, **kw) -> str:
    """Фраза по ключу. Если в узбекском словаре ключа нет — берём русский,
    чтобы человек увидел текст, а не пустоту или сам ключ."""
    s = T.get(lang, T[DEFAULT_LANG]).get(key) or T[DEFAULT_LANG].get(key) or key
    return s.format(**kw) if kw else s

def both(key: str) -> str:
    """Регулярка для кнопки меню на любом из языков: подписи локализованы,
    а ловим их одним хендлером, иначе узбекское меню просто перестало бы
    нажиматься."""
    return "^(" + "|".join(re.escape(T[l][key]) for l in LANGS) + ")$"

BAG_KEYS = ("bag_one", "bag_few", "bag_big", "bag_glass")

def bag_options(lang: str):
    return [t(lang, k) for k in BAG_KEYS]

def bag_to_canon(text: str) -> str:
    """Что бы человек ни нажал, в заявку кладём русский вариант: его же ждёт
    Mini App и вторая сторона, у которой может быть другой язык."""
    s = (text or "").strip()
    for k in BAG_KEYS:
        if s == T["uz"][k] or s == T["ru"][k]:
            return T["ru"][k]
    return s

STATUS_KEY = {"open": "st_open", "taken": "st_taken", "arrived": "st_arrived",
              "picked": "st_picked", "done": "st_done", "cancelled": "st_cancelled"}

def status_label(status: str, lang: str = DEFAULT_LANG) -> str:
    key = STATUS_KEY.get(status)
    return t(lang, key) if key else (status or "")

# консоль организатора остаётся русской — этот словарь читают только /admin и /orders
STATUS_LABEL = {s: t(DEFAULT_LANG, k) for s, k in STATUS_KEY.items()}

# ================= ГЛОБАЛЬНОЕ СОСТОЯНИЕ =================
main_loop = None            # event loop бота — заполняется в on_startup
bot_app = None               # Application — заполняется в main()
orders_status_cache = {}     # oid -> последний известный status, для отслеживания перехода
BOT_USERNAME = ""            # заполняется в on_startup, уходит в ссылку Mini App

# ================= ХЕЛПЕРЫ =================
def get_user(uid: str):
    return db.reference(f"users/{uid}").get()

def fmt_phone(raw: str) -> str:
    d = "".join(ch for ch in (raw or "") if ch.isdigit())
    if d.startswith("998"):
        d = d[3:]
    d = d[:9]
    out = "+998"
    if d: out += " " + d[0:2]
    if len(d) > 2: out += " " + d[2:5]
    if len(d) > 5: out += " " + d[5:7]
    if len(d) > 7: out += " " + d[7:9]
    return out

def full_name(user) -> str:
    return " ".join(filter(None, [user.first_name, user.last_name])) or (user.username or "Без имени")

# ================= КОДЫ ПРИГЛАШЕНИЯ ДЛЯ ВОЛОНТЁРОВ =================
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # без 0/O/1/I, чтобы не путали при вводе

def is_admin(uid: str) -> bool:
    return uid in ADMIN_IDS

def gen_invite_code(n: int = 6) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))

class InviteCodeInvalid(Exception):
    pass

def redeem_invite_code(code: str, uid: str, name: str) -> bool:
    """Атомарно помечает код использованным. Возвращает False, если код
    не найден или уже был использован кем-то другим — тогда роль волонтёра не выдаём."""
    ref = db.reference(f"inviteCodes/{code}")

    def txn(current):
        if current is None or current.get("used"):
            raise InviteCodeInvalid()
        current["used"] = True
        current["usedBy"] = uid
        current["usedByName"] = name
        current["usedAt"] = int(datetime.now().timestamp() * 1000)
        return current

    try:
        ref.transaction(txn)
        return True
    except InviteCodeInvalid:
        return False

def role_menu(role: str, lang: str = DEFAULT_LANG):
    if role == "volunteer":
        return ReplyKeyboardMarkup([
            [KeyboardButton(t(lang, "btn_onair"))],
            [KeyboardButton(t(lang, "btn_orders_near")), KeyboardButton(t(lang, "btn_my_jobs"))],
        ], resize_keyboard=True)
    return ReplyKeyboardMarkup([
        [KeyboardButton(t(lang, "btn_new_order"))],
        [KeyboardButton(t(lang, "btn_my_orders"))],
    ], resize_keyboard=True)

def bags_label(o: dict, lang: str) -> str:
    """В заявке лежит русский вариант — на экране показываем на языке читателя."""
    s = o.get("bags") or ""
    for k in BAG_KEYS:
        if s == T["ru"][k]:
            return t(lang, k)
    return s

def order_text(o: dict, full: bool = True, lang: str = DEFAULT_LANG) -> str:
    """full=False — версия для рассылки по всем свободным волонтёрам: там
    квартира, подъезд и комментарий (в нём часто код домофона) ещё не должны
    светиться. Точный адрес появляется у того, кто заявку взял."""
    if not full:
        lines = [t(lang, "addr_short", house=o.get("house", "—"))]
        if o.get("bags"):
            lines.append(f"🧺 {bags_label(o, lang)}")
        lines.append(t(lang, "addr_locked"))
        return "\n".join(lines)
    lines = [t(lang, "addr_full", house=o.get("house", "—"), flat=o.get("flat", "—"))]
    lines.append(t(lang, "addr_entrance", entrance=o.get("entrance", "—"), floor=o.get("floor", "—")))
    if o.get("note"):
        lines.append(f"💬 {o['note']}")
    if o.get("bags"):
        lines.append(f"🧺 {bags_label(o, lang)}")
    lines.append(status_label(o.get("status"), lang))
    return "\n".join(lines)

def send_async(chat_id: int, text: str, **kwargs):
    """Отправить сообщение из фонового потока Firebase-слушателя
    (у него нет своего event loop, поэтому шлём через основной)."""
    if not (main_loop and bot_app):
        return
    async def _send():
        try:
            await bot_app.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception as e:
            log.warning(f"send_async failed for {chat_id}: {e}")
    asyncio.run_coroutine_threadsafe(_send(), main_loop)

# ================= /start =================
def phone_kb(lang: str = DEFAULT_LANG):
    return ReplyKeyboardMarkup(
        [[KeyboardButton(t(lang, "btn_share_phone"), request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )

def app_url(role: str, lang: str = None) -> str:
    """Ссылка на Mini App. Имя бота передаём внутрь, чтобы приложение могло
    собрать корректную ссылку-приглашение для соседей, а не угадывать его.
    Язык тоже: приложение откроется на том же языке, что и бот."""
    parts = []
    if role == "volunteer":
        parts.append("role=volunteer")
    if BOT_USERNAME:
        parts.append("bot=" + BOT_USERNAME)
    if lang in LANGS:
        parts.append("lang=" + lang)
    return APP_URL + ("?" + "&".join(parts) if parts else "")

def open_app_kb(role: str, lang: str = DEFAULT_LANG):
    label = t(lang, "btn_open_app_vol" if role == "volunteer" else "btn_open_app")
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        label, web_app=WebAppInfo(url=app_url(role, lang)))]])

def lang_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(T["ru"]["btn_lang_ru"], callback_data="setlang_ru"),
        InlineKeyboardButton(T["uz"]["btn_lang_uz"], callback_data="setlang_uz"),
    ]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    arg = context.args[0] if context.args else ""

    # Язык спрашиваем только у того, кто его ещё не выбирал. Аргумент /start
    # (в том числе vol_КОД) переживает этот шаг в базе — иначе выбор языка
    # съедал бы приглашение волонтёра.
    if db.reference(f"users/{uid}/lang").get() not in LANGS:
        if arg:
            db.reference(f"users/{uid}/pendingStartArg").set(arg)
        await update.message.reply_text(T["ru"]["lang_ask"], reply_markup=lang_kb())
        return

    await run_start(update.message, uid, arg, user_lang(uid))

async def set_lang_cb(update, context):
    """Выбор языка кнопкой. Дальше — тот же путь, что и обычный /start,
    вместе с отложенным аргументом (приглашением), если он был."""
    query = update.callback_query
    uid = str(query.from_user.id)
    lang = query.data.replace("setlang_", "")
    if lang not in LANGS:
        lang = DEFAULT_LANG
    db.reference(f"users/{uid}/lang").set(lang)
    arg = db.reference(f"users/{uid}/pendingStartArg").get() or ""
    if arg:
        db.reference(f"users/{uid}/pendingStartArg").delete()
    await query.answer()
    await query.edit_message_text(t(lang, "lang_saved") + "\n" + t(lang, "lang_hint"))
    await run_start(query.message, uid, arg, lang, from_user=query.from_user)

async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сменить язык в любой момент."""
    await update.message.reply_text(T["ru"]["lang_ask"], reply_markup=lang_kb())

async def run_start(message, uid: str, arg: str, lang: str, from_user=None):
    """Общая логика /start для обоих входов — обычной команды и кнопки языка.
    Держим её в одном месте, чтобы разбор приглашения не разъехался."""
    user = get_user(uid)
    who = from_user or message.chat

    # /start vol_КОД должен разбираться ДО ветки "уже зарегистрирован" — иначе
    # для человека, у которого в users/{uid} уже есть phone (например, он же
    # раньше зашёл как житель через собственную регистрацию Mini App), код
    # приглашения молча игнорировался: бот отвечал "с возвращением" тем же
    # клиентским меню и даже не смотрел на arg.
    if arg.startswith("vol_") or arg == "vol":
        if user and user.get("role") == "volunteer":
            await message.reply_text(t(lang, "already_volunteer"),
                                     reply_markup=role_menu("volunteer", lang))
            await message.reply_text(t(lang, "app_cta"),
                                     reply_markup=open_app_kb("volunteer", lang))
            return
        code = arg[4:].strip().upper() if arg.startswith("vol_") else ""
        name_for_code = full_name(who) if from_user else (user or {}).get("name", "")
        if not code or not redeem_invite_code(code, uid, name_for_code):
            await message.reply_text(t(lang, "code_bad"))
            return
        if user and user.get("phone"):
            # человек уже отвечал на телефон/имя раньше (как житель) — второй
            # раз не переспрашиваем, нужен только район, которого у жителя нет
            db.reference(f"users/{uid}/pendingUpgradeRole").set("volunteer")
            await message.reply_text(t(lang, "code_ok_upgrade"))
            return
        db.reference(f"users/{uid}/pendingRole").set("volunteer")
        await message.reply_text(t(lang, "hello"), reply_markup=phone_kb(lang))
        return

    if user and user.get("phone"):
        role = user.get("role", "client")
        await message.reply_text(t(lang, "welcome_back", name=user.get("name", "")),
                                 reply_markup=role_menu(role, lang))
        await message.reply_text(t(lang, "app_cta"),
                                 reply_markup=open_app_kb(role, lang))
        return
    # /start ref_UID  -> пришёл по ссылке жителя, запомним кто пригласил
    if arg.startswith("ref_"):
        inviter = arg[4:].strip()
        if inviter and inviter != uid:
            db.reference(f"users/{uid}/pendingRef").set(inviter)
    db.reference(f"users/{uid}/pendingRole").set("client")
    await message.reply_text(t(lang, "hello"), reply_markup=phone_kb(lang))

async def classic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запасной выход: открыть прежнюю версию приложения (classic.html).
    Нужен только если в новой что-то сломается посреди пилота."""
    uid = str(update.effective_user.id)
    role = (get_user(uid) or {}).get("role", "client")
    base = APP_URL.split("?", 1)[0]
    if not base.endswith("/"):
        base = base.rsplit("/", 1)[0] + "/"
    url = base + "classic.html"
    if role == "volunteer":
        url += "?role=volunteer"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Открыть прежнюю версию",
                                                     web_app=WebAppInfo(url=url))]])
    await update.message.reply_text(
        "Прежняя версия приложения — на случай, если в новой что-то не работает.",
        reply_markup=kb
    )

async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    await update.message.reply_text(
        t(user_lang(uid), "your_id", id=uid), parse_mode="Markdown")

async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    code = gen_invite_code()
    db.reference(f"inviteCodes/{code}").set({
        "used": False,
        "createdAt": int(datetime.now().timestamp() * 1000),
        "createdBy": uid,
    })
    me_bot = await context.bot.get_me()
    link = f"https://t.me/{me_bot.username}?start=vol_{code}"
    await update.message.reply_text(
        f"Ссылка для нового волонтёра:\n{link}\n\n"
        f"Одноразовая: сработает только у того, кто откроет её первым."
    )  # без parse_mode: ссылка вида ?start=vol_XXXXXX содержит "_", Markdown ломается на нём

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сводка по сервису. Живёт в боте, а не в приложении: у Mini App нет входа
    по паролю, и любое правило, открывающее список пользователей организатору,
    открыло бы телефоны всех жителей кому угодно."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    users = db.reference("users").get() or {}
    orders = db.reference("orders").get() or {}
    reqs = db.reference("inviteRequests").get() or {}
    codes = db.reference("inviteCodes").get() or {}

    vols = [u for u in users.values() if isinstance(u, dict) and u.get("role") == "volunteer"]
    cls = [u for u in users.values() if isinstance(u, dict) and u.get("role") == "client"]
    onair = [u for u in vols if u.get("onair")]
    no_photo = [u for u in vols if not u.get("hasPhoto")]
    by_status = {}
    for o in orders.values():
        if isinstance(o, dict):
            by_status[o.get("status")] = by_status.get(o.get("status"), 0) + 1
    pending = [r for r in reqs.values() if isinstance(r, dict) and r.get("status") == "pending"]
    free_codes = [c for c, v in codes.items() if isinstance(v, dict) and not v.get("used")]

    lines = [
        "📊 Qulay — сводка", "",
        f"👤 Жителей: {len(cls)}",
        f"🚶 Волонтёров: {len(vols)} (на связи {len(onair)})",
    ]
    if no_photo:
        lines.append(f"⚠️ Без фото: {len(no_photo)} — их жители не видят в лицо")
    lines += ["", "📦 Заявки:"]
    for k in ("open", "taken", "arrived", "picked", "done", "cancelled"):
        if by_status.get(k):
            lines.append(f"   {STATUS_LABEL.get(k, k)} — {by_status[k]}")
    lines += ["", f"🎟 Свободных кодов: {len(free_codes)}",
              f"✉️ Запросов на приглашение: {len(pending)}"]
    if pending:
        lines.append("Посмотреть: /requests")
    lines += ["", "Цифры пилота: /stats",
              "Заявки сейчас: /orders",
              "Человек: /user <id|телефон|имя>",
              "Разговоры: /chats · написать: /say <кто> <текст>",
              "Ещё: /volunteers /requests /invite /invites /block /doc"]
    await update.message.reply_text("\n".join(lines))

def _find_user(needle: str):
    """Ищем пользователя по Telegram ID, номеру или части имени."""
    users = db.reference("users").get() or {}
    needle = needle.strip().lower()
    digits = "".join(ch for ch in needle if ch.isdigit())
    for uid_, u in users.items():
        if not isinstance(u, dict):
            continue
        if uid_ == needle:
            return uid_, u
        if digits and len(digits) >= 7 and digits[-7:] in "".join(
                ch for ch in (u.get("phone") or "") if ch.isdigit()):
            return uid_, u
        if len(needle) >= 3 and needle in (u.get("name") or "").lower():
            return uid_, u
    return None, None

async def block_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/block <id|телефон|имя> — закрыть доступ. /unblock — вернуть."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    unblock = update.message.text.strip().startswith("/unblock")
    if not context.args:
        await update.message.reply_text(
            "Кого? Укажите Telegram ID, номер или имя:\n"
            f"{'/unblock' if unblock else '/block'} 5730011770")
        return
    target, u = _find_user(" ".join(context.args))
    if not target:
        await update.message.reply_text("Не нашёл такого человека.")
        return
    db.reference(f"users/{target}/blocked").set(not unblock)
    if not unblock:
        db.reference(f"users/{target}/onair").set(False)
    label = "Волонтёр" if u.get("role") == "volunteer" else "Житель"
    await update.message.reply_text(
        f"{'✅ Доступ возвращён' if unblock else '⛔️ Доступ закрыт'}\n"
        f"{label}: {u.get('name','—')} · {u.get('phone','')}")
    try:
        send_async(int(target), t(user_lang(target), "unblocked") if unblock
                   else t(user_lang(target), "blocked"))
    except (ValueError, TypeError):
        pass

async def doc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/doc <id|телефон|имя> — показать документ волонтёра.
    Документы лежат в узле, который приложению читать запрещено: их видит
    только бот со служебным ключом, то есть фактически только организатор."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    if not context.args:
        await update.message.reply_text("Кого? Например: /doc Тимур")
        return
    target, u = _find_user(" ".join(context.args))
    if not target:
        await update.message.reply_text("Не нашёл такого человека.")
        return
    sent = False
    for node, caption in (("volunteerPhotos", "Фото волонтёра"), ("volunteerDocs", "Документ")):
        rec = db.reference(f"{node}/{target}").get() or {}
        img = rec.get("img") if isinstance(rec, dict) else None
        if not img or "," not in img:
            continue
        try:
            raw = base64.b64decode(img.split(",", 1)[1])
            await update.message.reply_photo(
                io.BytesIO(raw),
                caption=f"{caption} — {u.get('name','—')} · {u.get('phone','')}")
            sent = True
        except Exception as e:
            log.warning(f"doc_cmd {node}/{target}: {e}")
    if not sent:
        await update.message.reply_text(
            f"{u.get('name','—')}: фото и документ не загружены.")

def _median(xs):
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

def _mins(ms):
    return f"{round(ms / 60000)} мин" if ms is not None else "—"

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Цифры, по которым видно, живой пилот или нет: сколько заявок в день,
    как быстро их разбирают и какая доля вообще осталась без волонтёра.
    Без этого решение «работает / не работает» принимать не на чем."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    orders = db.reference("orders").get() or {}
    now = int(datetime.now().timestamp() * 1000)
    day = 86400000

    per_day = {}
    to_taken, to_done, kg_total = [], [], 0
    lost = done = total = 0
    vols_week = set()
    for o in orders.values():
        if not isinstance(o, dict):
            continue
        created = o.get("createdAt") or 0
        if not created:
            continue
        total += 1
        age_days = int((now - created) // day)
        if age_days < 7:
            per_day[age_days] = per_day.get(age_days, 0) + 1
        st = o.get("status")
        if st == "done":
            done += 1
            kg_total += o.get("kg") or 3
            if o.get("takenAt"):
                to_taken.append(o["takenAt"] - created)
                if o.get("doneAt"):
                    to_done.append(o["doneAt"] - o["takenAt"])
            if o.get("volunteerId") and now - created < 7 * day:
                vols_week.add(str(o["volunteerId"]))
        elif st == "cancelled" and not o.get("volunteerId"):
            lost += 1

    lines = ["📈 Пилот — цифры", ""]
    lines.append(f"Всего заявок: {total} · закрыто {done}")
    if total:
        lines.append(f"Осталось без волонтёра: {lost} ({round(lost * 100 / total)}%)")
    lines += ["", "По дням (0 = сегодня):"]
    for d in range(7):
        n = per_day.get(d, 0)
        lines.append(f"   {d}: {'▇' * min(n, 20)}{'' if n else '·'} {n}")
    lines += ["", "Скорость:",
              f"   до взятия — медиана {_mins(_median(to_taken))}",
              f"   в работе  — медиана {_mins(_median(to_done))}"]
    lines += ["", f"Волонтёров работало за неделю: {len(vols_week)}",
              f"Вынесено: ≈{kg_total} кг"]
    await update.message.reply_text("\n".join(lines))

async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Что прямо сейчас в работе — с кнопкой закрыть вручную.
    Нужно, когда заявка зависла, а сторож ещё не дошёл до неё."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    orders = db.reference("orders").get() or {}
    live = [(oid, o) for oid, o in orders.items()
            if isinstance(o, dict) and o.get("status") in ("open", "taken", "arrived", "picked")]
    if not live:
        await update.message.reply_text("Сейчас активных заявок нет.")
        return
    live.sort(key=lambda kv: kv[1].get("createdAt", 0))
    now = int(datetime.now().timestamp() * 1000)
    for oid, o in live[:15]:
        mins = round((now - (o.get("createdAt") or now)) / 60000)
        who = o.get("volunteerName") or "—"
        text = (f"{STATUS_LABEL.get(o.get('status'), o.get('status'))} · {mins} мин\n"
                f"{order_text(o)}\n"
                f"Житель: {o.get('clientName','—')} · {o.get('clientPhone','')}"
                + (f" · /say_{o['clientId']}" if o.get("clientId") else "") + "\n"
                f"Волонтёр: {who} · {o.get('volunteerPhone','')}"
                + (f" · /say_{o['volunteerId']}" if o.get("volunteerId") else ""))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "❌ Отменить заявку", callback_data=f"adminx_{oid}")]])
        await update.message.reply_text(text, reply_markup=kb)

async def admin_cancel(update, context):
    query = update.callback_query
    uid = str(query.from_user.id)
    if not is_admin(uid):
        await query.answer("Только для организатора")
        return
    oid = query.data.replace("adminx_", "")
    o = db.reference(f"orders/{oid}").get() or {}
    db.reference(f"orders/{oid}").update({
        "status": "cancelled", "cancelledBy": "admin"})
    await query.answer("Отменена")
    await query.edit_message_text(query.message.text + "\n\n❌ Отменена организатором")
    for side in ("clientId", "volunteerId"):
        if o.get(side):
            try:
                send_async(int(o[side]), t(user_lang(str(o[side])), "admin_cancelled"))
            except (ValueError, TypeError):
                pass

async def user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Полная карточка одного человека — что о нём вообще известно."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    if not context.args:
        await update.message.reply_text("Кого? /user 5730011770 или /user Азиза")
        return
    target, u = _find_user(" ".join(context.args))
    if not target:
        await update.message.reply_text("Не нашёл такого человека.")
        return
    b = db.reference(f"leaderboard/{target}").get() or {}
    orders = db.reference("orders").get() or {}
    as_cl = sum(1 for o in orders.values()
                if isinstance(o, dict) and str(o.get("clientId")) == target)
    as_vo = sum(1 for o in orders.values()
                if isinstance(o, dict) and str(o.get("volunteerId")) == target)
    avg = (b.get("ratingSum") or 0) / (b.get("ratingCount") or 1) if b.get("ratingCount") else None
    lines = [
        f"👤 {u.get('name','—')} · {u.get('phone','')}",
        f"ID: {target}",
        f"Роль: {'волонтёр' if u.get('role') == 'volunteer' else 'житель'}"
        + (" · ⛔️ заблокирован" if u.get("blocked") else ""),
        f"Дом/район: {u.get('house') or u.get('district') or '—'}",
        "",
        f"Заявок как житель: {as_cl} · как волонтёр: {as_vo}",
        f"Очки: {b.get('points', 0)} · закрыто: {b.get('ordersCompleted', 0)}",
        f"Оценка: {round(avg, 2) if avg else '—'} ({b.get('ratingCount', 0)} оценок)",
        f"Фото: {'есть' if u.get('hasPhoto') else 'нет'} · "
        f"документ: {'есть' if u.get('hasDoc') else 'нет'}",
    ]
    if u.get("invitedBy"):
        inv = get_user(str(u["invitedBy"])) or {}
        lines.append(f"Пригласил: {inv.get('name','—')}")
    lines.append("")
    lines.append(f"Написать: /say_{target}   ·   документ: /doc {target}")
    lines.append(f"Заблокировать: /block {target}")
    await update.message.reply_text("\n".join(lines))

# ================= ДИАЛОГ С ОРГАНИЗАТОРОМ =================
# Жалоба уходила в одну сторону: человек написал — и всё, ответить организатору
# он уже не мог. Сеанс открывает организатор; пока он открыт, всё, что человек
# пишет в чат бота, уходит организатору. Закрыл — снова тишина, чтобы бот не
# превратился в свалку случайных сообщений.
SUPPORT_IDLE_MIN = 180   # столько сеанс живёт без сообщений

def support_open(uid: str, by: str):
    db.reference(f"support/{uid}").set({
        "open": True, "by": by,
        "openedAt": int(datetime.now().timestamp() * 1000),
        "lastAt": int(datetime.now().timestamp() * 1000),
    })

def support_close(uid: str):
    db.reference(f"support/{uid}").delete()

def admin_target(admin_uid: str):
    """С кем организатор разговаривает прямо сейчас. Лежит в базе, а не в
    памяти: иначе рестарт бота посреди разговора терял бы собеседника."""
    return db.reference(f"adminChat/{admin_uid}").get() or None

def admin_set_target(admin_uid: str, target: str):
    db.reference(f"adminChat/{admin_uid}").set(target)

def admin_clear_target(admin_uid: str):
    db.reference(f"adminChat/{admin_uid}").delete()

def support_is_open(uid: str) -> bool:
    s = db.reference(f"support/{uid}").get()
    if not isinstance(s, dict) or not s.get("open"):
        return False
    last = s.get("lastAt") or s.get("openedAt") or 0
    if (int(datetime.now().timestamp() * 1000) - last) > SUPPORT_IDLE_MIN * 60 * 1000:
        support_close(uid)          # сам себя закрывает, если разговор заглох
        return False
    return True

async def say_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Написать человеку от имени сервиса. Заодно открывает сеанс — иначе
    человек получает вопрос и не может на него ответить."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    if not context.args:
        await update.message.reply_text(
            "Как: /say 5730011770 текст — или просто нажмите /say_id из карточки.")
        return
    target, u = _find_user(context.args[0])
    if not target:
        await update.message.reply_text("Не нашёл такого человека.")
        return
    # без текста — просто открываем разговор, дальше можно писать без команды
    if len(context.args) == 1:
        name = _begin_chat(uid, target)
        await update.message.reply_text(
            f"💬 Разговор с {name}\n\nПросто пишите сообщения.\nЗакончить: /close_{target}")
        return
    text = " ".join(context.args[1:])
    try:
        send_async(int(target), t(user_lang(target), "admin_says_reply", text=text))
        support_open(target, uid)
        admin_set_target(uid, target)
        await update.message.reply_text(
            f"Отправлено: {u.get('name','—')}\n"
            f"Дальше пишите без команды. Закончить: /close_{target}")
    except (ValueError, TypeError):
        await update.message.reply_text("Этот человек пришёл не из Telegram — написать не получится.")

def _begin_chat(admin_uid: str, target: str, greet: bool = True) -> str:
    """Открыть разговор и сделать его текущим для организатора."""
    u = get_user(target) or {}
    was_open = support_is_open(target)
    if not was_open:
        support_open(target, admin_uid)
    admin_set_target(admin_uid, target)
    db.reference(f"support/{target}/unread").set(0)   # переключились — прочитано
    # здороваемся только когда линия действительно открывается: при простом
    # переключении между разговорами человека дёргать незачем
    if greet and not was_open:
        try:
            send_async(int(target), t(user_lang(target), "admin_online"))
        except (ValueError, TypeError):
            pass
    return u.get("name", "—")

async def say_open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/say_5730011770 — одно нажатие в чате, без копирования id.
    Дальше организатор просто пишет сообщения, команду повторять не нужно."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        return
    m = re.match(r"^/(say|close)_(\w+)", update.message.text or "")
    if not m:
        return
    action, target = m.group(1), m.group(2)
    if action == "close":
        support_close(target)
        if admin_target(uid) == target:
            admin_clear_target(uid)
        u = get_user(target) or {}
        try:
            send_async(int(target), t(user_lang(target), "admin_closed"))
        except (ValueError, TypeError):
            pass
        await update.message.reply_text(f"Диалог закрыт: {u.get('name','—')}")
        return
    name = _begin_chat(uid, target)
    await update.message.reply_text(
        f"💬 Разговор с {name}\n\n"
        "Просто пишите сообщения — они уйдут ему.\n"
        f"Закончить: /close_{target}")

async def chats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Все открытые разговоры разом — с кем говорим сейчас, кто ждёт ответа."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    sessions = db.reference("support").get() or {}
    cur = admin_target(uid)
    rows = []
    for tid, s in sessions.items():
        if not isinstance(s, dict) or not s.get("open"):
            continue
        if not support_is_open(tid):      # заодно подчищает протухшие
            continue
        u = get_user(tid) or {}
        rows.append((s.get("lastAt") or 0, tid, u, s.get("unread") or 0))
    if not rows:
        await update.message.reply_text(
            "Открытых разговоров нет.\n"
            "Начать: /user <телефон или имя> — в карточке будет /say_id")
        return
    rows.sort(reverse=True)
    lines = ["💬 Разговоры", ""]
    for last, tid, u, unread in rows:
        mins = round((int(datetime.now().timestamp() * 1000) - last) / 60000) if last else 0
        mark = "▶️ " if tid == cur else ""
        bell = f" · {unread} новых" if unread and tid != cur else ""
        who = "волонтёр" if u.get("role") == "volunteer" else "житель"
        lines.append(f"{mark}{u.get('name','—')} ({who}){bell}")
        lines.append(f"    молчит {mins} мин · /say_{tid} · /close_{tid}")
    lines += ["", "▶️ — с кем вы говорите сейчас. Обычный текст уходит ему."]
    await update.message.reply_text("\n".join(lines))

async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершить сеанс — дальше человек писать напрямую не сможет.
    Без аргумента закрывает текущий разговор."""
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    if context.args:
        target, u = _find_user(" ".join(context.args))
        if not target:
            await update.message.reply_text("Не нашёл такого человека.")
            return
        name = u.get("name", "—")
    else:
        target = admin_target(uid)
        if not target:
            await update.message.reply_text("Сейчас нет открытого разговора.")
            return
        name = (get_user(target) or {}).get("name", "—")
    support_close(target)
    if admin_target(uid) == target:
        admin_clear_target(uid)
    try:
        send_async(int(target), t(user_lang(target), "admin_closed"))
    except (ValueError, TypeError):
        pass
    await update.message.reply_text(f"Диалог закрыт: {name}")

async def support_open_cb(update, context):
    """Кнопка «Ответить» под жалобой — сразу открывает сеанс."""
    query = update.callback_query
    uid = str(query.from_user.id)
    if not is_admin(uid):
        await query.answer("Только для организатора")
        return
    target = query.data.replace("supop_", "")
    name = _begin_chat(uid, target)
    await query.answer("Диалог открыт")
    await query.edit_message_text(
        query.message.text
        + f"\n\n💬 Разговор с {name} открыт — просто пишите сообщения."
          f"\nЗакончить: /close_{target}")

async def volunteers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    users = db.reference("users").get() or {}
    board = db.reference("leaderboard").get() or {}
    vols = [(k, v) for k, v in users.items()
            if isinstance(v, dict) and v.get("role") == "volunteer"]
    if not vols:
        await update.message.reply_text("Волонтёров пока нет.")
        return
    vols.sort(key=lambda kv: -(board.get(kv[0], {}) or {}).get("points", 0))
    lines = ["🚶 Волонтёры", ""]
    for vid, v in vols[:30]:
        b = board.get(vid, {}) or {}
        cnt = b.get("ratingCount") or 0
        avg = round(b.get("ratingSum", 0) / cnt, 1) if cnt else None
        lines.append(
            f"{'🟢' if v.get('onair') else '⚪️'} {v.get('name','—')} · {v.get('phone','')}\n"
            f"   {v.get('district') or 'район не указан'} · "
            f"{b.get('ordersCompleted',0)} заявок · {b.get('points',0)} очк."
            + (f" · ★{avg}" if avg else "")
            + ("" if v.get("hasPhoto") else "\n   ⚠️ без фото")
        )
    await update.message.reply_text("\n".join(lines))

async def requests_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    reqs = db.reference("inviteRequests").get() or {}
    pending = [(k, v) for k, v in reqs.items()
               if isinstance(v, dict) and v.get("status") == "pending"]
    if not pending:
        await update.message.reply_text("Новых запросов на приглашение нет.")
        return
    pending.sort(key=lambda kv: kv[1].get("at", 0))
    for rid, r in pending[:10]:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Выдать код", callback_data=f"okreq_{rid}"),
            InlineKeyboardButton("✖️ Отклонить", callback_data=f"noreq_{rid}"),
        ]])
        await update.message.reply_text(
            f"✉️ Запрос на приглашение\n\n"
            f"Кого зовут: {r.get('forName','—')} · {r.get('forPhone','')}\n"
            f"Просит: {r.get('byName','—')} · {r.get('byPhone','')}",
            reply_markup=kb
        )

async def approve_request(update, context):
    query = update.callback_query
    uid = str(query.from_user.id)
    if not is_admin(uid):
        await query.answer("Только для организатора", show_alert=True)
        return
    rid = query.data.replace("okreq_", "")
    ref = db.reference(f"inviteRequests/{rid}")
    r = ref.get()
    if not r or r.get("status") != "pending":
        await query.answer("Запрос уже обработан", show_alert=True)
        return
    code = gen_invite_code()
    db.reference(f"inviteCodes/{code}").set({
        "used": False, "createdAt": int(datetime.now().timestamp() * 1000),
        "createdBy": uid, "forName": r.get("forName"), "forPhone": r.get("forPhone"),
    })
    ref.update({"status": "approved", "code": code,
                "decidedAt": int(datetime.now().timestamp() * 1000)})
    link = f"https://t.me/{BOT_USERNAME}?start=vol_{code}" if BOT_USERNAME else f"код {code}"
    try:
        rlang = user_lang(str(r.get("byUid")))
        send_async(int(r.get("byUid")),
                   t(rlang, "code_ready", name=r.get("forName", "—"), link=link))
    except (ValueError, TypeError):
        pass
    await query.answer("Код выдан ✓")
    await query.edit_message_text(query.message.text + f"\n\n✅ Выдан код {code}")

async def decline_request(update, context):
    query = update.callback_query
    uid = str(query.from_user.id)
    if not is_admin(uid):
        await query.answer("Только для организатора", show_alert=True)
        return
    rid = query.data.replace("noreq_", "")
    ref = db.reference(f"inviteRequests/{rid}")
    r = ref.get()
    if not r or r.get("status") != "pending":
        await query.answer("Запрос уже обработан", show_alert=True)
        return
    ref.update({"status": "declined", "decidedAt": int(datetime.now().timestamp() * 1000)})
    try:
        send_async(int(r.get("byUid")),
                   t(user_lang(str(r.get("byUid"))), "code_declined",
                     name=r.get("forName", "—")))
    except (ValueError, TypeError):
        pass
    await query.answer("Отклонено")
    await query.edit_message_text(query.message.text + "\n\n✖️ Отклонено")

async def invites_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Эта команда только для организатора.")
        return
    codes = db.reference("inviteCodes").get() or {}
    if not codes:
        await update.message.reply_text("Кодов ещё не выпускали. Команда /invite создаст новый.")
        return
    rows = sorted(codes.items(), key=lambda kv: kv[1].get("createdAt", 0), reverse=True)[:30]
    lines = []
    for code, v in rows:
        if v.get("used"):
            lines.append(f"❌ {code} — использован ({v.get('usedByName', '?')})")
        else:
            lines.append(f"✅ {code} — свободен")
    await update.message.reply_text("\n".join(lines))

async def contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.effective_user
    uid = str(user.id)
    lang = user_lang(uid)
    if contact.user_id and contact.user_id != user.id:
        await update.message.reply_text(t(lang, "share_own_phone"))
        return
    phone = fmt_phone(contact.phone_number)
    # регистрация завершится в name_received() — там же пишем phone в users/{uid}
    db.reference(f"users/{uid}/pendingPhone").set(phone)
    await update.message.reply_text(
        t(lang, "ask_name"),
        reply_markup=ReplyKeyboardRemove()
    )

async def name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    # Апгрейд существующего жителя до волонтёра по уже погашенному в start()
    # коду — имя и телефон у него есть, спрашиваем только район. Проверяем
    # это раньше pendingPhone: у такого человека pendingPhone всегда пуст
    # (он уже зарегистрирован), и без этой проверки его ответ на вопрос
    # о районе принял бы за случайное сообщение боту.
    lang = user_lang(uid)
    pending_upgrade = db.reference(f"users/{uid}/pendingUpgradeRole").get()
    if pending_upgrade == "volunteer":
        district = update.message.text.strip()
        if len(district) < 1:
            await update.message.reply_text(t(lang, "place_short"))
            return
        u = get_user(uid) or {}
        db.reference(f"users/{uid}").update({
            "role": "volunteer", "district": district,
            "onair": False, "verifiedAt": int(datetime.now().timestamp() * 1000),
        })
        db.reference(f"users/{uid}/pendingUpgradeRole").delete()
        db.reference(f"leaderboard/{uid}").update({
            "name": u.get("name", ""), "role": "volunteer", "district": district,
        })
        await update.message.reply_text(
            t(lang, "upgrade_done", district=district),
            reply_markup=role_menu("volunteer", lang)
        )
        await update.message.reply_text(
            t(lang, "open_here"),
            reply_markup=open_app_kb("volunteer", lang)
        )
        return

    pending_phone = db.reference(f"users/{uid}/pendingPhone").get()
    if not pending_phone:
        # не регистрация — но, может быть, идёт разговор с организатором.
        # Отдельный MessageHandler тут заводить нельзя: второй широкий
        # filters.TEXT начал бы перехватывать кнопки меню.
        if is_admin(uid):
            # организатор в открытом разговоре пишет обычным текстом,
            # без /say и без id — команду хватило нажать один раз
            tgt = admin_target(uid)
            if tgt:
                u = get_user(tgt) or {}
                try:
                    send_async(int(tgt), t(user_lang(tgt), "admin_says")
                               + "\n\n" + update.message.text)
                    db.reference(f"support/{tgt}/lastAt").set(
                        int(datetime.now().timestamp() * 1000))
                    await update.message.reply_text(
                        f"→ {u.get('name','—')} ✓   ·   /chats   ·   /close_{tgt}")
                except (ValueError, TypeError):
                    await update.message.reply_text("Не получилось отправить.")
                return
        if support_is_open(uid):
            u = get_user(uid) or {}
            who = "волонтёр" if u.get("role") == "volunteer" else "житель"
            db.reference(f"support/{uid}/lastAt").set(int(datetime.now().timestamp() * 1000))
            for aid in ADMIN_IDS:
                # если организатор сейчас говорит с другим — не даём ему
                # случайно ответить не туда: помечаем письмо и предлагаем
                # переключиться одним нажатием
                focused = admin_target(aid) == uid
                head = (f"💬 {u.get('name','—')} ({who}) · {u.get('phone','')}"
                        if focused else
                        f"📨 Другой разговор — {u.get('name','—')} ({who}) · {u.get('phone','')}")
                tail = ("Отвечайте прямо здесь   ·   закончить: /close_" + uid
                        if focused else
                        f"Переключиться: /say_{uid}   ·   все разговоры: /chats")
                try:
                    send_async(int(aid), f"{head}\n\n{update.message.text}\n\n{tail}")
                except (ValueError, TypeError):
                    pass
                if not focused:
                    db.reference(f"support/{uid}/unread").transaction(lambda c: (c or 0) + 1)
            await update.message.reply_text(t(lang, "passed_on"))
            return
        # человек пишет боту просто так: раньше сообщение уходило в пустоту и
        # он не понимал, услышали его или нет
        if db.reference(f"users/{uid}/phone").get():
            await update.message.reply_text(t(lang, "no_session"))
        return  # не в процессе регистрации — пусть обработают другие хендлеры
    pending_role = db.reference(f"users/{uid}/pendingRole").get() or "client"
    pending_name = db.reference(f"users/{uid}/pendingName").get()

    if not pending_name:
        # это сообщение — имя
        name = update.message.text.strip()
        if len(name) < 2:
            await update.message.reply_text(t(lang, "name_short"))
            return
        db.reference(f"users/{uid}/pendingName").set(name)
        await update.message.reply_text(
            t(lang, "ask_district" if pending_role == "volunteer" else "ask_house"))
        return

    # это сообщение — дом (жилец) или район (волонтёр)
    place = update.message.text.strip()
    if len(place) < 1:
        await update.message.reply_text(t(lang, "place_short"))
        return
    name = pending_name
    place_field = {"district": place} if pending_role == "volunteer" else {"house": place}
    db.reference(f"users/{uid}").update({
        "name": name, "role": pending_role, "phone": pending_phone,
        "onair": False, "verifiedAt": int(datetime.now().timestamp() * 1000),
        **place_field,
    })
    inviter = db.reference(f"users/{uid}/pendingRef").get()
    if inviter:
        db.reference(f"users/{uid}/invitedBy").set(inviter)
        db.reference(f"referrals/{inviter}/{uid}").set({
            "name": name, "phone": pending_phone, "role": pending_role,
            "at": int(datetime.now().timestamp() * 1000),
        })
        try:
            send_async(int(inviter), t(user_lang(str(inviter)), "someone_joined",
                                       name=name, phone=pending_phone))
        except (ValueError, TypeError):
            pass
    db.reference(f"users/{uid}/pendingRole").delete()
    db.reference(f"users/{uid}/pendingPhone").delete()
    db.reference(f"users/{uid}/pendingName").delete()
    db.reference(f"users/{uid}/pendingRef").delete()

    db.reference(f"leaderboard/{uid}").update({
        "name": name, "role": pending_role,
        "house": place if pending_role != "volunteer" else None,
        "district": place if pending_role == "volunteer" else None,
        "points": 0, "ordersCompleted": 0, "ratingSum": 0, "ratingCount": 0,
    })

    label = t(lang, "role_volunteer" if pending_role == "volunteer" else "role_client")
    await update.message.reply_text(t(lang, "reg_done", name=name, role=label),
                                    reply_markup=role_menu(pending_role, lang))
    await update.message.reply_text(
        t(lang, "open_here"),
        reply_markup=open_app_kb(pending_role, lang)
    )

# ================= ЖИЛЕЦ: НОВАЯ ЗАЯВКА =================
async def new_order_start(update, context):
    lang = user_lang(str(update.effective_user.id))
    if not is_open_now():
        await update.message.reply_text(t(
            lang, "closed_now", open_h=OPEN_H,
            hours=t(lang, "hours_text", open_h=OPEN_H, close_h=CLOSE_H)))
        return ConversationHandler.END
    context.user_data["order"] = {}
    await update.message.reply_text(t(lang, "ask_house_num"), reply_markup=ReplyKeyboardRemove())
    return HOUSE

async def get_house(update, context):
    context.user_data["order"]["house"] = update.message.text.strip()
    await update.message.reply_text(t(user_lang(str(update.effective_user.id)), "ask_entrance"))
    return ENTRANCE

async def get_entrance(update, context):
    context.user_data["order"]["entrance"] = update.message.text.strip()
    await update.message.reply_text(t(user_lang(str(update.effective_user.id)), "ask_floor"))
    return FLOOR

async def get_floor(update, context):
    context.user_data["order"]["floor"] = update.message.text.strip()
    await update.message.reply_text(t(user_lang(str(update.effective_user.id)), "ask_flat"))
    return FLAT

async def get_flat(update, context):
    context.user_data["order"]["flat"] = update.message.text.strip()
    await update.message.reply_text(t(user_lang(str(update.effective_user.id)), "ask_note"))
    return NOTE

async def get_note(update, context):
    lang = user_lang(str(update.effective_user.id))
    text = update.message.text.strip()
    context.user_data["order"]["note"] = "" if text == "-" else text
    kb = ReplyKeyboardMarkup([[KeyboardButton(b)] for b in bag_options(lang)],
                              resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(t(lang, "ask_bags"), reply_markup=kb)
    return BAGS

async def get_bags(update, context):
    order = context.user_data["order"]
    # в базу кладём русский вариант: заявку читают обе стороны и Mini App,
    # а язык у них может быть разный
    order["bags"] = bag_to_canon(update.message.text)

    user = update.effective_user
    uid = str(user.id)
    lang = user_lang(uid)
    profile = get_user(uid) or {}
    order.update({
        "clientId": uid,
        "clientName": profile.get("name") or full_name(user),
        "clientPhone": profile.get("phone", ""),
        "status": "open",
        "when": "сейчас",
        "createdAt": int(datetime.now().timestamp() * 1000),
    })
    db.reference("orders").push(order)
    await update.message.reply_text(t(lang, "order_sent"),
                                    reply_markup=role_menu("client", lang))
    context.user_data.pop("order", None)
    return ConversationHandler.END

async def cancel_conv(update, context):
    context.user_data.pop("order", None)
    uid = str(update.effective_user.id)
    lang = user_lang(uid)
    role = (get_user(uid) or {}).get("role", "client")
    await update.message.reply_text(t(lang, "cancelled"), reply_markup=role_menu(role, lang))
    return ConversationHandler.END

async def my_orders_client(update, context):
    uid = str(update.effective_user.id)
    lang = user_lang(uid)
    orders = db.reference("orders").order_by_child("clientId").equal_to(uid).get() or {}
    if not orders:
        await update.message.reply_text(t(lang, "no_orders"))
        return
    entries = sorted(orders.items(), key=lambda kv: kv[1].get("createdAt", 0), reverse=True)
    for _id, o in entries[:10]:
        await update.message.reply_text(order_text(o, lang=lang))

# ================= ВОЛОНТЁР: НА СВЯЗИ =================
async def toggle_onair(update, context):
    uid = str(update.effective_user.id)
    lang = user_lang(uid)
    cur = (get_user(uid) or {}).get("onair", False)
    db.reference(f"users/{uid}/onair").set(not cur)
    await update.message.reply_text(
        t(lang, "onair_on" if not cur else "onair_off"),
        reply_markup=role_menu("volunteer", lang)
    )

def open_orders():
    orders = db.reference("orders").order_by_child("status").equal_to("open").get() or {}
    return sorted(orders.items(), key=lambda kv: kv[1].get("createdAt", 0), reverse=True)

async def orders_nearby(update, context):
    lang = user_lang(str(update.effective_user.id))
    entries = open_orders()
    if not entries:
        await update.message.reply_text(t(lang, "no_open_orders"))
        return
    for oid, o in entries[:10]:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            t(lang, "btn_take"), callback_data=f"take_{oid}")]])
        await update.message.reply_text(order_text(o, full=False, lang=lang), reply_markup=kb)

# ================= ВОЛОНТЁР: ВЗЯТЬ ЗАЯВКУ (транзакция — защита от гонки) =================
async def take_order(update, context):
    query = update.callback_query
    oid = query.data.replace("take_", "")
    user = query.from_user
    uid = str(user.id)
    profile = get_user(uid) or {}
    vol_name = profile.get("name") or full_name(user)
    vol_phone = profile.get("phone", "")

    ref = db.reference(f"orders/{oid}")

    def txn(cur):
        if not cur or cur.get("status") != "open":
            return cur
        cur["status"] = "taken"
        cur["volunteerId"] = uid
        cur["volunteerName"] = vol_name
        cur["volunteerPhone"] = vol_phone
        cur["takenAt"] = int(datetime.now().timestamp() * 1000)
        return cur

    lang = user_lang(uid)
    result = ref.transaction(txn)
    if result and result.get("volunteerId") == uid:
        await query.answer(t(lang, "took_it"))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "btn_arrived"), callback_data=f"arrived_{oid}")],
            [InlineKeyboardButton(t(lang, "btn_drop"), callback_data=f"drop_{oid}")],
        ])
        await query.edit_message_text(order_text(result, lang=lang), reply_markup=kb)
    else:
        await query.answer(t(lang, "too_late"), show_alert=True)
        await query.edit_message_text(query.message.text + "\n\n" + t(lang, "taken_note"))

async def step_arrived(update, context):
    query = update.callback_query
    oid = query.data.replace("arrived_", "")
    lang = user_lang(str(query.from_user.id))
    db.reference(f"orders/{oid}").update({"status": "arrived", "arrivedAt": int(datetime.now().timestamp()*1000)})
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_picked"), callback_data=f"picked_{oid}")],
        [InlineKeyboardButton(t(lang, "btn_drop"), callback_data=f"drop_{oid}")],
    ])
    await query.answer()
    await query.edit_message_text(
        order_text(db.reference(f"orders/{oid}").get(), lang=lang), reply_markup=kb)

async def drop_order(update, context):
    """Волонтёр вернул заявку в общий список — без штрафа, иначе он просто пропадёт молча."""
    query = update.callback_query
    oid = query.data.replace("drop_", "")
    uid = str(query.from_user.id)
    ref = db.reference(f"orders/{oid}")

    def txn(cur):
        if not cur or cur.get("volunteerId") != uid or cur.get("status") in ("done", "cancelled"):
            return cur
        cur["status"] = "open"
        for k in ("volunteerId", "volunteerName", "volunteerPhone", "takenAt", "arrivedAt", "pickedAt"):
            cur.pop(k, None)
        return cur

    lang = user_lang(uid)
    result = ref.transaction(txn)
    if result and result.get("status") == "open":
        await query.answer(t(lang, "dropped"))
        await query.edit_message_text(query.message.text + "\n\n" + t(lang, "dropped_note"))
    else:
        await query.answer(t(lang, "drop_too_late"), show_alert=True)

async def step_picked(update, context):
    query = update.callback_query
    oid = query.data.replace("picked_", "")
    lang = user_lang(str(query.from_user.id))
    db.reference(f"orders/{oid}").update({"status": "picked", "pickedAt": int(datetime.now().timestamp()*1000)})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        t(lang, "btn_done"), callback_data=f"done_{oid}")]])
    await query.answer()
    await query.edit_message_text(
        order_text(db.reference(f"orders/{oid}").get(), lang=lang), reply_markup=kb)

async def step_done(update, context):
    query = update.callback_query
    oid = query.data.replace("done_", "")
    uid = str(query.from_user.id)
    lang = user_lang(uid)
    db.reference(f"orders/{oid}").update({"status": "done", "doneAt": int(datetime.now().timestamp()*1000)})
    db.reference(f"users/{uid}/completedCount").transaction(lambda c: (c or 0) + 1)
    await query.answer(t(lang, "done_ok"))
    await query.edit_message_text(query.message.text + "\n\n" + t(lang, "done_note"))

async def my_orders_volunteer(update, context):
    uid = str(update.effective_user.id)
    lang = user_lang(uid)
    orders = db.reference("orders").order_by_child("volunteerId").equal_to(uid).get() or {}
    if not orders:
        await update.message.reply_text(t(lang, "no_jobs"))
        return
    entries = sorted(orders.items(), key=lambda kv: kv[1].get("createdAt", 0), reverse=True)
    for _id, o in entries[:10]:
        await update.message.reply_text(order_text(o, lang=lang))

# ================= ЖИВАЯ СИНХРОНИЗАЦИЯ С Firebase (в обе стороны) =================
def _bump_leaderboard(uid: str, points: int, orders_delta: int):
    """Атомарно прибавляет очки/счётчик заявок в leaderboard/{uid}.
    Если записи ещё нет (пользователь зарегистрирован до появления этой фичи),
    создаёт её из текущего профиля — тогда дом/район подтянутся сами при
    следующем сохранении профиля, если пользователь их ещё не указал."""
    ref = db.reference(f"leaderboard/{uid}")

    def txn(cur):
        if cur is None:
            profile = get_user(uid) or {}
            cur = {
                "name": profile.get("name", ""), "role": profile.get("role", "client"),
                "house": profile.get("house"), "district": profile.get("district"),
                "points": 0, "ordersCompleted": 0, "ratingSum": 0, "ratingCount": 0,
            }
        cur["points"] = (cur.get("points") or 0) + points
        cur["ordersCompleted"] = (cur.get("ordersCompleted") or 0) + orders_delta
        return cur

    ref.transaction(txn)

def on_orders_change(event):
    """Срабатывает на любое изменение в /orders — и из Mini App, и из бота.
    Перечитывает заявку целиком при каждом релевантном событии, а не пытается
    восстановить её из самого события: .update() сразу несколькими полями
    (а это буквально любой переход статуса — claim/setStatus/step_*) отдаёт в
    event.data только ИЗМЕНИВШИЕСЯ поля, а не весь объект. Раньше код заменял
    ими весь закэшированный объект целиком — clientId и остальное терялись,
    из-за чего уведомления и начисление очков тихо переставали срабатывать."""
    global orders_status_cache
    path = event.path.strip("/")

    if path == "":
        orders_status_cache = {oid: (o or {}).get("status") for oid, o in (event.data or {}).items()}
        return

    oid = path.split("/")[0]

    if event.data is None and "/" not in path:
        orders_status_cache.pop(oid, None)
        return

    after = db.reference(f"orders/{oid}").get()
    if not isinstance(after, dict):
        return

    before_status = orders_status_cache.get(oid)
    new_status = after.get("status")
    orders_status_cache[oid] = new_status
    if new_status == before_status:
        return

    if new_status == "open":
        # заявка ищет волонтёра: либо она только что создана, либо волонтёр
        # отказался и вернул её в общий список — во втором случае before_status
        # не None, и раньше эта ветка молча пропускалась, а заявка повисала
        returned = before_status is not None
        title_key = "order_free" if returned else "new_order"
        # заявку «к 18:00» не будим сейчас — её разошлёт сторож, когда подойдёт
        # время, иначе волонтёр возьмёт её в полдень и житель полдня ждёт
        if order_is_due(after):
            broadcast_open_order(oid, after, title_key)
            db.reference(f"orders/{oid}/notifiedAt").set(int(datetime.now().timestamp() * 1000))
        if returned:
            try:
                clang = user_lang(str(after.get("clientId")))
                send_async(int(after.get("clientId")),
                           t(clang, "vol_gone") + "\n" + order_text(after, lang=clang))
            except (ValueError, TypeError):
                pass

    elif new_status in ("taken", "arrived", "picked", "done", "cancelled"):
        client_id = after.get("clientId")
        try:
            clang = user_lang(str(client_id))
            msg = status_label(new_status, clang) + "\n" + order_text(after, lang=clang)
            if new_status in ("taken", "arrived", "picked"):
                base = app_url("client", clang)
                deep_url = base + ("&" if "?" in base else "?") + "view=live"
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                    t(clang, "btn_open_order"), web_app=WebAppInfo(url=deep_url))]])
                send_async(int(client_id), msg, reply_markup=kb)
            else:
                send_async(int(client_id), msg)
        except (ValueError, TypeError):
            pass  # жилец пришёл из Mini App, не из Telegram

        if new_status == "cancelled" and after.get("volunteerId"):
            # житель отменил, когда волонтёр уже шёл — иначе тот узнает
            # только по тому, что экран в приложении молча сменился
            try:
                vlang = user_lang(str(after["volunteerId"]))
                send_async(int(after["volunteerId"]),
                           t(vlang, "client_cancelled") + "\n" + order_text(after, lang=vlang))
            except (ValueError, TypeError):
                pass

        if new_status == "done":
            # начисляем очки/счётчик независимо от того, кто закрыл заявку —
            # через бота или через setStatus('done') в Mini App
            vol_id = after.get("volunteerId")
            cli_id = after.get("clientId")
            if vol_id:
                _bump_leaderboard(str(vol_id), points=POINTS_PER_ORDER, orders_delta=1)
            if cli_id:
                _bump_leaderboard(str(cli_id), points=0, orders_delta=1)

def on_report(event):
    """Кнопка «Что-то не так» из приложения — сразу всем организаторам."""
    if event.data is None or not isinstance(event.data, dict):
        return
    r = event.data
    if "byUid" not in r:          # пришёл весь узел целиком при подписке
        return
    who = "житель" if r.get("role") == "client" else "волонтёр"
    by = str(r.get("byUid") or "")
    text = (f"🆘 Жалоба во время заявки\n\n"
            f"От кого: {r.get('byName','—')} ({who}) · {r.get('byPhone','')}\n"
            f"Адрес: {r.get('addr') or '—'}\n"
            f"Вторая сторона: {r.get('otherName') or '—'} · {r.get('otherPhone') or ''}")
    # без этой кнопки жалоба была улицей с односторонним движением: спросить
    # «что случилось?» организатор мог, а ответить человек — уже нет
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "💬 Открыть диалог", callback_data=f"supop_{by}")]]) if by else None
    for aid in ADMIN_IDS:
        try:
            send_async(int(aid), text, reply_markup=kb)
        except (ValueError, TypeError):
            pass

def on_order_msg(event):
    """Готовая фраза из приложения — доставляем второй стороне в чат.
    Звонок остаётся, но перестаёт быть единственным способом связи."""
    if event.data is None or not isinstance(event.data, dict):
        return
    m = event.data
    if "toUid" not in m:          # пришёл весь узел целиком при подписке
        return
    rlang = user_lang(str(m["toUid"]))
    who = t(rlang, "who_client" if m.get("fromRole") == "client" else "who_volunteer")
    text = t(rlang, "msg_from", who=who, name=m.get("fromName", "")) + f"\n\n{m.get('text','')}"
    try:
        send_async(int(m["toUid"]), text)
    except (ValueError, TypeError):
        pass  # вторая сторона пришла не из Telegram
    # фразы живут только ради доставки — узел не копим
    try:
        oid = event.path.strip("/").split("/")[0]
        if oid:
            db.reference(f"orderMsgs/{oid}").delete()
    except Exception:
        pass

def start_firebase_listener():
    def _run():
        db.reference("orders").listen(on_orders_change)
    threading.Thread(target=_run, daemon=True).start()
    def _reports():
        db.reference("reports").listen(on_report)
    threading.Thread(target=_reports, daemon=True).start()
    def _msgs():
        db.reference("orderMsgs").listen(on_order_msg)
    threading.Thread(target=_msgs, daemon=True).start()

LEAD_MIN = 40           # за столько минут до назначенного времени будим волонтёров
OPEN_H, CLOSE_H = 7, 21  # часы работы сервиса

def is_open_now() -> bool:
    return OPEN_H <= datetime.now().hour < CLOSE_H

OPEN_HOURS_TEXT = f"Сервис работает с {OPEN_H}:00 до {CLOSE_H}:00"

def order_due_at(o: dict):
    """Момент, к которому житель просил забрать мусор. None — «сейчас».
    Строка вида «сегодня в 18:00» приходит из приложения как есть."""
    w = (o or {}).get("when") or ""
    if not w or w == "сейчас":
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", w)
    if not m:
        return None
    created = datetime.fromtimestamp((o.get("createdAt") or 0) / 1000) if o.get("createdAt") else datetime.now()
    due = created.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    return int(due.timestamp() * 1000)

def order_is_due(o: dict) -> bool:
    t = order_due_at(o)
    if t is None:
        return True
    return int(datetime.now().timestamp() * 1000) >= t - LEAD_MIN * 60 * 1000

def broadcast_open_order(oid: str, o: dict, title_key: str, everyone: bool = False):
    """Разослать свободную заявку волонтёрам «на связи».
    everyone=True — всем волонтёрам подряд: это вторая ступень, когда заявку
    четверть часа никто не взял и молчание уже дороже лишнего уведомления.
    Каждому пишем на его языке — рассылка идёт по разным людям."""
    users = db.reference("users").get() or {}
    sent = 0
    for vid, u in users.items():
        if not isinstance(u, dict) or u.get("blocked"):
            continue
        if u.get("role") != "volunteer":
            continue
        if not everyone and not u.get("onair"):
            continue
        vlang = u.get("lang") if u.get("lang") in LANGS else DEFAULT_LANG
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            t(vlang, "btn_take"), callback_data=f"take_{oid}")]])
        try:
            send_async(int(vid),
                       t(vlang, title_key) + "\n\n" + order_text(o, full=False, lang=vlang),
                       reply_markup=kb)
            sent += 1
        except (ValueError, TypeError):
            pass  # заявка создана из Mini App, uid не telegram id
    return sent

ESCALATE_MIN = 15       # столько ждём, прежде чем будить волонтёров не «на связи»
ALERT_ADMIN_MIN = 30    # столько — прежде чем звать организатора

def escalate_open_order(oid: str, o: dict, now: int):
    """Заявка висит, а волонтёры выключили тумблер — раньше она молча ждала
    сутки, и об этом не узнавал никто, включая организатора."""
    # у заявки «к 18:00» ожидание считаем с момента рассылки, а не с создания:
    # иначе она эскалируется сразу, хотя волонтёры увидели её минуту назад
    since = o.get("notifiedAt") or o.get("createdAt") or now
    waited = (now - since) / 60000
    if waited >= ESCALATE_MIN and not o.get("escalatedAt"):
        n = broadcast_open_order(oid, o, "order_help", everyone=True)
        db.reference(f"orders/{oid}/escalatedAt").set(now)
        log.info(f"заявка {oid}: разослана всем волонтёрам ({n})")
        return
    if waited >= ALERT_ADMIN_MIN and not o.get("adminAlertedAt"):
        db.reference(f"orders/{oid}/adminAlertedAt").set(now)
        text = (f"⚠️ Заявка висит {round(waited)} мин без волонтёра\n\n"
                f"{order_text(o)}\n"
                f"Житель: {o.get('clientName','—')} · {o.get('clientPhone','')}\n\n"
                "Посмотреть все: /orders")
        for aid in ADMIN_IDS:
            try:
                send_async(int(aid), text)
            except (ValueError, TypeError):
                pass
        log.info(f"заявка {oid}: организатор предупреждён")

# ================= СТОРОЖ ЗАВИСШИХ ЗАЯВОК =================
STALE_MINUTES = 60      # столько заявка может стоять без движения
ORPHAN_HOURS = 24       # столько никем не взятая заявка ждёт волонтёра

def sweep_stale_orders():
    """Волонтёр может просто закрыть телефон — тогда взятая заявка висит вечно,
    и житель ждёт человека, который не придёт. Раз в 10 минут возвращаем такие
    заявки в общий список, а совсем старые невостребованные закрываем."""
    now = int(datetime.now().timestamp() * 1000)
    stale_ms = STALE_MINUTES * 60 * 1000
    orphan_ms = ORPHAN_HOURS * 60 * 60 * 1000
    orders = db.reference("orders").get() or {}
    for oid, o in orders.items():
        if not isinstance(o, dict):
            continue
        status = o.get("status")
        if status in ("done", "cancelled"):
            continue
        if status == "open":
            # сервис закрылся, а заявку так и не взяли: держать её до утра нечестно —
            # житель всю ночь думает, что за мусором идут. Уже взятые заявки
            # (taken/arrived/picked) закрытие не трогает, волонтёр их доводит.
            if not is_open_now():
                db.reference(f"orders/{oid}").update({
                    "status": "cancelled", "cancelledBy": "schedule"})
                log.info(f"заявка {oid} закрыта: сервис не работает")
                try:
                    clang = user_lang(str(o.get("clientId")))
                    send_async(int(o.get("clientId")), t(
                        clang, "closed_cancel", close_h=CLOSE_H,
                        hours=t(clang, "hours_text", open_h=OPEN_H, close_h=CLOSE_H)))
                except (ValueError, TypeError):
                    pass
                continue
            if now - (o.get("createdAt") or now) > orphan_ms:
                db.reference(f"orders/{oid}").update({"status": "cancelled"})
                log.info(f"заявка {oid} закрыта: сутки без волонтёра")
                try:
                    send_async(int(o.get("clientId")),
                               t(user_lang(str(o.get("clientId"))), "orphan_cancel"))
                except (ValueError, TypeError):
                    pass
                continue
            # запланированная заявка, время которой подошло, — рассылаем один раз
            if not o.get("notifiedAt") and order_is_due(o):
                broadcast_open_order(oid, o, "order_due")
                db.reference(f"orders/{oid}/notifiedAt").set(now)
                log.info(f"заявка {oid} разослана: подошло назначенное время")
                continue
            # уже разослана, но так и висит — поднимаем тревогу по ступеням
            if o.get("notifiedAt"):
                escalate_open_order(oid, o, now)
            continue
        # taken / arrived / picked — смотрим на последнее движение
        last = max(o.get("pickedAt") or 0, o.get("arrivedAt") or 0,
                   o.get("takenAt") or 0, o.get("createdAt") or 0)
        if now - last > stale_ms:
            db.reference(f"orders/{oid}").update({
                "status": "open", "volunteerId": None, "volunteerName": None,
                "volunteerPhone": None, "takenAt": None, "arrivedAt": None, "pickedAt": None,
            })
            log.info(f"заявка {oid} возвращена в общий список: {STALE_MINUTES} мин без движения")

def start_stale_sweeper():
    def _run():
        while True:
            try:
                sweep_stale_orders()
            except Exception as e:
                log.warning(f"sweep_stale_orders: {e}")
            threading.Event().wait(600)   # каждые 10 минут
    threading.Thread(target=_run, daemon=True).start()

# ================= MAIN =================
async def on_startup(app: Application):
    global main_loop, BOT_USERNAME
    main_loop = asyncio.get_running_loop()
    try:
        BOT_USERNAME = (await app.bot.get_me()).username or ""
        # Кладём имя в базу: приложение берёт его оттуда и всегда собирает
        # ссылку-приглашение на бота. Раньше имя приходило только в ?bot=,
        # и при заходе через кнопку меню приглашение вело на голый сайт.
        if BOT_USERNAME:
            db.reference("config/bot").set(BOT_USERNAME)
    except Exception as e:
        log.warning(f"не удалось узнать имя бота: {e}")
    # Кнопка меню рядом с полем ввода ведёт на тот же URL, что и кнопки в чате —
    # с bot=, иначе приложение не знает имени бота и «Поделиться» отдаёт
    # ссылку на сайт вместо ссылки на бота.
    try:
        await app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Qulay", web_app=WebAppInfo(url=app_url("client")))
        )
    except Exception as e:
        log.warning(f"не удалось настроить кнопку меню: {e}")
    start_firebase_listener()
    start_stale_sweeper()
    log.info("Firebase listener и сторож зависших заявок запущены")

def main():
    global bot_app
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    bot_app = app

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("lang", lang_cmd))
    app.add_handler(CommandHandler("classic", classic_cmd))
    app.add_handler(CommandHandler("invite", invite_cmd))
    app.add_handler(CommandHandler("invites", invites_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CommandHandler("user", user_cmd))
    app.add_handler(CommandHandler("say", say_cmd))
    app.add_handler(CommandHandler("close", close_cmd))
    app.add_handler(CommandHandler("chats", chats_cmd))
    # /say_5730011770 и /close_5730011770 — нажимаются прямо в чате одним касанием.
    # CommandHandler("say") их не ловит: для Telegram это команда "say_5730011770".
    app.add_handler(MessageHandler(filters.Regex(r"^/(say|close)_\w+"), say_open_cmd))
    app.add_handler(CommandHandler("volunteers", volunteers_cmd))
    app.add_handler(CommandHandler("requests", requests_cmd))
    app.add_handler(CommandHandler("block", block_cmd))
    app.add_handler(CommandHandler("unblock", block_cmd))
    app.add_handler(CommandHandler("doc", doc_cmd))
    app.add_handler(MessageHandler(filters.CONTACT, contact_received))

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(both("btn_new_order")), new_order_start)],
        states={
            HOUSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_house)],
            ENTRANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_entrance)],
            FLOOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_floor)],
            FLAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_flat)],
            NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_note)],
            BAGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_bags)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
    )
    app.add_handler(conv)

    # подписи кнопок локализованы, поэтому ловим оба языка одной регуляркой —
    # иначе узбекское меню просто перестало бы нажиматься
    app.add_handler(MessageHandler(filters.Regex(both("btn_my_orders")), my_orders_client))
    app.add_handler(MessageHandler(filters.Regex(both("btn_onair")), toggle_onair))
    app.add_handler(MessageHandler(filters.Regex(both("btn_orders_near")), orders_nearby))
    app.add_handler(MessageHandler(filters.Regex(both("btn_my_jobs")), my_orders_volunteer))

    app.add_handler(CallbackQueryHandler(take_order, pattern="^take_"))
    app.add_handler(CallbackQueryHandler(step_arrived, pattern="^arrived_"))
    app.add_handler(CallbackQueryHandler(step_picked, pattern="^picked_"))
    app.add_handler(CallbackQueryHandler(step_done, pattern="^done_"))
    app.add_handler(CallbackQueryHandler(drop_order, pattern="^drop_"))
    app.add_handler(CallbackQueryHandler(approve_request, pattern="^okreq_"))
    app.add_handler(CallbackQueryHandler(decline_request, pattern="^noreq_"))
    app.add_handler(CallbackQueryHandler(admin_cancel, pattern="^adminx_"))
    app.add_handler(CallbackQueryHandler(support_open_cb, pattern="^supop_"))
    app.add_handler(CallbackQueryHandler(set_lang_cb, pattern="^setlang_"))

    # ловит "имя" после шаринга номера; регистрируется последним, чтобы не перехватывать
    # нажатия обычных кнопок меню — сам себя выключает, если пользователь не в процессе регистрации
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, name_received))

    log.info("Бот запускается...")
    app.run_polling()

if __name__ == "__main__":
    main()
