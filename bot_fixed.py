"""
Complete Gmail Selling Bot - All Features Included
- User balance management
- Direct messaging
- 30 min wait message
- No .env needed
"""

import os
import sqlite3
import logging
import asyncio
import sys
from datetime import datetime
from enum import Enum

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
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

# Get token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_BOT_TOKEN:
    print("⚠️ TELEGRAM_BOT_TOKEN environment variable not found!")
    print("Please set it in your Blender/Replit environment")
    TELEGRAM_BOT_TOKEN = input("Enter your bot token manually: ")

ADMIN_ID = 8669242020
SCRIPT_PRICE = 15
MIN_WITHDRAWAL = 100

# DB_DIR: Render-এ Persistent Disk অ্যাড করে তার mount path এখানে
# Environment Variable হিসেবে সেট করুন (উদাহরণ: DB_DIR=/var/data)
# না দিলে আগের মতো bot ফাইলের পাশেই থাকবে (Render free/no-disk হলে প্রতি deploy-এ ডেটা মুছে যাবে)
DB_DIR = os.getenv("DB_DIR", os.path.dirname(__file__))
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "scripts.db")
BOT_STATUS_DB = os.path.join(DB_DIR, "bot_status.db")

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

SELL_BTN = "📝 Sell Gmail"
BALANCE_BTN = "💰 Balance"
WITHDRAWAL_BTN = "💸 Withdrawal"
ADMIN_BTN = "⚙️ Admin Panel"

# ---------- Bot Status Database ----------

def init_status_db():
    conn = sqlite3.connect(BOT_STATUS_DB)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS status (key TEXT PRIMARY KEY, value TEXT)")
    
    cur.execute("SELECT value FROM status WHERE key = 'bot_active'")
    if not cur.fetchone():
        cur.execute("INSERT INTO status (key, value) VALUES ('bot_active', '1')")
        conn.commit()
    
    conn.close()


def is_bot_active():
    conn = sqlite3.connect(BOT_STATUS_DB)
    cur = conn.cursor()
    cur.execute("SELECT value FROM status WHERE key = 'bot_active'")
    result = cur.fetchone()
    conn.close()
    return result[0] == '1' if result else True


def set_bot_status(active):
    conn = sqlite3.connect(BOT_STATUS_DB)
    cur = conn.cursor()
    cur.execute("UPDATE status SET value = ? WHERE key = 'bot_active'", ('1' if active else '0',))
    conn.commit()
    conn.close()


# ---------- Database ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            total_sold INTEGER DEFAULT 0,
            bkash_number TEXT,
            created_at TEXT
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scripts (
            script_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            script_text TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            submitted_at TEXT,
            approved_at TEXT
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            withdrawal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER,
            bkash_number TEXT,
            status TEXT DEFAULT 'pending',
            requested_at TEXT,
            approved_at TEXT
        )
    """)
    
    conn.commit()
    conn.close()


def get_or_create_user(user_id, username):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cur.fetchone()
    
    if user is None:
        cur.execute("INSERT INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
                   (user_id, username, datetime.now().isoformat()))
        conn.commit()
    conn.close()


def get_user_balance(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result[0] if result else 0


def add_script(user_id, title, script_text):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO scripts (user_id, title, script_text, submitted_at) VALUES (?, ?, ?, ?)",
               (user_id, title, script_text, datetime.now().isoformat()))
    conn.commit()
    script_id = cur.lastrowid
    conn.close()
    return script_id


def get_pending_scripts():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT script_id, user_id, title, script_text, submitted_at, status
        FROM scripts
        WHERE status = 'pending'
        ORDER BY submitted_at ASC
    """)
    results = cur.fetchall()
    conn.close()
    return results


