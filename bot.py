#!/usr/bin/env python3
"""
Qulay Bot — волонтёрский вывоз мусора (вариант "бот" для сравнения с Mini App)
Использует ту же Firebase Realtime Database, что и qulay-pickup.html

Запуск:
    pip install python-telegram-bot firebase-admin
    python3 bot.py

Перед запуском заполни переменные ниже (BOT_TOKEN, SERVICE_ACCOUNT_PATH, DB_URL)
"""

import logging
import math
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, db

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)

# ============================================================
# НАСТРОЙКИ — заполни перед запуском
# ============================================================
BOT_TOKEN = "8975681393:AAF8uuiIjrLw-ikK2yKbhBW16lhgrwfrv5w"
SERVICE_ACCOUNT_PATH = "serviceAccountKey.json"   # файл, скачанный из Firebase Console
DB_URL = "https://qulay-bfc96-default-rtdb.europe-west1.firebasedatabase.app"

# ============================================================
# FIREBASE INIT
# ============================================================
cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
firebase_admin.initialize_app(cred, {"databaseURL": DB_URL})

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# Состояния диалога создания заявки
HOUSE, ENTRANCE, FLOOR, FLAT, COMMENT, LOCATION = range(6)

# ============================================================
# ХЕЛПЕРЫ
# ============================================================
def get_user(user_id: int):
    return db.reference(f"users/{user_id}").get()

def set_role(user_id: int, name: str, role: str):
    db.reference(f"users/{user_id}").update({
        "name": name, "role": role, "updatedAt": int(datetime.now().timestamp() * 1000)
    })

def distance_km(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def role_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Я оставляю заявку", callback_data="role_client")],
        [InlineKeyboardButton("🚶 Я волонтёр", callback_data="role_volunteer")],
    ])

def client_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📦 Оставить заявку")],
        [KeyboardButton("📋 Мои заявки")],
        [KeyboardButton("🔄 Сменить роль")],
    ], resize_keyboard=True)

def volunteer_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🗺 Заявки рядом")],
        [KeyboardButton("📦 Мои заявки"), KeyboardButton("🏆 Рейтинг")],
        [KeyboardButton("🔄 Сменить роль")],
    ], resize_keyboard=True)

def status_label(status: str, volunteer_name: str = None):
    return {
        "open": "🟡 Ищем волонтёра",
        "accepted": f"🟢 Принята: {volunteer_name or ''}",
        "done": "✅ Выполнено",
    }.get(status, status)

# ============================================================
# СТАРТ / ВЫБОР РОЛИ
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    if user and user.get("role"):
        role = user["role"]
        text = "С возвращением!"
        menu = client_menu() if role == "client" else volunteer_menu()
        await update.message.reply_text(text, reply_markup=menu)
    else:
        await update.message.reply_text(
            "Привет! Это пилот сервиса помощи с вывозом мусора.\n\nКто вы сегодня?",
            reply_markup=role_keyboard()
        )

async def role_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    role = "client" if query.data == "role_client" else "volunteer"
    user = query.from_user
    name = " ".join(filter(None, [user.first_name, user.last_name])) or user.username or "Без имени"
    set_role(user.id, name, role)

    await query.edit_message_text(f"Роль сохранена: {'Клиент' if role=='client' else 'Волонтёр'} ✓")
    menu = client_menu() if role == "client" else volunteer_menu()
    await context.bot.send_message(chat_id=user.id, text="Готово! Выберите действие в меню ниже.", reply_markup=menu)

async def switch_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите новую роль:", reply_markup=role_keyboard())

# ============================================================
# КЛИЕНТ: СОЗДАНИЕ ЗАЯВКИ (ConversationHandler)
# ============================================================
async def new_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order"] = {}
    await update.message.reply_text("Номер дома?", reply_markup=ReplyKeyboardRemove())
    return HOUSE

async def get_house(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order"]["house"] = update.message.text
    await update.message.reply_text("Подъезд?")
    return ENTRANCE

async def get_entrance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order"]["entrance"] = update.message.text
    await update.message.reply_text("Этаж?")
    return FLOOR

async def get_floor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order"]["floor"] = update.message.text
    await update.message.reply_text("Квартира?")
    return FLAT

async def get_flat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order"]["flat"] = update.message.text
    await update.message.reply_text("Комментарий (например, код домофона). Если нет — отправьте «-»")
    return COMMENT

async def get_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["order"]["comment"] = "" if text == "-" else text

    loc_btn = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Отправить геолокацию", request_location=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        "Отправьте геолокацию, чтобы волонтёру было проще найти вас:",
        reply_markup=loc_btn
    )
    return LOCATION

