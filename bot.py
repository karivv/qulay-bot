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

def order_text(o: dict, full: bool = True) -> str:
    """full=False — версия для рассылки по всем свободным волонтёрам: там
    квартира, подъезд и комментарий (в нём часто код домофона) ещё не должны
    светиться. Точный адрес появляется у того, кто заявку взял."""
    if not full:
        lines = [f"Дом {o.get('house','—')}"]
        if o.get("bags"):
            lines.append(f"🧺 {o['bags']}")
        lines.append("🔒 Точный адрес — после того, как возьмёте заявку")
        return "\n".join(lines)
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
    """Ссылка на Mini App. Имя бота передаём внутрь, чтобы приложение могло
    собрать корректную ссылку-приглашение для соседей, а не угадывать его."""
    parts = []
    if role == "volunteer":
        parts.append("role=volunteer")
    if BOT_USERNAME:
        parts.append("bot=" + BOT_USERNAME)
    return APP_URL + ("?" + "&".join(parts) if parts else "")

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
    # /start ref_UID  -> пришёл по ссылке жителя, запомним кто пригласил
    arg = context.args[0] if context.args else ""
    pending_role = "client"
    if arg.startswith("ref_"):
        inviter = arg[4:].strip()
        if inviter and inviter != uid:
            db.reference(f"users/{uid}/pendingRef").set(inviter)
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
              "Написать: /say <кто> <текст> · закрыть диалог: /close <кто>",
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
        send_async(int(target), "✅ Доступ к Qulay возвращён." if unblock
                   else "⛔️ Организатор временно закрыл вам доступ к Qulay.")
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
                send_async(int(o[side]), "❌ Организатор отменил заявку.")
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
        send_async(int(target),
                   f"✉️ Организатор Qulay:\n\n{text}\n\n"
                   "— Можете ответить прямо здесь, просто напишите сообщение.")
        support_open(target, uid)
        admin_set_target(uid, target)
        await update.message.reply_text(
            f"Отправлено: {u.get('name','—')}\n"
            f"Дальше пишите без команды. Закончить: /close_{target}")
    except (ValueError, TypeError):
        await update.message.reply_text("Этот человек пришёл не из Telegram — написать не получится.")

def _begin_chat(admin_uid: str, target: str) -> str:
    """Открыть разговор и сделать его текущим для организатора."""
    u = get_user(target) or {}
    support_open(target, admin_uid)
    admin_set_target(admin_uid, target)
    try:
        send_async(int(target),
                   "✉️ Организатор Qulay на связи — напишите, что случилось. "
                   "Просто отправьте сообщение сюда.")
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
            send_async(int(target), "✅ Организатор завершил разговор. Спасибо!")
        except (ValueError, TypeError):
            pass
        await update.message.reply_text(f"Диалог закрыт: {u.get('name','—')}")
        return
    name = _begin_chat(uid, target)
    await update.message.reply_text(
        f"💬 Разговор с {name}\n\n"
        "Просто пишите сообщения — они уйдут ему.\n"
        f"Закончить: /close_{target}")

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
        send_async(int(target), "✅ Организатор завершил разговор. Спасибо!")
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
        send_async(int(r.get("byUid")),
                   f"✅ Код для {r.get('forName','друга')} готов.\n"
                   f"Перешлите ему эту ссылку:\n{link}")
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
                   f"Организатор пока не выдал код для {r.get('forName','вашего друга')}.")
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
                    send_async(int(tgt),
                               f"✉️ Организатор Qulay:\n\n{update.message.text}")
                    db.reference(f"support/{tgt}/lastAt").set(
                        int(datetime.now().timestamp() * 1000))
                    await update.message.reply_text(
                        f"→ {u.get('name','—')} ✓   ·   закончить: /close_{tgt}")
                except (ValueError, TypeError):
                    await update.message.reply_text("Не получилось отправить.")
                return
        if support_is_open(uid):
            u = get_user(uid) or {}
            who = "волонтёр" if u.get("role") == "volunteer" else "житель"
            text = (f"💬 {u.get('name','—')} ({who}) · {u.get('phone','')}\n\n"
                    f"{update.message.text}\n\n"
                    f"Ответить: /say_{uid}   ·   закончить: /close_{uid}")
            db.reference(f"support/{uid}/lastAt").set(int(datetime.now().timestamp() * 1000))
            for aid in ADMIN_IDS:
                try:
                    send_async(int(aid), text)
                except (ValueError, TypeError):
                    pass
            await update.message.reply_text("Передал организатору ✓")
        return  # не в процессе регистрации — пусть обработают другие хендлеры
    pending_role = db.reference(f"users/{uid}/pendingRole").get() or "client"
    pending_name = db.reference(f"users/{uid}/pendingName").get()

    if not pending_name:
        # это сообщение — имя
        name = update.message.text.strip()
        if len(name) < 2:
            await update.message.reply_text("Имя слишком короткое, напишите ещё раз.")
            return
        db.reference(f"users/{uid}/pendingName").set(name)
        prompt = ("В каком доме вы живёте? (номер или название)" if pending_role != "volunteer"
                  else "В каком районе/махалле вы обычно волонтёрите?")
        await update.message.reply_text(prompt)
        return

    # это сообщение — дом (жилец) или район (волонтёр)
    place = update.message.text.strip()
    if len(place) < 1:
        await update.message.reply_text("Напишите хотя бы коротко.")
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
            send_async(int(inviter), f"🏡 По вашей ссылке зарегистрировался {name} · {pending_phone}")
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

    label = "Волонтёр" if pending_role == "volunteer" else "Жилец"
    await update.message.reply_text(f"Готово, {name}! Роль: {label}", reply_markup=role_menu(pending_role))
    await update.message.reply_text(
        "Открывайте заявки прямо здесь:",
        reply_markup=open_app_kb(pending_role)
    )

