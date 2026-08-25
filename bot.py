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
import secrets
import threading
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, db

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo
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

cred = credentials.Certificate(json.loads(FIREBASE_CREDS_JSON))
firebase_admin.initialize_app(cred, {"databaseURL": DB_URL})

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

HOUSE, ENTRANCE, FLOOR, FLAT, NOTE, BAGS = range(6)

BAG_OPTIONS = ["Один пакет", "Два-три пакета", "Крупный мусор", "Стекло / банки"]

STATUS_LABEL = {
    "open": "🟡 Ищем волонтёра",
    "taken": "🟢 Волонтёр в пути",
    "arrived": "🚪 Волонтёр у двери",
    "picked": "📦 Несёт до контейнера",
    "done": "✅ Готово",
    "cancelled": "❌ Отменена",
}

# ================= ГЛОБАЛЬНОЕ СОСТОЯНИЕ =================
main_loop = None            # event loop бота — заполняется в on_startup
bot_app = None               # Application — заполняется в main()
orders_cache = {}            # локальная копия /orders для отслеживания смены статусов

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

def role_menu(role: str):
    if role == "volunteer":
        return ReplyKeyboardMarkup([
            [KeyboardButton("🟢 Вы на связи")],
            [KeyboardButton("🗺 Заявки рядом"), KeyboardButton("📦 Мои заявки")],
        ], resize_keyboard=True)
    return ReplyKeyboardMarkup([
        [KeyboardButton("📦 Оставить заявку")],
        [KeyboardButton("📋 Мои заявки")],
    ], resize_keyboard=True)

def order_text(o: dict) -> str:
    lines = [f"Дом {o.get('house','—')}, кв. {o.get('flat','—')}"]
    lines.append(f"Подъезд {o.get('entrance','—')}, этаж {o.get('floor','—')}")
    if o.get("note"):
        lines.append(f"💬 {o['note']}")
    if o.get("bags"):
        lines.append(f"🧺 {o['bags']}")
    lines.append(STATUS_LABEL.get(o.get("status"), o.get("status", "")))
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
def phone_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )

def app_url(role: str) -> str:
    return APP_URL if role != "volunteer" else APP_URL + "?role=volunteer"

def open_app_kb(role: str):
    label = "🚶 Открыть приложение" if role == "volunteer" else "📦 Открыть приложение"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, web_app=WebAppInfo(url=app_url(role)))]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    user = get_user(uid)
    if user and user.get("phone"):
        role = user.get("role", "client")
        await update.message.reply_text(
            f"С возвращением, {user.get('name','')}!",
            reply_markup=role_menu(role)
        )
        await update.message.reply_text(
            "Заявки и статус — прямо в приложении:",
            reply_markup=open_app_kb(role)
        )
        return
    # /start vol_КОД  -> регистрация волонтёром, но только по действующему коду приглашения
    arg = context.args[0] if context.args else ""
    pending_role = "client"
    if arg.startswith("vol_") or arg == "vol":
        code = arg[4:].strip().upper() if arg.startswith("vol_") else ""
        if code and redeem_invite_code(code, uid, full_name(update.effective_user)):
            pending_role = "volunteer"
        else:
            await update.message.reply_text(
                "Код приглашения недействителен или уже использован.\n"
                "Чтобы стать волонтёром, попросите новый код у организатора и наберите:\n"
                "/start vol_КОД"
            )
    db.reference(f"users/{uid}/pendingRole").set(pending_role)
    await update.message.reply_text(
        "Привет! Это Qulay — вывоз мусора с помощью волонтёров.\n\n"
        "Поделитесь номером, чтобы продолжить:",
        reply_markup=phone_kb()
    )

async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш Telegram ID: `{update.effective_user.id}`", parse_mode="Markdown")

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
    if contact.user_id and contact.user_id != user.id:
        await update.message.reply_text("Пожалуйста, поделитесь именно своим номером.")
        return
    uid = str(user.id)
    phone = fmt_phone(contact.phone_number)
    # регистрация завершится в name_received() — там же пишем phone в users/{uid}
    db.reference(f"users/{uid}/pendingPhone").set(phone)
    await update.message.reply_text(
        "Как вас записать? Жильцы и волонтёры увидят именно это имя.",
        reply_markup=ReplyKeyboardRemove()
    )

async def name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    pending_phone = db.reference(f"users/{uid}/pendingPhone").get()
    if not pending_phone:
        return  # не в процессе регистрации — это не про нас, пусть обработают другие хендлеры
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Имя слишком короткое, напишите ещё раз.")
        return
    pending_role = db.reference(f"users/{uid}/pendingRole").get() or "client"
    db.reference(f"users/{uid}").update({
        "name": name, "role": pending_role, "phone": pending_phone,
        "onair": False, "verifiedAt": int(datetime.now().timestamp() * 1000),
    })
    db.reference(f"users/{uid}/pendingRole").delete()
    db.reference(f"users/{uid}/pendingPhone").delete()
    label = "Волонтёр" if pending_role == "volunteer" else "Жилец"
    await update.message.reply_text(f"Готово, {name}! Роль: {label}", reply_markup=role_menu(pending_role))
    await update.message.reply_text(
        "Открывайте заявки прямо здесь:",
        reply_markup=open_app_kb(pending_role)
    )