async def get_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order = context.user_data["order"]
    if update.message.location:
        order["lat"] = update.message.location.latitude
        order["lng"] = update.message.location.longitude
    else:
        order["lat"] = None
        order["lng"] = None

    user = update.effective_user
    order.update({
        "clientId": str(user.id),
        "clientName": " ".join(filter(None, [user.first_name, user.last_name])) or "Без имени",
        "status": "open",
        "createdAt": int(datetime.now().timestamp() * 1000),
        "volunteerId": None,
        "volunteerName": None,
    })
    db.reference("orders").push(order)

    await update.message.reply_text("Заявка создана ✅", reply_markup=client_menu())
    context.user_data.pop("order", None)
    return ConversationHandler.END

async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("order", None)
    await update.message.reply_text("Отменено.", reply_markup=client_menu())
    return ConversationHandler.END

# ============================================================
# КЛИЕНТ: МОИ ЗАЯВКИ
# ============================================================
async def my_orders_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    orders = db.reference("orders").order_by_child("clientId").equal_to(uid).get() or {}
    if not orders:
        await update.message.reply_text("Пока нет заявок.")
        return
    entries = sorted(orders.items(), key=lambda kv: kv[1].get("createdAt", 0), reverse=True)
    lines = []
    for _id, o in entries[:15]:
        lines.append(
            f"Дом {o.get('house','—')}, кв. {o.get('flat','—')}\n"
            f"{status_label(o.get('status'), o.get('volunteerName'))}"
        )
    await update.message.reply_text("\n\n".join(lines))

# ============================================================
# ВОЛОНТЁР: ЗАЯВКИ РЯДОМ
# ============================================================
async def orders_nearby_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc_btn = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Отправить моё местоположение", request_location=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text("Отправьте геолокацию, чтобы найти ближайшие заявки:", reply_markup=loc_btn)

async def orders_nearby_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        return
    my_lat = update.message.location.latitude
    my_lng = update.message.location.longitude
    context.user_data["last_loc"] = (my_lat, my_lng)

    orders = db.reference("orders").order_by_child("status").equal_to("open").get() or {}
    if not orders:
        await update.message.reply_text("Открытых заявок сейчас нет.", reply_markup=volunteer_menu())
        return

    entries = []
    for oid, o in orders.items():
        d = distance_km(my_lat, my_lng, o["lat"], o["lng"]) if o.get("lat") else 999
        entries.append((d, oid, o))
    entries.sort(key=lambda x: x[0])

    await update.message.reply_text(f"Найдено заявок: {len(entries)}", reply_markup=volunteer_menu())
    for d, oid, o in entries[:10]:
        dist_txt = f"{d:.1f} км" if d < 999 else "расстояние неизвестно"
        text = (
            f"📍 Дом {o.get('house','—')}, подъезд {o.get('entrance','—')}, "
            f"этаж {o.get('floor','—')}, кв. {o.get('flat','—')}\n"
            f"Расстояние: {dist_txt}"
        )
        if o.get("comment"):
            text += f"\n💬 {o['comment']}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Принять заявку", callback_data=f"claim_{oid}")]])
        await update.message.reply_text(text, reply_markup=kb)