# ================= ЖИЛЕЦ: НОВАЯ ЗАЯВКА =================
async def new_order_start(update, context):
    if not is_open_now():
        await update.message.reply_text(
            f"🌙 Сейчас закрыто. {OPEN_HOURS_TEXT}.\n"
            f"Оставьте заявку утром — с {OPEN_H}:00 волонтёры снова на связи.")
        return ConversationHandler.END
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
        await update.message.reply_text(order_text(o, full=False), reply_markup=kb)

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
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚪 Я на месте", callback_data=f"arrived_{oid}")],
            [InlineKeyboardButton("↩️ Не смогу выполнить", callback_data=f"drop_{oid}")],
        ])
        await query.edit_message_text(order_text(result), reply_markup=kb)
    else:
        await query.answer("Заявку уже взял другой волонтёр", show_alert=True)
        await query.edit_message_text(query.message.text + "\n\n❌ Уже занято")

async def step_arrived(update, context):
    query = update.callback_query
    oid = query.data.replace("arrived_", "")
    db.reference(f"orders/{oid}").update({"status": "arrived", "arrivedAt": int(datetime.now().timestamp()*1000)})
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Пакет забрал", callback_data=f"picked_{oid}")],
        [InlineKeyboardButton("↩️ Не смогу выполнить", callback_data=f"drop_{oid}")],
    ])
    await query.answer()
    await query.edit_message_text(order_text(db.reference(f"orders/{oid}").get()), reply_markup=kb)

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

    result = ref.transaction(txn)
    if result and result.get("status") == "open":
        await query.answer("Заявка возвращена")
        await query.edit_message_text(query.message.text + "\n\n↩️ Вы вернули заявку другим волонтёрам")
    else:
        await query.answer("Эту заявку уже нельзя вернуть", show_alert=True)

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
        title = "🔁 Заявка снова свободна" if returned else "🔔 Новая заявка рядом"
        # заявку «к 18:00» не будим сейчас — её разошлёт сторож, когда подойдёт
        # время, иначе волонтёр возьмёт её в полдень и житель полдня ждёт
        if order_is_due(after):
            broadcast_open_order(oid, after, title)
            db.reference(f"orders/{oid}/notifiedAt").set(int(datetime.now().timestamp() * 1000))
        if returned:
            try:
                send_async(int(after.get("clientId")),
                           "🔎 Волонтёр не смог прийти — ищем другого.\n" + order_text(after))
            except (ValueError, TypeError):
                pass

    elif new_status in ("taken", "arrived", "picked", "done", "cancelled"):
        client_id = after.get("clientId")
        try:
            msg = STATUS_LABEL.get(new_status, new_status) + "\n" + order_text(after)
            if new_status in ("taken", "arrived", "picked"):
                deep_url = app_url("client") + ("&" if "?" in app_url("client") else "?") + "view=live"
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                    "📱 Открыть заявку", web_app=WebAppInfo(url=deep_url))]])
                send_async(int(client_id), msg, reply_markup=kb)
            else:
                send_async(int(client_id), msg)
        except (ValueError, TypeError):
            pass  # жилец пришёл из Mini App, не из Telegram

        if new_status == "cancelled" and after.get("volunteerId"):
            # житель отменил, когда волонтёр уже шёл — иначе тот узнает
            # только по тому, что экран в приложении молча сменился
            try:
                send_async(int(after["volunteerId"]),
                           "❌ Житель отменил заявку — идти не нужно.\n" + order_text(after))
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
    who = "Житель" if m.get("fromRole") == "client" else "Волонтёр"
    text = f"💬 {who} {m.get('fromName','')}:\n\n{m.get('text','')}"
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

