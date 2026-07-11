"""
Video Script Selling Bot - FIXED VERSION
Search/Display issue fixed for pending scripts/withdrawals
"""

import os
import sqlite3
import logging
import asyncio
import sys
from datetime import datetime
from enum import Enum

from dotenv import load_dotenv
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

# Python 3.14 fix
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Setup
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = 8669242020
SCRIPT_PRICE = int(os.getenv("SCRIPT_PRICE", "15"))
MIN_WITHDRAWAL = int(os.getenv("MIN_WITHDRAWAL", "100"))

DB_PATH = os.path.join(os.path.dirname(__file__), "scripts.db")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

class States(Enum):
    WAITING_TITLE = 1
    WAITING_SCRIPT = 2
    WAITING_BKASH_NUMBER = 3
    CONFIRMING_WITHDRAWAL = 4

SELL_BTN = "📝 Sell Gmail"
BALANCE_BTN = "💰 Balance"
WITHDRAWAL_BTN = "💸 Withdrawal"
ADMIN_BTN = "⚙️ Admin Panel"

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
    """Get ALL pending scripts from database"""
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
    cur.execute("UPDATE users SET balance = balance + ?, total_sold = total_sold + 1 WHERE user_id = ?",
               (SCRIPT_PRICE, user_id))
    conn.commit()
    conn.close()


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
    cur.execute("UPDATE withdrawals SET status = 'approved', approved_at = ? WHERE withdrawal_id = ?",
               (datetime.now().isoformat(), withdrawal_id))
    cur.execute("SELECT user_id, amount FROM withdrawals WHERE withdrawal_id = ?", (withdrawal_id,))
    user_id, amount = cur.fetchone()
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def get_username_by_id(user_id):
    """Get username for a user_id - fallback if user record missing"""
    user_info = get_user_info(user_id)
    if user_info:
        return user_info[1] or f"User{user_id}"
    return f"User{user_id}"


# ---------- Handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    text = (
        "👋 স্বাগতম GMAIL Selling Bot এ!\n\n"
        f"প্রতিটি অনুমোদিত GMAIL: {SCRIPT_PRICE} টাকা\n"
        f"উত্তোলনের ন্যূনতম: {MIN_WITHDRAWAL} টাকা"
    )
    
    if query:
        await query.answer()
        await query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)


async def start_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(f"✅ জমা হয়েছে! (ID: {script_id})", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END


async def start_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text(f"❌ ন্যূনতম {MIN_WITHDRAWAL} টাকা প্রয়োজন। আপনার: {balance} টাকা")
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
    ]
    await update.message.reply_text("⚙️ Admin Panel", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_scripts(query, context):
    pending = get_pending_scripts()
    
    if not pending:
        await query.edit_message_text("কোনো পেন্ডিং GMAIL নেই।")
        return
    
    # Fixed: Send each script separately to avoid Telegram's 4096 char limit
    for i, (script_id, user_id, title, script_text, submitted_at, status) in enumerate(pending):
        username = get_username_by_id(user_id)
        preview = script_text[:150] if len(script_text) > 150 else script_text
        
        text = f"[{i+1}/{len(pending)}] ID: {script_id}\n@{username}\nTITLE: {title}\nSCRIPT: {preview}...\n\nজমা: {submitted_at}"
        
        keyboard = [
            [
                InlineKeyboardButton(f"✅ অনুমোদন", callback_data=f"approve_{script_id}_{user_id}"),
                InlineKeyboardButton(f"❌ প্রত্যাখ্যান", callback_data=f"reject_{script_id}")
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
        username = get_username_by_id(user_id)
        text += f"[ID {withdrawal_id}] @{username}\n{amount} টাকা → {bkash_number}\n\n"
        keyboard.append([InlineKeyboardButton(f"✅ অনুমোদন {withdrawal_id}", callback_data=f"approve_w_{withdrawal_id}")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_menu":
        await start(update, context)
    elif query.data == "admin_scripts":
        await show_admin_scripts(query, context)
    elif query.data == "admin_withdrawals":
        await show_admin_withdrawals(query, context)
    elif query.data.startswith("approve_"):
        parts = query.data.split("_")
        script_id = int(parts[1])
        user_id = int(parts[2])
        
        row = get_script(script_id)
        if not row or row[4] != "pending":
            await query.answer("ইতিমধ্যে প্রসেস করা।", show_alert=True)
            return
        
        approve_script(script_id, user_id)
        
        try:
            await context.bot.send_message(user_id, f"✅ অনুমোদিত! {SCRIPT_PRICE} টাকা যুক্ত হয়েছে।")
        except:
            pass
        
        await query.answer("✅ Done", show_alert=True)
        await show_admin_scripts(query, context)
    
    elif query.data.startswith("reject_"):
        script_id = int(query.data.split("_")[1])
        row = get_script(script_id)
        if not row or row[4] != "pending":
            await query.answer("ইতিমধ্যে প্রসেস করা।", show_alert=True)
            return
        
        reject_script(script_id)
        await query.answer("❌ Done", show_alert=True)
        await show_admin_scripts(query, context)
    
    elif query.data.startswith("approve_w_"):
        withdrawal_id = int(query.data.split("_")[2])
        approve_withdrawal(withdrawal_id)
        
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id, amount, bkash_number FROM withdrawals WHERE withdrawal_id = ?", (withdrawal_id,))
        user_id, amount, bkash_number = cur.fetchone()
        conn.close()
        
        try:
            await context.bot.send_message(user_id, f"✅ অনুমোদিত! {amount} টাকা → {bkash_number}")
        except:
            pass
        
        await query.answer("✅ Done", show_alert=True)
        await show_admin_withdrawals(query, context)


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing in .env")
    
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
    
    logger.info("Bot starting...")
    asyncio.run(app.run_polling())


if __name__ == "__main__":
    main()
