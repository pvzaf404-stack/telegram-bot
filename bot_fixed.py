"""
Complete Photo Selling Bot - Supabase PostgreSQL Version
- Persistent data (never deletes on redeploy)
- User balance management
- Direct messaging
- 30 min wait message
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
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

# Get tokens
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    print("⚠️ TELEGRAM_BOT_TOKEN not found!")
    TELEGRAM_BOT_TOKEN = input("Enter your bot token: ")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("⚠️ DATABASE_URL not found!")
    DATABASE_URL = input("Enter your Supabase Connection String: ")

ADMIN_ID = 8669242020
SCRIPT_PRICE = 15
MIN_WITHDRAWAL = 100

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

# ---------- PostgreSQL Connection Helper ----------

def get_db_connection():
    """Get a new connection to Supabase PostgreSQL"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None

# ---------- Database Initialization ----------

def init_db():
    """Create tables if they don't exist"""
    conn = get_db_connection()
    if not conn:
        logger.error("Could not connect to database")
        return
    
    cur = conn.cursor()
    
    try:
        # Users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 0,
                total_sold INTEGER DEFAULT 0,
                bkash_number TEXT,
                created_at TEXT
            )
        """)
        
        # Scripts table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                script_id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                title TEXT NOT NULL,
                script_text TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                submitted_at TEXT,
                approved_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Withdrawals table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                withdrawal_id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INTEGER,
                bkash_number TEXT,
                status TEXT DEFAULT 'pending',
                requested_at TEXT,
                approved_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Bot status table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_status (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # Check if bot_active status exists
        cur.execute("SELECT value FROM bot_status WHERE key = 'bot_active'")
        if not cur.fetchone():
            cur.execute("INSERT INTO bot_status (key, value) VALUES ('bot_active', '1')")
        
        conn.commit()
        logger.info("✅ Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Error creating tables: {e}")
    finally:
        cur.close()
        conn.close()

# ---------- Bot Status Functions ----------

def is_bot_active():
    conn = get_db_connection()
    if not conn:
        return True
    
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM bot_status WHERE key = 'bot_active'")
        result = cur.fetchone()
        return result[0] == '1' if result else True
    finally:
        cur.close()
        conn.close()

def set_bot_status(active):
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    try:
        value = '1' if active else '0'
        cur.execute("UPDATE bot_status SET value = %s WHERE key = 'bot_active'", (value,))
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ---------- User Functions ----------

def get_or_create_user(user_id, username):
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        
        if user is None:
            cur.execute(
                "INSERT INTO users (user_id, username, created_at) VALUES (%s, %s, %s)",
                (user_id, username, datetime.now().isoformat())
            )
            conn.commit()
    finally:
        cur.close()
        conn.close()

def get_user_balance(user_id):
    conn = get_db_connection()
    if not conn:
        return 0
    
    cur = conn.cursor()
    try:
        cur.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        return result[0] if result else 0
    finally:
        cur.close()
        conn.close()

def get_user_info(user_id):
    conn = get_db_connection()
    if not conn:
        return None
    
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        return result
    finally:
        cur.close()
        conn.close()

def update_user_balance(user_id, new_balance):
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET balance = %s WHERE user_id = %s", (new_balance, user_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()

def get_all_users():
    conn = get_db_connection()
    if not conn:
        return []
    
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id, username, balance, total_sold FROM users ORDER BY user_id DESC")
        results = cur.fetchall()
        return results if results else []
    finally:
        cur.close()
        conn.close()

# ---------- Script Functions ----------

def add_script(user_id, title, script_text):
    conn = get_db_connection()
    if not conn:
        return None
    
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO scripts (user_id, title, script_text, submitted_at) VALUES (%s, %s, %s, %s) RETURNING script_id",
            (user_id, title, script_text, datetime.now().isoformat())
        )
        script_id = cur.fetchone()[0]
        conn.commit()
        return script_id
    finally:
        cur.close()
        conn.close()

def get_pending_scripts():
    conn = get_db_connection()
    if not conn:
        return []
    
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT script_id, user_id, title, script_text, submitted_at, status
            FROM scripts
            WHERE status = 'pending'
            ORDER BY submitted_at ASC
        """)
        results = cur.fetchall()
        return results if results else []
    finally:
        cur.close()
        conn.close()

def get_script(script_id):
    conn = get_db_connection()
    if not conn:
        return None
    
    cur = conn.cursor()
    try:
        cur.execute("SELECT script_id, user_id, title, script_text, status FROM scripts WHERE script_id = %s", (script_id,))
        result = cur.fetchone()
        return result
    finally:
        cur.close()
        conn.close()

