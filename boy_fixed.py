"""
Complete Gmail Selling Bot - Final Version
- PostgreSQL (Supabase) persistent storage — Render redeploy করলেও ডেটা থাকে
- User balance management (admin panel থেকে)
- Direct messaging (individual + broadcast)
- Approve/Reject with reason
- User Block/Unblock feature
"""

import os
import logging
import asyncio
from datetime import datetime
from enum import Enum

import psycopg
from psycopg_pool import ConnectionPool

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Python 3.14+ fix
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# ---------- Config (Environment Variables) ----------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")  # Supabase PostgreSQL connection string

if not TELEGRAM_BOT_TOKEN:
    raise SystemExit("❌ TELEGRAM_BOT_TOKEN environment variable পাওয়া যায়নি! Render → Environment-এ সেট করুন।")

if not DATABASE_URL:
    raise SystemExit("❌ DATABASE_URL environment variable পাওয়া যায়নি! Supabase connection string Render → Environment-এ সেট করুন।")

ADMIN_ID = 8669242020
SCRIPT_PRICE = 15
MIN_WITHDRAWAL = 30

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


class States(Enum):
    WAITING_TITLE = 1
    WAITING_SCRIPT = 2
    WAITING_BKASH_NUMBER = 3
    CONFIRMING_WITHDRAWAL = 4
    ADMIN_EDIT_USER_ID = 5
    ADMIN_EDIT_BALANCE = 6
    ADMIN_MESSAGE_USER_ID = 7
    ADMIN_MESSAGE_TEXT = 8
    ADMIN_BROADCAST_TEXT = 9
    ADMIN_REJECT_REASON = 10
    ADMIN_BLOCK_USER_ID = 11
    ADMIN_BLOCK_ACTION = 12


SELL_BTN = "📝 Sell Gmail"
BALANCE_BTN = "💰 Balance"
WITHDRAWAL_BTN = "💸 Withdrawal"
ADMIN_BTN = "⚙️ Admin Panel"

BLOCKED_MESSAGE = (
    "⏸️ আপনার অ্যাকাউন্ট সাময়িকভাবে বন্ধ করা হয়েছে\n\n"
    "আপনি এই মুহূর্তে বটে কাজ করতে পারবেন না।\n"
    "Admin এই বন্ধ অবস্থা সমাপ্ত করলে আপনাকে মেসেজ দ্বারা জানানো হবে।\n\n"
    "ধন্যবাদ।"
)

UNBLOCKED_MESSAGE = (
    "🟢 আপনার অ্যাকাউন্ট আবার খোলা হয়েছে\n\n"
    "আপনি এখন থেকে বটে কাজ করতে পারবেন।\n\n"
    "ধন্যবাদ।"
)

# ---------- PostgreSQL Connection Pool (psycopg3) ----------
# একটা connection pool ব্যবহার করা হচ্ছে যাতে Supabase-এর কানেকশন লিমিট শেষ না হয়ে যায়

_db_pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10, open=True)


def get_conn():
    return _db_pool.getconn()


def release_conn(conn):
    _db_pool.putconn(conn)


# ---------- Database Setup ----------