# ================= ЖИЛЕЦ: НОВАЯ ЗАЯВКА =================
async def new_order_start(update, context):
    context.user_data["order"] = {}
    await update.message.reply_text("Номер дома?", reply_markup=ReplyKeyboardRemove())
    return HOUSE

async def get_house(update, context):
    context.user_data["order"]["house"] = update.message.text.strip()
    await update.message.reply_text("Подъезд?")
    return ENTRANCE

async def get_entrance(update, context):
    context.user_data["order"]["entrance"] = update.message.text.strip()
    await update.message.reply_text("Этаж?")
    return FLOOR

async def get_floor(update, context):
    context.user_data["order"]["floor"] = update.message.text.strip()
    await update.message.reply_text("Квартира?")
    return FLAT

async def get_flat(update, context):
    context.user_data["order"]["flat"] = update.message.text.strip()
    await update.message.reply_text("Комментарий (например, код домофона). Если нет — «-»")
    return NOTE

async def get_note(update, context):
    text = update.message.text.strip()
    context.user_data["order"]["note"] = "" if text == "-" else text
    kb = ReplyKeyboardMarkup([[KeyboardButton(b)] for b in BAG_OPTIONS],
                              resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Что выносим?", reply_markup=kb)
    return BAGS

async def get_bags(update, context):
    order = context.user_data["order"]
    order["bags"] = update.message.text.strip()

    user = update.effective_user
    uid = str(user.id)
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
    await update.message.reply_text("Заявка отправлена волонтёрам ✅", reply_markup=role_menu("client"))
    context.user_data.pop("order", None)
    return ConversationHandler.END

async def cancel_conv(update, context):
    context.user_data.pop("order", None)
    role = (get_user(str(update.effective_user.id)) or {}).get("role", "client")
    await update.message.reply_text("Отменено.", reply_markup=role_menu(role))
    return ConversationHandler.END

async def my_orders_client(update, context):
    uid = str(update.effective_user.id)
    orders = db.reference("orders").order_by_child("clientId").equal_to(uid).get() or {}
    if not orders:
        await update.message.reply_text("Пока нет заявок.")
        return
    entries = sorted(orders.items(), key=lambda kv: kv[1].get("createdAt", 0), reverse=True)
    for _id, o in entries[:10]:
        await update.message.reply_text(order_text(o))

# ================= ВОЛОНТЁР: НА СВЯЗИ =================
async def toggle_onair(update, context):
    uid = str(update.effective_user.id)
    cur = (get_user(uid) or {}).get("onair", False)
    db.reference(f"users/{uid}/onair").set(not cur)
    await update.message.reply_text(
        "Вы на связи 🟢 — пришлём уведомление о новой заявке" if not cur
        else "Уведомления выключены 🔕",
        reply_markup=role_menu("volunteer")
    )

def open_orders():
    orders = db.reference("orders").order_by_child("status").equal_to("open").get() or {}
    return sorted(orders.items(), key=lambda kv: kv[1].get("createdAt", 0), reverse=True)

async def orders_nearby(update, context):
    entries = open_orders()
    if not entries:
        await update.message.reply_text("Открытых заявок сейчас нет.")
        return
    for oid, o in entries[:10]:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Взять заявку", callback_data=f"take_{oid}")]])
        await update.message.reply_text(order_text(o), reply_markup=kb)

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

    result = ref.transaction(txn)
    if result and result.get("volunteerId") == uid:
        await query.answer("Заявка ваша ✓")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚪 Я на месте", callback_data=f"arrived_{oid}")]])
        await query.edit_message_text(order_text(result), reply_markup=kb)
    else:
        await query.answer("Заявку уже взял другой волонтёр", show_alert=True)
        await query.edit_message_text(query.message.text + "\n\n❌ Уже занято")

async def step_arrived(update, context):
    query = update.callback_query
    oid = query.data.replace("arrived_", "")
    db.reference(f"orders/{oid}").update({"status": "arrived", "arrivedAt": int(datetime.now().timestamp()*1000)})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📦 Пакет забрал", callback_data=f"picked_{oid}")]])
    await query.answer()
    await query.edit_message_text(order_text(db.reference(f"orders/{oid}").get()), reply_markup=kb)

async def step_picked(update, context):
    query = update.callback_query
    oid = query.data.replace("picked_", "")
    db.reference(f"orders/{oid}").update({"status": "picked", "pickedAt": int(datetime.now().timestamp()*1000)})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"done_{oid}")]])
    await query.answer()
    await query.edit_message_text(order_text(db.reference(f"orders/{oid}").get()), reply_markup=kb)