def approve_script(script_id, user_id):
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE scripts SET status = 'approved', approved_at = %s WHERE script_id = %s",
            (datetime.now().isoformat(), script_id)
        )
        cur.execute(
            "UPDATE users SET balance = balance + %s, total_sold = total_sold + 1 WHERE user_id = %s",
            (SCRIPT_PRICE, user_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

def reject_script(script_id):
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    try:
        cur.execute("UPDATE scripts SET status = 'rejected' WHERE script_id = %s", (script_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ---------- Withdrawal Functions ----------

def get_pending_withdrawals():
    conn = get_db_connection()
    if not conn:
        return []
    
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT withdrawal_id, user_id, amount, bkash_number, requested_at, status
            FROM withdrawals
            WHERE status = 'pending'
            ORDER BY requested_at ASC
        """)
        results = cur.fetchall()
        return results if results else []
    finally:
        cur.close()
        conn.close()

def add_withdrawal_request(user_id, amount, bkash_number):
    conn = get_db_connection()
    if not conn:
        return None
    
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET bkash_number = %s WHERE user_id = %s",
            (bkash_number, user_id)
        )
        cur.execute(
            "INSERT INTO withdrawals (user_id, amount, bkash_number, requested_at) VALUES (%s, %s, %s, %s) RETURNING withdrawal_id",
            (user_id, amount, bkash_number, datetime.now().isoformat())
        )
        withdrawal_id = cur.fetchone()[0]
        conn.commit()
        return withdrawal_id
    finally:
        cur.close()
        conn.close()

def approve_withdrawal(withdrawal_id):
    conn = get_db_connection()
    if not conn:
        return False
    
    cur = conn.cursor()
    try:
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
        
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()

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
    
    text = f"👋 স্বাগতম Professional Photo Selling Bot এ!\n\nপ্রতিটি অনুমোদিত ছবি: {SCRIPT_PRICE} টাকা\n\nনিচের মেনু থেকে অপশন বেছে নিন 👇"
    
    if query:
        await query.answer()
        await query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def start_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বর্তমানে বন্ধ আছে।")
        return
    
    await update.message.reply_text(
        "📌 কীভাবে ছবি বিক্রি করবেন:\n"
        "1️⃣ Sell বাটনে ক্লিক করুন\n"
        "2️⃣ ছবির বর্ণনা লিখুন\n"
        "3️⃣ Admin approval এর অপেক্ষা করুন — অনুমোদন হলে ১৫ টাকা যোগ হবে\n\n"
        "এখন আপনার ছবি সম্পর্কে তথ্য লিখুন (যেমন: Professional portrait):"
    )
    return States.WAITING_TITLE

async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["script_title"] = update.message.text.strip()
    await update.message.reply_text("✅ তথ্য রেকর্ড করা হয়েছে।\n\nএখন ছবির বিস্তারিত বর্ণনা লিখুন:")
    return States.WAITING_SCRIPT

async def handle_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    title = context.user_data.get("script_title", "(No title)")
    script_text = update.message.text.strip()
    
    script_id = add_script(user.id, title, script_text)
    
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📸 নতুন ছবি জমা হয়েছে!\n\n"
            f"ব্যবহারকারী: @{user.username or user.first_name}\n"
            f"ID: {script_id}\n"
            f"শিরোনাম: {title}\n"
            f"বর্ণনা: {script_text[:100]}..."
        )
    except Exception as e:
        logger.error(f"Admin notify error: {e}")
    
    keyboard = [[InlineKeyboardButton("← মেনু", callback_data="back_menu")]]
    await update.message.reply_text(
        f"✅ জমা হয়েছে! (ID: {script_id})\n\n"
        f"আমরা আপনার ছবি পর্যালোচনা করছি।\n"
        f"⏳ অনুমোদনের জন্য সর্বোচ্চ ৩০ মিনিট অপেক্ষা করুন।\n\n"
        f"অনুমোদিত হলে আপনার অ্যাকাউন্টে {SCRIPT_PRICE} টাকা যোগ হবে।",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

async def start_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বর্তমানে বন্ধ আছে।")
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
        await update.message.reply_text("❌ বৈধ Bkash নম্বর পাঠান (10+ ডিজিট)।")
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
    
    await update.message.reply_text(
        f"💳 উত্তোলন নিশ্চিত করুন\n\n"
        f"পরিমাণ: {balance} টাকা\n"
        f"Bkash: {bkash_number}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
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
                f"💳 নতুন উত্তোলন অনুরোধ!\n\n"
                f"ব্যবহারকারী: @{user.username or user.first_name}\n"
                f"পরিমাণ: {amount} টাকা\n"
                f"Bkash: {bkash_number}\n"
                f"অনুরোধ ID: {withdrawal_id}"
            )
        except Exception as e:
            logger.error(f"Admin notify error: {e}")
        
        await query.edit_message_text(
            f"✅ উত্তোলন অনুরোধ পাঠানো হয়েছে! (ID: {withdrawal_id})\n\n"
            f"⏳ অনুমোদনের জন্য সর্বোচ্চ ৩০ মিনিট অপেক্ষা করুন।"
        )
    
    await start(update, context)
    return ConversationHandler.END

async def balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_active():
        await update.message.reply_text("🔴 বট বর্তমানে বন্ধ আছে।")
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

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    keyboard = [
        [InlineKeyboardButton("📸 পেন্ডিং ছবি", callback_data="admin_scripts")],
        [InlineKeyboardButton("💳 উত্তোলন অনুরোধ", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("👥 ইউজার ম্যানেজ", callback_data="admin_users")],
        [InlineKeyboardButton("🔴 বট বন্ধ", callback_data="bot_stop")],
        [InlineKeyboardButton("🟢 বট চালু", callback_data="bot_start")],
    ]
    await update.message.reply_text("⚙️ Admin Panel", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_admin_scripts(query, context):
    pending = get_pending_scripts()
    
    if not pending:
        await query.edit_message_text("কোনো পেন্ডিং ছবি নেই।")
        return
    
    for i, (script_id, user_id, title, script_text, submitted_at, status) in enumerate(pending):
        preview = script_text[:150] if len(script_text) > 150 else script_text
        text = f"[{i+1}/{len(pending)}] ID: {script_id}\nশিরোনাম: {title}\nবর্ণনা: {preview}...\n\nজমা: {submitted_at}"
        
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
        await query.edit_message_text("কোনো পেন্ডিং উত্তোলন অনুরোধ নেই।")
        return
    
    text = f"💳 পেন্ডিং উত্তোলন ({len(pending_ws)} টি):\n\n"
    keyboard = []
    
    for withdrawal_id, user_id, amount, bkash_number, requested_at, status in pending_ws:
        text += f"[ID {withdrawal_id}] {amount} টাকা → {bkash_number}\n\n"
        keyboard.append([InlineKeyboardButton(f"✅ অনুমোদন {withdrawal_id}", callback_data=f"approve_w_{withdrawal_id}")])
    
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
            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute("SELECT user_id, amount, bkash_number FROM withdrawals WHERE withdrawal_id = %s", (withdrawal_id,))
                result = cur.fetchone()
                cur.close()
                conn.close()
                
                if result:
                    user_id, amount, bkash_number = result
                    try:
                        await context.bot.send_message(user_id, f"✅ আপনার উত্তোলন অনুমোদিত হয়েছে!\n\n{amount} টাকা আপনার Bkash নম্বরে পাঠানো হবে।\nBkash: {bkash_number}")
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
            await query.answer("ইতিমধ্যে প্রসেস করা হয়েছে।", show_alert=True)
            return
        
        approve_script(script_id, user_id)
        
        try:
            await context.bot.send_message(user_id, f"✅ আপনার ছবি অনুমোদিত হয়েছে!\n\nআপনার অ্যাকাউন্টে {SCRIPT_PRICE} টাকা যোগ হয়েছে।")
        except:
            pass
        
        await query.answer("✅ Done", show_alert=True)
        await show_admin_scripts(query, context)
    elif query.data.startswith("reject_"):
        script_id = int(query.data.split("_")[1])
        row = get_script(script_id)
        if not row or row[4] != "pending":
            await query.answer("ইতিমধ্যে প্রসেস করা হয়েছে।", show_alert=True)
            return
        
        reject_script(script_id)
        await query.answer("❌ Done", show_alert=True)
        await show_admin_scripts(query, context)

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("❌ Bot token not found!")
    
    if not DATABASE_URL:
        raise SystemExit("❌ DATABASE_URL not found!")
    
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
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(script_conv)
    app.add_handler(bkash_conv)
    app.add_handler(MessageHandler(filters.Text([BALANCE_BTN]), balance_handler))
    app.add_handler(MessageHandler(filters.Text([ADMIN_BTN]), admin_handler))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info("✅ Bot started successfully with Supabase PostgreSQL!")
    asyncio.run(app.run_polling())

if __name__ == "__main__":
    main()