def get_script(script_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT script_id, user_id, title, script_text, status FROM scripts WHERE script_id = ?", (script_id,))
    result = cur.fetchone()
    conn.close()
    return result


def approve_script(script_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("UPDATE scripts SET status = 'approved', approved_at = ? WHERE script_id = ?",
               (datetime.now().isoformat(), script_id))

    # যদি ইউজার ডাটাবেজে না থাকে (যেমন Render redeploy-তে DB রিসেট হয়ে থাকলে),
    # তাকে নতুন করে (balance=0 দিয়ে) তৈরি করি — যাতে টাকা নীরবে হারিয়ে না যায়
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
               (user_id, None, datetime.now().isoformat()))

    cur.execute("UPDATE users SET balance = balance + ?, total_sold = total_sold + 1 WHERE user_id = ?",
               (SCRIPT_PRICE, user_id))
    balance_updated = cur.rowcount > 0

    conn.commit()
    conn.close()
    return balance_updated


def reject_script(script_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE scripts SET status = 'rejected' WHERE script_id = ?", (script_id,))
    conn.commit()
    conn.close()


def get_user_info(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result


def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, balance, total_sold FROM users ORDER BY user_id DESC")
    results = cur.fetchall()
    conn.close()
    return results


def update_user_balance(user_id, new_balance):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
    conn.commit()
    conn.close()


def get_pending_withdrawals():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT withdrawal_id, user_id, amount, bkash_number, requested_at, status
        FROM withdrawals
        WHERE status = 'pending'
        ORDER BY requested_at ASC
    """)
    results = cur.fetchall()
    conn.close()
    return results


def add_withdrawal_request(user_id, amount, bkash_number):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET bkash_number = ? WHERE user_id = ?", (bkash_number, user_id))
    cur.execute("INSERT INTO withdrawals (user_id, amount, bkash_number, requested_at) VALUES (?, ?, ?, ?)",
               (user_id, amount, bkash_number, datetime.now().isoformat()))
    conn.commit()
    withdrawal_id = cur.lastrowid
    conn.close()
    return withdrawal_id


def approve_withdrawal(withdrawal_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute("SELECT user_id, amount, status FROM withdrawals WHERE withdrawal_id = ?", (withdrawal_id,))
    result = cur.fetchone()
    
    if not result:
        conn.close()
        return False
    
    user_id, amount, status = result
    
    if status != "pending":
        conn.close()
        return False
    
    cur.execute("UPDATE withdrawals SET status = 'approved', approved_at = ? WHERE withdrawal_id = ?",
               (datetime.now().isoformat(), withdrawal_id))
    
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
               (amount, user_id, amount))
    
    cur.execute("SELECT changes()")
    changes = cur.fetchone()[0]
    
    conn.commit()
    conn.close()
    
    return changes > 0


# ---------- Handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বর্তমানে বন্ধ আছে।")
        return
    
    query = update.callback_query
    user = query.from_user if query else update.effective_user
    get_or_create_user(user.id, user.username or user.first_name)
    
    keyboard = [
        [SELL_BTN, BALANCE_BTN],
        [WITHDRAWAL_BTN],
    ]
    if user.id == ADMIN_ID:
        keyboard.append([ADMIN_BTN])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    text=(
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
        return
    
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
            f"📝 নতুন GMAIL জমা!\n\nব্যবহারকারী: @{user.username or user.first_name}\nID: {script_id}\nGMAIL: {title}\nPASSWORD: {script_text[:100]}..."
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


async def edit_user_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin edit user balance"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        args = update.message.text.split()
        if len(args) < 3:
            await update.message.reply_text("❌ ফর্ম্যাট: /edituser userid newbalance\nউদাহরণ: /edituser 12345 500")
            return
        
        user_id = int(args[1])
        new_balance = int(args[2])
        
        user_info = get_user_info(user_id)
        if not user_info:
            get_or_create_user(user_id, None)
            old_balance = 0
        else:
            old_balance = user_info[2]

        update_user_balance(user_id, new_balance)
        
        await update.message.reply_text(f"✅ ইউজার {user_id} এর ব্যালেন্স আপডেট হয়েছে:\n\n📊 আগে: {old_balance} টাকা\n📊 এখন: {new_balance} টাকা")
        
        try:
            await context.bot.send_message(user_id, f"⚠️ আপনার ব্যালেন্স অ্যাডমিন দ্বারা আপডেট করা হয়েছে: {new_balance} টাকা")
        except:
            pass
    except ValueError:
        await update.message.reply_text("❌ সংখ্যা সঠিক নয়।")


async def send_message_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin send message to user"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        parts = update.message.text.split(None, 2)
        if len(parts) < 3:
            await update.message.reply_text("❌ ফর্ম্যাট: /msg userid message\nউদাহরণ: /msg 12345 আপনার অ্যাকাউন্ট সাসপেন্ড করা হয়েছে")
            return
        
        user_id = int(parts[1])
        message_text = parts[2]
        
        try:
            await context.bot.send_message(user_id, f"📬 Admin Message:\n\n{message_text}")
            await update.message.reply_text(f"✅ মেসেজ পাঠানো হয়েছে ইউজার {user_id} কে।")
        except Exception as e:
            await update.message.reply_text(f"❌ ইউজারকে পাঠানো যায়নি: {e}")
    except ValueError:
        await update.message.reply_text("❌ ইউজার ID ভুল।")


async def start_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বন্ধ আছে।")
        return
    
    balance = get_user_balance(update.effective_user.id)
    if balance < MIN_WITHDRAWAL:
        await update.message.reply_text(f"❌ ন্যূনতম {MIN_WITHDRAWAL} টাকা প্রয়োজন। আপনার: {balance} টাকা")
        return ConversationHandler.END
    
    await update.message.reply_text(f"💸 উত্তোলন\n\nব্যালেন্স: {balance} টাকা\n\nBkash নম্বর পাঠান:")
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
        [InlineKeyboardButton("✅ নিশ্চিত", callback_data="confirm_withdrawal")],
        [InlineKeyboardButton("❌ বাতিল", callback_data="back_menu")],
    ]
    
    await update.message.reply_text(f"💳 নিশ্চিত করুন\n\n{balance} টাকা → {bkash_number}", 
                                    reply_markup=InlineKeyboardMarkup(keyboard))
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
                f"💳 নতুন উত্তোলন!\n\nব্যবহারকারী: @{user.username or user.first_name}\nপরিমাণ: {amount} টাকা\nBkash: {bkash_number}"
            )
        except Exception as e:
            logger.error(f"Admin notify error: {e}")
        
        await query.edit_message_text(f"✅ অনুরোধ পাঠানো হয়েছে! (ID: {withdrawal_id})")
    
    await start(update, context)
    return ConversationHandler.END


async def balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বন্ধ।")
        return
    
    balance = get_user_balance(update.effective_user.id)
    user_info = get_user_info(update.effective_user.id)
    total_sold = user_info[3] if user_info else 0
    
    text = f"💰 আপনার অ্যাকাউন্ট\n\nব্যালেন্স: {balance} টাকা\nবিক্রিত: {total_sold} টি"
    await update.message.reply_text(text)


async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    keyboard = [
        [InlineKeyboardButton("📝 পেন্ডিং GMAIL", callback_data="admin_scripts")],
        [InlineKeyboardButton("💳 উত্তোলন", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("👥 ইউজার ম্যানেজ", callback_data="admin_users")],
        [InlineKeyboardButton("💰 ব্যালেন্স এডিট", callback_data="admin_edit_balance_start")],
        [InlineKeyboardButton("✉️ নির্দিষ্ট ইউজারকে মেসেজ", callback_data="admin_msg_user_start")],
        [InlineKeyboardButton("📢 সবাইকে মেসেজ (Broadcast)", callback_data="admin_broadcast_start")],
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
                InlineKeyboardButton("✅ অনুমোদন", callback_data=f"approve_{script_id}_{user_id}"),
                InlineKeyboardButton("❌ প্রত্যাখ্যান", callback_data=f"reject_{script_id}")
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
        keyboard.append([InlineKeyboardButton(f"✅ অনুমোদন", callback_data=f"approve_w_{withdrawal_id}")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_users(query, context):
    users = get_all_users()
    
    if not users:
        await query.edit_message_text("কোনো ইউজার নেই।")
        return
    
    text = "👥 সকল ইউজার:\n\n"
    for user_id, username, balance, total_sold in users[:20]:
        text += f"ID: {user_id} | @{username}\nব্যালেন্স: {balance} | বিক্রিত: {total_sold}\n\n"
    
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
        # ডাটাবেজে ইউজার নেই (হয়তো DB রিসেট হয়েছে) — ব্লক না করে balance=0 দিয়ে তৈরি করে এগিয়ে যাই
        get_or_create_user(user_id, None)
        await update.message.reply_text(
            f"⚠️ ইউজার {user_id} লোকাল ডাটাবেজে ছিল না, নতুন করে তৈরি করা হলো (ব্যালেন্স ০)।\n"
            f"এটা দেখলে ধরে নিন Render redeploy-তে ডাটাবেজ রিসেট হয়েছে।"
        )
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
            user_id, f"⚠️ আপনার ব্যালেন্স অ্যাডমিন কর্তৃক আপডেট করা হয়েছে।\nনতুন ব্যালেন্স: {new_balance} টাকা"
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

    user_info = get_user_info(user_id)
    if not user_info:
        # DB-তে না থাকলেও ব্লক করছি না — Telegram user_id সঠিক হলে মেসেজ ঠিকই যাবে
        await update.message.reply_text(
            f"⚠️ ইউজার {user_id} লোকাল ডাটাবেজে পাওয়া যায়নি, তবু মেসেজ পাঠানোর চেষ্টা করা হবে।"
        )

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

    for user_id, username, balance, total_sold in users:
        try:
            await context.bot.send_message(user_id, f"📢 ঘোষণা:\n\n{message_text}")
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Broadcast সম্পন্ন!\n\n📨 পাঠানো হয়েছে: {sent} জন\n❌ ব্যর্থ (বট ব্লক করেছে): {failed} জন"
    )
    if sent == 0 and failed == 0:
        await update.message.reply_text(
            "⚠️ ডাটাবেজে কোনো ইউজার পাওয়া যায়নি — সম্ভবত সাম্প্রতিক deploy/restart-এ ডাটাবেজ রিসেট হয়েছে "
            "(persistent disk বা external database না থাকলে এটা ঘটবে)।"
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
            user_id, f"❌ আপনার Gmail (ID: {script_id}) প্রত্যাখ্যান করা হয়েছে।\n\nকারণ: {reason}"
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
            user_id, f"❌ আপনার Gmail (ID: {script_id}) প্রত্যাখ্যান করা হয়েছে।\n\nকারণ: {reason}"
        )
    except Exception:
        pass

    await update.message.reply_text(f"✅ প্রত্যাখ্যান সম্পন্ন। (ID: {script_id})")
    context.user_data.pop("reject_script_id", None)
    context.user_data.pop("reject_user_id", None)
    return ConversationHandler.END


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
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT user_id, amount, bkash_number FROM withdrawals WHERE withdrawal_id = ?", (withdrawal_id,))
            result = cur.fetchone()
            conn.close()
            
            if result:
                user_id, amount, bkash_number = result
                try:
                    await context.bot.send_message(user_id, f"✅ অনুমোদিত! {amount} টাকা → {bkash_number}")
                except:
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
            await context.bot.send_message(user_id, f"✅ অনুমোদিত! {SCRIPT_PRICE} টাকা যুক্ত।")
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
    # নোট: "reject_" callback এখন admin_reject_conv (ConversationHandler) হ্যান্ডেল করে,
    # যেটা কারণ জিজ্ঞেস করে তারপর ইউজারকে জানায় — তাই এখান থেকে সরানো হয়েছে।


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("❌ Bot token not found!")
    
    init_db()
    init_status_db()
    
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(script_conv)
    app.add_handler(bkash_conv)
    # গুরুত্বপূর্ণ: নিচের ৪টা admin conversation handler অবশ্যই button_callback-এর আগে থাকতে হবে,
    # নাহলে button_callback আগে callback_data ধরে ফেলবে আর conversation state শুরুই হবে না।
    app.add_handler(admin_balance_conv)
    app.add_handler(admin_msg_conv)
    app.add_handler(admin_broadcast_conv)
    app.add_handler(admin_reject_conv)
    app.add_handler(MessageHandler(filters.Regex("^/edituser"), edit_user_balance))
    app.add_handler(MessageHandler(filters.Regex("^/msg"), send_message_to_user))
    app.add_handler(MessageHandler(filters.Text([BALANCE_BTN]), balance_handler))
    app.add_handler(MessageHandler(filters.Text([ADMIN_BTN]), admin_handler))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info("✅ Bot started successfully!")
    app.run_polling()


if __name__ == "__main__":
    main()