def broadcast_open_order(oid: str, o: dict, title: str, everyone: bool = False):
    """Разослать свободную заявку волонтёрам «на связи».
    everyone=True — всем волонтёрам подряд: это вторая ступень, когда заявку
    четверть часа никто не взял и молчание уже дороже лишнего уведомления."""
    users = db.reference("users").get() or {}
    sent = 0
    for vid, u in users.items():
        if not isinstance(u, dict) or u.get("blocked"):
            continue
        if u.get("role") != "volunteer":
            continue
        if not everyone and not u.get("onair"):
            continue
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Взять заявку", callback_data=f"take_{oid}")]])
        try:
            send_async(int(vid), title + "\n\n" + order_text(o, full=False), reply_markup=kb)
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
        n = broadcast_open_order(
            oid, o, "🔔 Заявку никто не взял — нужна помощь", everyone=True)
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
                    send_async(int(o.get("clientId")),
                               f"🌙 Извините, волонтёр не нашёлся до {CLOSE_H}:00.\n"
                               f"{OPEN_HOURS_TEXT} — оставьте заявку утром, "
                               "с утра волонтёров обычно больше.")
                except (ValueError, TypeError):
                    pass
                continue
            if now - (o.get("createdAt") or now) > orphan_ms:
                db.reference(f"orders/{oid}").update({"status": "cancelled"})
                log.info(f"заявка {oid} закрыта: сутки без волонтёра")
                try:
                    send_async(int(o.get("clientId")),
                               "⌛️ Заявку закрыли — за сутки никто не смог её взять.\n"
                               "Попробуйте оставить новую — волонтёров бывает больше по вечерам.")
                except (ValueError, TypeError):
                    pass
                continue
            # запланированная заявка, время которой подошло, — рассылаем один раз
            if not o.get("notifiedAt") and order_is_due(o):
                broadcast_open_order(oid, o, "🕓 Скоро время заявки")
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
    app.add_handler(CommandHandler("classic", classic_cmd))
    app.add_handler(CommandHandler("invite", invite_cmd))
    app.add_handler(CommandHandler("invites", invites_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CommandHandler("user", user_cmd))
    app.add_handler(CommandHandler("say", say_cmd))
    app.add_handler(CommandHandler("close", close_cmd))
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
    app.add_handler(CallbackQueryHandler(drop_order, pattern="^drop_"))
    app.add_handler(CallbackQueryHandler(approve_request, pattern="^okreq_"))
    app.add_handler(CallbackQueryHandler(decline_request, pattern="^noreq_"))
    app.add_handler(CallbackQueryHandler(admin_cancel, pattern="^adminx_"))
    app.add_handler(CallbackQueryHandler(support_open_cb, pattern="^supop_"))

    # ловит "имя" после шаринга номера; регистрируется последним, чтобы не перехватывать
    # нажатия обычных кнопок меню — сам себя выключает, если пользователь не в процессе регистрации
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, name_received))

    log.info("Бот запускается...")
    app.run_polling()

if __name__ == "__main__":
    main()