async def step_done(update, context):
    query = update.callback_query
    oid = query.data.replace("done_", "")
    uid = str(query.from_user.id)
    db.reference(f"orders/{oid}").update({"status": "done", "doneAt": int(datetime.now().timestamp()*1000)})
    db.reference(f"users/{uid}/completedCount").transaction(lambda c: (c or 0) + 1)
    await query.answer("Готово ✓")
    await query.edit_message_text(query.message.text + "\n\n✅ Заявка закрыта. Спасибо!")

async def my_orders_volunteer(update, context):
    uid = str(update.effective_user.id)
    orders = db.reference("orders").order_by_child("volunteerId").equal_to(uid).get() or {}
    if not orders:
        await update.message.reply_text("Пока нет принятых заявок.")
        return
    entries = sorted(orders.items(), key=lambda kv: kv[1].get("createdAt", 0), reverse=True)
    for _id, o in entries[:10]:
        await update.message.reply_text(order_text(o))

# ================= ЖИВАЯ СИНХРОНИЗАЦИЯ С Firebase (в обе стороны) =================
def on_orders_change(event):
    """Срабатывает на любое изменение в /orders — и из Mini App, и из бота.
    Держит локальный кэш, чтобы понимать переход статуса, и рассылает
    уведомления волонтёрам (новая заявка) и жильцам (смена статуса)."""
    global orders_cache
    path = event.path.strip("/")

    if path == "":
        orders_cache = event.data or {}
        return

    parts = path.split("/")
    oid = parts[0]
    before = orders_cache.get(oid)
    before_status = before.get("status") if isinstance(before, dict) else None

    if event.data is None and len(parts) == 1:
        orders_cache.pop(oid, None)
        return

    if len(parts) == 1:
        orders_cache[oid] = event.data
    else:
        rec = orders_cache.setdefault(oid, {})
        if isinstance(rec, dict):
            rec[parts[1]] = event.data

    after = orders_cache.get(oid)
    if not isinstance(after, dict):
        return

    new_status = after.get("status")
    if new_status == before_status:
        return

    if new_status == "open" and before_status is None:
        # новая заявка -> уведомить волонтёров "на связи"
        users = db.reference("users").get() or {}
        for vid, u in users.items():
            if u.get("role") == "volunteer" and u.get("onair"):
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Взять заявку", callback_data=f"take_{oid}")]])
                try:
                    send_async(int(vid), "🔔 Новая заявка рядом\n\n" + order_text(after), reply_markup=kb)
                except (ValueError, TypeError):
                    pass  # заявка создана из Mini App, uid не telegram id

    elif new_status in ("taken", "arrived", "picked", "done", "cancelled"):
        client_id = after.get("clientId")
        try:
            send_async(int(client_id), STATUS_LABEL.get(new_status, new_status) + "\n" + order_text(after))
        except (ValueError, TypeError):
            pass  # жилец пришёл из Mini App, не из Telegram

def start_firebase_listener():
    def _run():
        db.reference("orders").listen(on_orders_change)
    threading.Thread(target=_run, daemon=True).start()

# ================= MAIN =================
async def on_startup(app: Application):
    global main_loop
    main_loop = asyncio.get_running_loop()
    start_firebase_listener()
    log.info("Firebase listener запущен")

def main():
    global bot_app
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    bot_app = app

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("invite", invite_cmd))
    app.add_handler(CommandHandler("invites", invites_cmd))
    app.add_handler(MessageHandler(filters.CONTACT, contact_received))

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📦 Оставить заявку$"), new_order_start)],
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

    app.add_handler(MessageHandler(filters.Regex("^📋 Мои заявки$"), my_orders_client))
    app.add_handler(MessageHandler(filters.Regex("^🟢 Вы на связи$"), toggle_onair))
    app.add_handler(MessageHandler(filters.Regex("^🗺 Заявки рядом$"), orders_nearby))
    app.add_handler(MessageHandler(filters.Regex("^📦 Мои заявки$"), my_orders_volunteer))

    app.add_handler(CallbackQueryHandler(take_order, pattern="^take_"))
    app.add_handler(CallbackQueryHandler(step_arrived, pattern="^arrived_"))
    app.add_handler(CallbackQueryHandler(step_picked, pattern="^picked_"))
    app.add_handler(CallbackQueryHandler(step_done, pattern="^done_"))

    # ловит "имя" после шаринга номера; регистрируется последним, чтобы не перехватывать
    # нажатия обычных кнопок меню — сам себя выключает, если пользователь не в процессе регистрации
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, name_received))

    log.info("Бот запускается...")
    app.run_polling()

if __name__ == "__main__":
    main()