def init_db():
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 0,
                total_sold INTEGER DEFAULT 0,
                bkash_number TEXT,
                created_at TEXT,
                is_blocked BOOLEAN DEFAULT FALSE,
                blocked_at TEXT,
                unblock_reason TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                script_id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                title TEXT NOT NULL,
                script_text TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                submitted_at TEXT,
                approved_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                withdrawal_id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INTEGER,
                bkash_number TEXT,
                status TEXT DEFAULT 'pending',
                requested_at TEXT,
                approved_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_status (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cur.execute("SELECT value FROM bot_status WHERE key = 'bot_active'")
        if not cur.fetchone():
            cur.execute("INSERT INTO bot_status (key, value) VALUES ('bot_active', '1')")

        conn.commit()
    finally:
        release_conn(conn)


# ---------- Bot Status ----------

def is_bot_active():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_status WHERE key = 'bot_active'")
        result = cur.fetchone()
        return result[0] == '1' if result else True
    finally:
        release_conn(conn)


def set_bot_status(active):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE bot_status SET value = %s WHERE key = 'bot_active'", ('1' if active else '0',))
        conn.commit()
    finally:
        release_conn(conn)


# ---------- Users ----------

def get_or_create_user(user_id, username):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        if user is None:
            cur.execute(
                "INSERT INTO users (user_id, username, created_at) VALUES (%s, %s, %s)",
                (user_id, username, datetime.now().isoformat())
            )
            conn.commit()
    finally:
        release_conn(conn)


def get_user_balance(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        return result[0] if result else 0
    finally:
        release_conn(conn)


def get_user_info(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        return cur.fetchone()
    finally:
        release_conn(conn)


def get_all_users():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, balance, total_sold, is_blocked FROM users ORDER BY user_id DESC")
        return cur.fetchall()
    finally:
        release_conn(conn)


def update_user_balance(user_id, new_balance):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance = %s WHERE user_id = %s", (new_balance, user_id))
        conn.commit()
    finally:
        release_conn(conn)


# ---------- User Block/Unblock ----------

def block_user(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (user_id, username, created_at) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
            (user_id, None, datetime.now().isoformat())
        )
        cur.execute(
            "UPDATE users SET is_blocked = TRUE, blocked_at = %s WHERE user_id = %s",
            (datetime.now().isoformat(), user_id)
        )
        conn.commit()
    finally:
        release_conn(conn)


def unblock_user(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_blocked = FALSE WHERE user_id = %s", (user_id,))
        conn.commit()
    finally:
        release_conn(conn)


def is_user_blocked(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_blocked FROM users WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        return bool(result[0]) if result else False
    finally:
        release_conn(conn)


def get_blocked_users():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, blocked_at FROM users WHERE is_blocked = TRUE")
        return cur.fetchall()
    finally:
        release_conn(conn)


# ---------- Scripts (Gmail submissions) ----------

def add_script(user_id, title, script_text):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO scripts (user_id, title, script_text, submitted_at) VALUES (%s, %s, %s, %s) RETURNING script_id",
            (user_id, title, script_text, datetime.now().isoformat())
        )
        script_id = cur.fetchone()[0]
        conn.commit()
        return script_id
    finally:
        release_conn(conn)


def get_pending_scripts():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT script_id, user_id, title, script_text, submitted_at, status
            FROM scripts
            WHERE status = 'pending'
            ORDER BY submitted_at ASC
        """)
        return cur.fetchall()
    finally:
        release_conn(conn)


def get_script(script_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT script_id, user_id, title, script_text, status FROM scripts WHERE script_id = %s",
            (script_id,)
        )
        return cur.fetchone()
    finally:
        release_conn(conn)


def approve_script(script_id, user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE scripts SET status = 'approved', approved_at = %s WHERE script_id = %s",
            (datetime.now().isoformat(), script_id)
        )
        # ইউজার যদি ডাটাবেজে না থাকে, নতুন করে তৈরি করা (নিরাপত্তা স্তর, সাধারণত দরকার হবে না যেহেতু DB এখন persistent)
        cur.execute(
            "INSERT INTO users (user_id, username, created_at) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
            (user_id, None, datetime.now().isoformat())
        )
        cur.execute(
            "UPDATE users SET balance = balance + %s, total_sold = total_sold + 1 WHERE user_id = %s",
            (SCRIPT_PRICE, user_id)
        )
        balance_updated = cur.rowcount > 0
        conn.commit()
        return balance_updated
    finally:
        release_conn(conn)


def reject_script(script_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE scripts SET status = 'rejected' WHERE script_id = %s", (script_id,))
        conn.commit()
    finally:
        release_conn(conn)


# ---------- Withdrawals ----------

def get_pending_withdrawals():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT withdrawal_id, user_id, amount, bkash_number, requested_at, status
            FROM withdrawals
            WHERE status = 'pending'
            ORDER BY requested_at ASC
        """)
        return cur.fetchall()
    finally:
        release_conn(conn)


def get_withdrawal(withdrawal_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, amount, bkash_number FROM withdrawals WHERE withdrawal_id = %s",
            (withdrawal_id,)
        )
        return cur.fetchone()
    finally:
        release_conn(conn)


def add_withdrawal_request(user_id, amount, bkash_number):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET bkash_number = %s WHERE user_id = %s", (bkash_number, user_id))
        cur.execute(
            "INSERT INTO withdrawals (user_id, amount, bkash_number, requested_at) VALUES (%s, %s, %s, %s) RETURNING withdrawal_id",
            (user_id, amount, bkash_number, datetime.now().isoformat())
        )
        withdrawal_id = cur.fetchone()[0]
        conn.commit()
        return withdrawal_id
    finally:
        release_conn(conn)


def approve_withdrawal(withdrawal_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, amount, status FROM withdrawals WHERE withdrawal_id = %s", (withdrawal_id,))
        result = cur.fetchone()
        if not result:
            return False

        user_id, amount, status = result
        if status != "pending":
            return False

        cur.execute(
            "UPDATE withdrawals SET status = 'approved', approved_at = %s WHERE withdrawal_id = %s",
            (datetime.now().isoformat(), withdrawal_id)
        )
        cur.execute(
            "UPDATE users SET balance = balance - %s WHERE user_id = %s AND balance >= %s",
            (amount, user_id, amount)
        )
        changes = cur.rowcount
        conn.commit()
        return changes > 0
    finally:
        release_conn(conn)


# ---------- Handlers: Main flow ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বর্তমানে বন্ধ আছে।")
        return

    query = update.callback_query
    user = query.from_user if query else update.effective_user

    if is_user_blocked(user.id):
        if query:
            await query.answer()
            await query.message.reply_text(BLOCKED_MESSAGE, reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text(BLOCKED_MESSAGE, reply_markup=ReplyKeyboardRemove())
        return

    get_or_create_user(user.id, user.username or user.first_name)

    keyboard = [
        [SELL_BTN, BALANCE_BTN],
        [WITHDRAWAL_BTN],
    ]
    if user.id == ADMIN_ID:
        keyboard.append([ADMIN_BTN])

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    text = (
        "👋 স্বাগতম GMAIL Selling Bot এ!\n\n"
        "আপনার Gmail and Password লিখে জমা দিন এবং প্রতিটি অনুমোদিত "
        f"Gmail {SCRIPT_PRICE} টাকা আয় করুন।\n\n"
        "📌 কীভাবে কাজ করবেন:\n"
        f"1️⃣ নিচের \"{SELL_BTN}\" বাটনে ক্লিক করুন\n"
        "2️⃣ GMAIL লিখে পাঠান\n"
        "3️⃣ PASSWORD লিখে পাঠান\n"
        "4️⃣ Admin পর্যালোচনা করবেন (সাধারণত ৩০ মিনিটের মধ্যে)\n"
        f"5️⃣ অনুমোদিত হলে আপনার ব্যালেন্সে {SCRIPT_PRICE} টাকা যোগ হবে, "
        "প্রত্যাখ্যাত হলে কোনো টাকা যোগ হবে না\n\n"
        f"💸 উত্তোলন (Withdrawal): সর্বনিম্ন {MIN_WITHDRAWAL} টাকা ব্যালেন্স থাকলে "
        "তবেই উত্তোলনের অনুরোধ করা যাবে।\n\n"
        "নিচের মেনু থেকে অপশন বেছে নিন 👇"
    )

    if query:
        await query.answer()
        await query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)


async def start_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বর্তমানে বন্ধ আছে।")
        return ConversationHandler.END

    if is_user_blocked(update.effective_user.id):
        await update.message.reply_text(BLOCKED_MESSAGE)
        return ConversationHandler.END

    await update.message.reply_text("📝 আপনার GMAIL লিখে পাঠান:")
    return States.WAITING_TITLE


async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["script_title"] = update.message.text.strip()
    await update.message.reply_text("✅ GMAIL রেকর্ড করা হয়েছে।\n\nএখন PASSWORD লিখে পাঠান:")
    return States.WAITING_SCRIPT


async def handle_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    title = context.user_data.get("script_title", "(No title)")
    script_text = update.message.text.strip()

    script_id = add_script(user.id, title, script_text)

    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📝 নতুন GMAIL জমা!\n\nব্যবহারকারী: @{user.username or user.first_name}\n"
            f"ID: {script_id}\nGMAIL: {title}\nPASSWORD: {script_text[:100]}..."
        )
    except Exception as e:
        logger.error(f"Admin notify error: {e}")

    keyboard = [[InlineKeyboardButton("← মেনু", callback_data="back_menu")]]
    await update.message.reply_text(
        f"✅ জমা হয়েছে! (ID: {script_id})\n\n"
        f"⏳ অনুমোদনের জন্য অপেক্ষা করুন\n\n"
        f"আমরা আপনার Gmail পর্যালোচনা করছি। সাধারণত ৩০ মিনিটের মধ্যে অনুমোদন দেওয়া হবে।\n\n"
        f"আপনার অ্যাকাউন্ট verified হলে {SCRIPT_PRICE} টাকা যোগ হবে।",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END


async def balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বন্ধ।")
        return

    if is_user_blocked(update.effective_user.id):
        await update.message.reply_text(BLOCKED_MESSAGE)
        return

    balance = get_user_balance(update.effective_user.id)
    user_info = get_user_info(update.effective_user.id)
    total_sold = user_info[3] if user_info else 0

    text = (
        f"💰 আপনার অ্যাকাউন্ট\n\n"
        f"বর্তমান ব্যালেন্স: {balance} টাকা\n"
        f"মোট বিক্রিত: {total_sold} টি\n"
        f"মোট আয়: {total_sold * SCRIPT_PRICE} টাকা"
    )
    await update.message.reply_text(text)


async def start_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বন্ধ আছে।")
        return ConversationHandler.END

    if is_user_blocked(update.effective_user.id):
        await update.message.reply_text(BLOCKED_MESSAGE)
        return ConversationHandler.END

    balance = get_user_balance(update.effective_user.id)
    if balance < MIN_WITHDRAWAL:
        await update.message.reply_text(f"❌ ন্যূনতম {MIN_WITHDRAWAL} টাকা প্রয়োজন। আপনার: {balance} টাকা")
        return ConversationHandler.END

    await update.message.reply_text(f"💸 উত্তোলন অনুরোধ\n\nব্যালেন্স: {balance} টাকা\n\nBkash নম্বর পাঠান:")
    return States.WAITING_BKASH_NUMBER


async def handle_bkash_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bkash_number = update.message.text.strip()
    if not bkash_number.isdigit() or len(bkash_number) < 10:
        await update.message.reply_text("❌ বৈধ নম্বর (10+ ডিজিট):")
        return States.WAITING_BKASH_NUMBER

    balance = get_user_balance(update.effective_user.id)
    if balance < MIN_WITHDRAWAL:
        await update.message.reply_text(f"❌ ন্যূনতম {MIN_WITHDRAWAL} টাকা প্রয়োজন।")
        return ConversationHandler.END

    context.user_data["bkash_number"] = bkash_number
    context.user_data["withdrawal_amount"] = balance

    keyboard = [
        [InlineKeyboardButton("✅ নিশ্চিত করুন", callback_data="confirm_withdrawal")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="back_menu")],
    ]

    text = f"💳 উত্তোলন নিশ্চিত করুন\n\nপরিমাণ: {balance} টাকা\nBkash: {bkash_number}"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return States.CONFIRMING_WITHDRAWAL


async def confirm_withdrawal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_withdrawal":
        user = query.from_user
        bkash_number = context.user_data.get("bkash_number")
        amount = context.user_data.get("withdrawal_amount")

        withdrawal_id = add_withdrawal_request(user.id, amount, bkash_number)

        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"💳 নতুন উত্তোলন অনুরোধ!\n\nব্যবহারকারী: @{user.username or user.first_name}\n"
                f"পরিমাণ: {amount} টাকা\nBkash: {bkash_number}\nঅনুরোধ ID: {withdrawal_id}"
            )
        except Exception as e:
            logger.error(f"Admin notify error: {e}")

        await query.edit_message_text(
            f"✅ উত্তোলন অনুরোধ পাঠানো হয়েছে! (ID: {withdrawal_id})\n\n⏳ অনুমোদনের জন্য অপেক্ষা করুন"
        )

    await start(update, context)
    return ConversationHandler.END


# ---------- Handlers: Admin panel ----------

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("📝 পেন্ডিং GMAIL", callback_data="admin_scripts")],
        [InlineKeyboardButton("💳 উত্তোলন", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("👥 ইউজার ম্যানেজ", callback_data="admin_users")],
        [InlineKeyboardButton("✏️ ইউজার ব্যালেন্স এডিট", callback_data="admin_edit_balance_start")],
        [InlineKeyboardButton("💬 ইউজারকে মেসেজ পাঠান", callback_data="admin_msg_user_start")],
        [InlineKeyboardButton("📢 সব ইউজারকে ঘোষণা", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("🚫 ইউজার ব্লক/আনব্লক", callback_data="admin_block_start")],
        [InlineKeyboardButton("🔴 বট বন্ধ", callback_data="bot_stop")],
        [InlineKeyboardButton("🟢 বট চালু", callback_data="bot_start")],
    ]
    await update.message.reply_text("⚙️ Admin Panel", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_scripts(query, context):
    pending = get_pending_scripts()

    if not pending:
        await query.edit_message_text("কোনো পেন্ডিং GMAIL নেই।")
        return

    for i, (script_id, user_id, title, script_text, submitted_at, status) in enumerate(pending):
        preview = script_text[:150] if len(script_text) > 150 else script_text
        text = f"[{i+1}/{len(pending)}] ID: {script_id}\nGMAIL: {title}\nPASSWORD: {preview}...\n\nজমা: {submitted_at}"

        keyboard = [
            [
                InlineKeyboardButton(f"✅ অনুমোদন {script_id}", callback_data=f"approve_{script_id}_{user_id}"),
                InlineKeyboardButton(f"❌ প্রত্যাখ্যান {script_id}", callback_data=f"reject_{script_id}")
            ]
        ]

        if i == 0:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await context.bot.send_message(query.from_user.id, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_withdrawals(query, context):
    pending_ws = get_pending_withdrawals()

    if not pending_ws:
        await query.edit_message_text("কোনো পেন্ডিং নেই।")
        return

    text = f"💳 পেন্ডিং উত্তোলন ({len(pending_ws)} টি):\n\n"
    keyboard = []

    for withdrawal_id, user_id, amount, bkash_number, requested_at, status in pending_ws:
        text += f"[ID {withdrawal_id}] {amount} টাকা → {bkash_number}\n\n"
        keyboard.append([InlineKeyboardButton("✅ অনুমোদন", callback_data=f"approve_w_{withdrawal_id}")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_users(query, context):
    users = get_all_users()

    if not users:
        await query.edit_message_text("কোনো ইউজার নেই।")
        return

    text = "👥 সকল ইউজার:\n\n"
    for user_id, username, balance, total_sold, is_blocked in users[:20]:
        blocked_mark = " 🚫[ব্লকড]" if is_blocked else ""
        text += f"ID: {user_id} | @{username}{blocked_mark}\nব্যালেন্স: {balance} | বিক্রিত: {total_sold}\n\n"

    if len(users) > 20:
        text += f"... আরও {len(users) - 20} জন"

    keyboard = [[InlineKeyboardButton("← ফিরে যান", callback_data="admin_back")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ---------- Admin: Edit Balance (button flow) ----------

async def admin_edit_balance_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("👤 যে ইউজারের ব্যালেন্স পরিবর্তন করবেন তার User ID পাঠান:\n\n(বাতিল করতে /start লিখুন)")
    return States.ADMIN_EDIT_USER_ID


async def admin_edit_balance_get_userid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ সঠিক সংখ্যার User ID পাঠান।")
        return States.ADMIN_EDIT_USER_ID

    user_info = get_user_info(user_id)
    if not user_info:
        get_or_create_user(user_id, None)
        current_balance = 0
    else:
        current_balance = user_info[2]

    context.user_data["edit_target_user"] = user_id
    await update.message.reply_text(
        f"👤 User ID: {user_id}\nবর্তমান ব্যালেন্স: {current_balance} টাকা\n\n➡️ নতুন ব্যালেন্স লিখুন (শুধু সংখ্যা):"
    )
    return States.ADMIN_EDIT_BALANCE


async def admin_edit_balance_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_balance = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ সংখ্যা লিখুন।")
        return States.ADMIN_EDIT_BALANCE

    user_id = context.user_data.get("edit_target_user")
    user_info = get_user_info(user_id)
    old_balance = user_info[2] if user_info else 0

    update_user_balance(user_id, new_balance)

    await update.message.reply_text(
        f"✅ সম্পন্ন!\n\n👤 User ID: {user_id}\n📊 আগে: {old_balance} টাকা\n📊 এখন: {new_balance} টাকা"
    )

    try:
        await context.bot.send_message(
            user_id, f"⚠️ আপনার ব্যালেন্স অ্যাডমিন কর্তৃক আপডেট করা হয়েছে। নতুন ব্যালেন্স: {new_balance} টাকা"
        )
    except Exception as e:
        logger.error(f"User notify error: {e}")

    context.user_data.pop("edit_target_user", None)
    return ConversationHandler.END


# ---------- Admin: Message a specific user (button flow) ----------

async def admin_msg_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("👤 যাকে মেসেজ পাঠাবেন তার User ID পাঠান:\n\n(বাতিল করতে /start লিখুন)")
    return States.ADMIN_MESSAGE_USER_ID


async def admin_msg_get_userid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ সঠিক সংখ্যার User ID পাঠান।")
        return States.ADMIN_MESSAGE_USER_ID

    context.user_data["msg_target_user"] = user_id
    await update.message.reply_text(f"✅ User ID: {user_id}\n\n✏️ এখন মেসেজ লিখুন:")
    return States.ADMIN_MESSAGE_TEXT


async def admin_msg_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get("msg_target_user")
    message_text = update.message.text

    try:
        await context.bot.send_message(user_id, f"📬 Admin Message:\n\n{message_text}")
        await update.message.reply_text(f"✅ মেসেজ পাঠানো হয়েছে ইউজার {user_id} কে।")
    except Exception as e:
        await update.message.reply_text(f"❌ পাঠানো যায়নি: {e}")

    context.user_data.pop("msg_target_user", None)
    return ConversationHandler.END


# ---------- Admin: Broadcast to all users (button flow) ----------

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("📢 সকল ইউজারকে যে মেসেজ পাঠাবেন তা লিখুন:\n\n(বাতিল করতে /start লিখুন)")
    return States.ADMIN_BROADCAST_TEXT


async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    users = get_all_users()
    sent, failed = 0, 0

    for user_id, username, balance, total_sold, is_blocked in users:
        try:
            await context.bot.send_message(user_id, f"📢 ঘোষণা:\n\n{message_text}")
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Broadcast সম্পন্ন!\n\n📨 পাঠানো হয়েছে: {sent} জন\n❌ ব্যর্থ (বট ব্লক করেছে): {failed} জন"
    )
    return ConversationHandler.END


# ---------- Admin: Reject with reason (button flow) ----------

REJECT_REASONS = {
    "reason_invalid": "আপনার ইমেইলটি সঠিক নয়।",
    "reason_notworking": "আপনার ইমেইলটি কাজ করছে না।",
    "reason_issue": "আপনার ইমেইলে কারিগরি সমস্যা রয়েছে।",
}


async def reject_script_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    script_id = int(query.data.split("_")[1])
    row = get_script(script_id)
    if not row or row[4] != "pending":
        await query.answer("ইতিমধ্যে প্রসেস।", show_alert=True)
        return ConversationHandler.END

    context.user_data["reject_script_id"] = script_id
    context.user_data["reject_user_id"] = row[1]

    keyboard = [
        [InlineKeyboardButton("❌ ইমেইলটি সঠিক নয়", callback_data="reason_invalid")],
        [InlineKeyboardButton("❌ ইমেইলটি কাজ করছে না", callback_data="reason_notworking")],
        [InlineKeyboardButton("❌ ইমেইলে সমস্যা আছে", callback_data="reason_issue")],
        [InlineKeyboardButton("✏️ কাস্টম কারণ লিখুন", callback_data="reason_custom")],
    ]
    await query.edit_message_text("প্রত্যাখ্যানের কারণ বেছে নিন:", reply_markup=InlineKeyboardMarkup(keyboard))
    return States.ADMIN_REJECT_REASON


async def reject_script_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    script_id = context.user_data.get("reject_script_id")
    user_id = context.user_data.get("reject_user_id")

    if query.data == "reason_custom":
        await query.edit_message_text("✏️ প্রত্যাখ্যানের কারণ লিখে পাঠান:")
        return States.ADMIN_REJECT_REASON

    reason = REJECT_REASONS.get(query.data, "আপনার Gmail টি অনুমোদিত হয়নি।")
    reject_script(script_id)

    try:
        await context.bot.send_message(
            user_id, f"❌ আপনার GMAIL (ID: {script_id}) প্রত্যাখ্যান করা হয়েছে।\n\nকারণ: {reason}"
        )
    except Exception:
        pass

    await query.edit_message_text(f"✅ প্রত্যাখ্যান সম্পন্ন। (ID: {script_id})")
    context.user_data.pop("reject_script_id", None)
    context.user_data.pop("reject_user_id", None)
    return ConversationHandler.END


async def reject_script_custom_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    script_id = context.user_data.get("reject_script_id")
    user_id = context.user_data.get("reject_user_id")
    reason = update.message.text.strip()

    reject_script(script_id)

    try:
        await context.bot.send_message(
            user_id, f"❌ আপনার GMAIL (ID: {script_id}) প্রত্যাখ্যান করা হয়েছে।\n\nকারণ: {reason}"
        )
    except Exception:
        pass

    await update.message.reply_text(f"✅ প্রত্যাখ্যান সম্পন্ন। (ID: {script_id})")
    context.user_data.pop("reject_script_id", None)
    context.user_data.pop("reject_user_id", None)
    return ConversationHandler.END


# ---------- Admin: Block/Unblock user (button flow) ----------

async def admin_block_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("👤 যাকে ব্লক/আনব্লক করবেন তার User ID পাঠান:\n\n(বাতিল করতে /start লিখুন)")
    return States.ADMIN_BLOCK_USER_ID


async def admin_block_get_userid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ সঠিক সংখ্যার User ID পাঠান।")
        return States.ADMIN_BLOCK_USER_ID

    context.user_data["block_target_user"] = user_id

    keyboard = [
        [InlineKeyboardButton("🚫 ব্লক করুন", callback_data="block_do_block")],
        [InlineKeyboardButton("✅ আনব্লক করুন", callback_data="block_do_unblock")],
    ]
    await update.message.reply_text(
        f"✅ User ID: {user_id}\n\nএই ইউজারকে ব্লক করবেন নাকি আনব্লক করবেন?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return States.ADMIN_BLOCK_ACTION


async def admin_block_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = context.user_data.get("block_target_user")

    if query.data == "block_do_block":
        block_user(user_id)
        await query.edit_message_text(f"✅ ব্লক সম্পন্ন!\n\nUser ID: {user_id} এখন ব্লক করা হয়েছে।")
        try:
            await context.bot.send_message(user_id, BLOCKED_MESSAGE, reply_markup=ReplyKeyboardRemove())
        except Exception as e:
            logger.error(f"Block notify error: {e}")
    else:
        unblock_user(user_id)
        await query.edit_message_text(f"✅ আনব্লক সম্পন্ন!\n\nUser ID: {user_id} এখন আনব্লক করা হয়েছে।")
        try:
            await context.bot.send_message(user_id, UNBLOCKED_MESSAGE)
        except Exception as e:
            logger.error(f"Unblock notify error: {e}")

    context.user_data.pop("block_target_user", None)
    return ConversationHandler.END


# ---------- Generic button callback ----------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_menu":
        await start(update, context)
    elif query.data == "admin_back":
        await admin_handler(update, context)
    elif query.data == "admin_scripts":
        await show_admin_scripts(query, context)
    elif query.data == "admin_withdrawals":
        await show_admin_withdrawals(query, context)
    elif query.data == "admin_users":
        await show_admin_users(query, context)
    elif query.data == "bot_stop":
        set_bot_status(False)
        await context.bot.send_message(ADMIN_ID, "🔴 বট বন্ধ করা হয়েছে।")
        await query.answer("বট বন্ধ।", show_alert=True)
    elif query.data == "bot_start":
        set_bot_status(True)
        await context.bot.send_message(ADMIN_ID, "🟢 বট চালু করা হয়েছে।")
        await query.answer("বট চালু।", show_alert=True)
    elif query.data.startswith("approve_w_"):
        withdrawal_id = int(query.data.split("_")[2])
        success = approve_withdrawal(withdrawal_id)

        if success:
            result = get_withdrawal(withdrawal_id)
            if result:
                user_id, amount, bkash_number = result
                try:
                    await context.bot.send_message(
                        user_id,
                        f"✅ আপনার উত্তোলন অনুমোদিত হয়েছে!\n\n{amount} টাকা আপনার Bkash নম্বরে পাঠানো হবে।\nBkash: {bkash_number}"
                    )
                except Exception:
                    pass
            await query.answer("✅ Done", show_alert=True)
        else:
            await query.answer("❌ Error", show_alert=True)

        await show_admin_withdrawals(query, context)
    elif query.data.startswith("approve_"):
        parts = query.data.split("_")
        script_id = int(parts[1])
        user_id = int(parts[2])

        row = get_script(script_id)
        if not row or row[4] != "pending":
            await query.answer("ইতিমধ্যে প্রসেস।", show_alert=True)
            return

        approve_script(script_id, user_id)

        try:
            await context.bot.send_message(
                user_id,
                f"🎉 অভিনন্দন!\n\n✅ আপনার GMAIL অনুমোদিত হয়েছে।\n💰 আপনার অ্যাকাউন্টে {SCRIPT_PRICE} টাকা যোগ করা হয়েছে।\n\nধন্যবাদ।"
            )
        except Exception as e:
            logger.error(f"Notify user error: {e}")
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"⚠️ script {script_id} অনুমোদিত হয়েছে ও ব্যালেন্স যোগ হয়েছে, কিন্তু ইউজার {user_id}-কে "
                    f"নোটিফাই করা যায়নি (হয়তো বট ব্লক করা): {e}"
                )
            except Exception:
                pass

        await query.answer("✅ Done", show_alert=True)
        await show_admin_scripts(query, context)
    # নোট: "reject_" callback_data এখন admin_reject_conv (ConversationHandler) হ্যান্ডেল করে।


def main():
    init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    script_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text([SELL_BTN]), start_sell)],
        states={
            States.WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title)],
            States.WAITING_SCRIPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_script)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
    )

    bkash_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text([WITHDRAWAL_BTN]), start_withdrawal)],
        states={
            States.WAITING_BKASH_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bkash_number)],
            States.CONFIRMING_WITHDRAWAL: [CallbackQueryHandler(confirm_withdrawal_callback)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
    )

    admin_balance_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_edit_balance_start, pattern="^admin_edit_balance_start$")],
        states={
            States.ADMIN_EDIT_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_balance_get_userid)],
            States.ADMIN_EDIT_BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_balance_apply)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
    )

    admin_msg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_msg_user_start, pattern="^admin_msg_user_start$")],
        states={
            States.ADMIN_MESSAGE_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_msg_get_userid)],
            States.ADMIN_MESSAGE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_msg_send)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
    )

    admin_broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast_start$")],
        states={
            States.ADMIN_BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
    )

    admin_reject_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reject_script_start, pattern="^reject_")],
        states={
            States.ADMIN_REJECT_REASON: [
                CallbackQueryHandler(reject_script_reason_callback, pattern="^reason_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reject_script_custom_reason),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
    )

    admin_block_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_block_start, pattern="^admin_block_start$")],
        states={
            States.ADMIN_BLOCK_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_block_get_userid)],
            States.ADMIN_BLOCK_ACTION: [CallbackQueryHandler(admin_block_apply, pattern="^block_do_")],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(script_conv)
    app.add_handler(bkash_conv)
    # গুরুত্বপূর্ণ: admin conversation handler-গুলো অবশ্যই button_callback-এর আগে থাকতে হবে
    app.add_handler(admin_balance_conv)
    app.add_handler(admin_msg_conv)
    app.add_handler(admin_broadcast_conv)
    app.add_handler(admin_reject_conv)
    app.add_handler(admin_block_conv)
    app.add_handler(MessageHandler(filters.Text([BALANCE_BTN]), balance_handler))
    app.add_handler(MessageHandler(filters.Text([ADMIN_BTN]), admin_handler))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("✅ Bot started successfully!")
    app.run_polling()


if __name__ == "__main__":
    main()