# ============================================================
# ВОЛОНТЁР: ПРИНЯТЬ ЗАЯВКУ (транзакция — защита от гонки)
# ============================================================
async def claim_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = query.data.replace("claim_", "")
    user = query.from_user
    vol_name = " ".join(filter(None, [user.first_name, user.last_name])) or "Волонтёр"

    ref = db.reference(f"orders/{order_id}")

    def txn(current):
        if current and current.get("status") == "open":
            current["status"] = "accepted"
            current["volunteerId"] = str(user.id)
            current["volunteerName"] = vol_name
            current["acceptedAt"] = int(datetime.now().timestamp() * 1000)
        return current

    result = ref.transaction(txn)

    if result and result.get("volunteerId") == str(user.id):
        await query.answer("Заявка ваша ✓")
        lat, lng = result.get("lat"), result.get("lng")
        maps_kb = None
        if lat and lng:
            maps_url = f"https://yandex.ru/maps/?rtext=~{lat},{lng}&rtt=pd"
            maps_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗺 Маршрут в Яндекс.Картах", url=maps_url)],
                [InlineKeyboardButton("✅ Отметить выполненной", callback_data=f"done_{order_id}")],
            ])
        else:
            maps_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Отметить выполненной", callback_data=f"done_{order_id}")],
            ])
        await query.edit_message_text(
            query.message.text + "\n\n🟢 Вы приняли эту заявку",
            reply_markup=maps_kb
        )
    else:
        await query.answer("Кто-то опередил вас 😔", show_alert=True)
        await query.edit_message_text(query.message.text + "\n\n❌ Уже занято другим волонтёром")

# ============================================================
# ВОЛОНТЁР: ОТМЕТИТЬ ВЫПОЛНЕННОЙ
# ============================================================
async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = query.data.replace("done_", "")
    user_id = query.from_user.id

    db.reference(f"orders/{order_id}").update({
        "status": "done", "doneAt": int(datetime.now().timestamp() * 1000)
    })

    ref = db.reference(f"users/{user_id}/completedCount")
    ref.transaction(lambda c: (c or 0) + 1)

    await query.answer("Отмечено ✓")
    await query.edit_message_text(query.message.text + "\n\n✅ Выполнено. Спасибо!")

# ============================================================
# ВОЛОНТЁР: МОИ ЗАЯВКИ
# ============================================================
async def my_orders_volunteer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    orders = db.reference("orders").order_by_child("volunteerId").equal_to(uid).get() or {}
    if not orders:
        await update.message.reply_text("Пока нет принятых заявок.")
        return
    active = [(k, o) for k, o in orders.items() if o.get("status") == "accepted"]
    done = [(k, o) for k, o in orders.items() if o.get("status") == "done"]

    lines = ["🟢 Активные:"]
    lines += [f"Дом {o['house']}, кв. {o['flat']}" for _, o in active] or ["— нет —"]
    lines.append("\n✅ Выполненные:")
    lines += [f"Дом {o['house']}, кв. {o['flat']}" for _, o in done] or ["— нет —"]
    await update.message.reply_text("\n".join(lines))

# ============================================================
# РЕЙТИНГ
# ============================================================
async def ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = db.reference("users").get() or {}
    volunteers = [(u.get("name", "Волонтёр"), u.get("completedCount", 0))
                  for u in users.values() if u.get("role") == "volunteer"]
    volunteers.sort(key=lambda x: x[1], reverse=True)

    if not volunteers:
        await update.message.reply_text("Рейтинг пока пуст.")
        return

    lines = ["🏆 Топ волонтёров:\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (name, count) in enumerate(volunteers[:15]):
        prefix = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{prefix} {name} — {count} заявок")
    await update.message.reply_text("\n".join(lines))

# ============================================================
# MAIN
# ============================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(role_chosen, pattern="^role_"))
    app.add_handler(MessageHandler(filters.Regex("^🔄 Сменить роль$"), switch_role))

    # Создание заявки (клиент)
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📦 Оставить заявку$"), new_order_start)],
        states={
            HOUSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_house)],
            ENTRANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_entrance)],
            FLOOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_floor)],
            FLAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_flat)],
            COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_comment)],
            LOCATION: [MessageHandler(filters.LOCATION | filters.TEXT, get_location)],
        },
        fallbacks=[CommandHandler("cancel", cancel_order)],
    )
    app.add_handler(conv)

    app.add_handler(MessageHandler(filters.Regex("^📋 Мои заявки$"), my_orders_client))
    app.add_handler(MessageHandler(filters.Regex("^🗺 Заявки рядом$"), orders_nearby_start))
    app.add_handler(MessageHandler(filters.LOCATION, orders_nearby_result))
    app.add_handler(MessageHandler(filters.Regex("^📦 Мои заявки$"), my_orders_volunteer))
    app.add_handler(MessageHandler(filters.Regex("^🏆 Рейтинг$"), ranking))

    app.add_handler(CallbackQueryHandler(claim_order, pattern="^claim_"))
    app.add_handler(CallbackQueryHandler(mark_done, pattern="^done_"))

    log.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
