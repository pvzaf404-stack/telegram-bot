"""
Video Script Selling Bot
-------------------------
Features:
- Users submit a video script (title + script text) to sell (fixed price: 15 taka each)
- Admin approves/rejects submitted scripts from an in-Telegram Admin Panel
- User balance increases automatically on approval
- Withdrawal via Bkash (minimum balance required to withdraw)
- Admin gets notified instantly on Telegram (no email/Gmail involved anywhere)
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

# Windows asyncio fix
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Python 3.14 fix: asyncio no longer auto-creates an event loop in the
# main thread, but python-telegram-bot's run_polling() still expects one
# via asyncio.get_event_loop(). Create and set it explicitly.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# ---------- Setup ----------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = 8669242020  # আপনার Admin ID
SCRIPT_PRICE = int(os.getenv("SCRIPT_PRICE", os.getenv("PHOTO_PRICE", "15")))
MIN_WITHDRAWAL = int(os.getenv("MIN_WITHDRAWAL", "100"))

DB_PATH = os.path.join(os.path.dirname(__file__), "scripts.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- States for ConversationHandler ----------

class States(Enum):
    WAITING_TITLE = 1
    WAITING_SCRIPT = 2
    WAITING_BKASH_NUMBER = 3
    CONFIRMING_WITHDRAWAL = 4


# Persistent bottom-menu button labels (must match exactly, used for text matching)
SELL_BTN = "📝 Sell Gmail"
BALANCE_BTN = "💰 Balance"
WITHDRAWAL_BTN = "💸 Withdrawal"
ADMIN_BTN = "⚙️ Admin Panel"


# ---------- Database ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Users table
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

    # Scripts table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scripts (
            script_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            script_text TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            submitted_at TEXT,
            approved_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)

    # Withdrawal requests table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            withdrawal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER,
            bkash_number TEXT,
            status TEXT DEFAULT 'pending',
            requested_at TEXT,
            approved_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
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
        cur.execute(
            "INSERT INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
            (user_id, username, datetime.now().isoformat())
        )
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
    cur.execute(
        "INSERT INTO scripts (user_id, title, script_text, submitted_at) VALUES (?, ?, ?, ?)",
        (user_id, title, script_text, datetime.now().isoformat())
    )
    conn.commit()
    script_id = cur.lastrowid
    conn.close()
    return script_id


def get_pending_scripts():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.script_id, s.user_id, u.username, s.title, s.script_text, s.submitted_at
        FROM scripts s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.status = 'pending'
        ORDER BY s.submitted_at ASC
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

    # Update script status
    cur.execute(
        "UPDATE scripts SET status = 'approved', approved_at = ? WHERE script_id = ?",
        (datetime.now().isoformat(), script_id)
    )

    # Add balance to user
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


def add_withdrawal_request(user_id, amount, bkash_number):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Update user's bkash number
    cur.execute("UPDATE users SET bkash_number = ? WHERE user_id = ?", (bkash_number, user_id))

    # Add withdrawal request
    cur.execute(
        "INSERT INTO withdrawals (user_id, amount, bkash_number, requested_at) VALUES (?, ?, ?, ?)",
        (user_id, amount, bkash_number, datetime.now().isoformat())
    )
    conn.commit()
    withdrawal_id = cur.lastrowid
    conn.close()
    return withdrawal_id


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
        SELECT w.withdrawal_id, w.user_id, u.username, w.amount, w.bkash_number, w.requested_at
        FROM withdrawals w
        JOIN users u ON w.user_id = u.user_id
        WHERE w.status = 'pending'
        ORDER BY w.requested_at ASC
    """)
    results = cur.fetchall()
    conn.close()
    return results


def approve_withdrawal(withdrawal_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE withdrawals SET status = 'approved', approved_at = ? WHERE withdrawal_id = ?",
        (datetime.now().isoformat(), withdrawal_id)
    )

    # Deduct from user balance
    cur.execute("SELECT user_id, amount FROM withdrawals WHERE withdrawal_id = ?", (withdrawal_id,))
    user_id, amount = cur.fetchone()
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))

    conn.commit()
    conn.close()


# ---------- Telegram Handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Works both as /start command AND as a callback (e.g. 'back to menu')."""
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

    # Note: a persistent ReplyKeyboardMarkup can only be attached to a NEW
    # message (Telegram doesn't allow editing an inline message into a
    # reply keyboard), so we always send fresh rather than edit.
    if query:
        await query.answer()
        await query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

    return ConversationHandler.END


# ---------- Sell / Script submission flow ----------

async def start_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 নতুন Gmail বিক্রি করুন\n\n"
        "প্রথমে আপনার Gmail লিখে পাঠান:"
    )
    return States.WAITING_TITLE


async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        await update.message.reply_text("❌ শুধু টেক্সট আকারে টাইটেল পাঠান।")
        return States.WAITING_TITLE

    context.user_data["script_title"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ GMAIL রেকর্ড করা হয়েছে।\n\n"
        "এখন PASSWORD লিখে পাঠান:"
    )
    return States.WAITING_SCRIPT


async def handle_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        await update.message.reply_text("❌ শুধু টেক্সট আকারে GMAIL পাঠান।")
        return States.WAITING_SCRIPT

    user = update.effective_user
    title = context.user_data.get("script_title", "(কোনো টাইটেল নেই)")
    script_text = update.message.text.strip()

    script_id = add_script(user.id, title, script_text)
    context.user_data.pop("script_title", None)

    # Notify admin directly on Telegram
    try:
        preview = script_text if len(script_text) <= 200 else script_text[:200] + "..."
        await context.bot.send_message(
            ADMIN_ID,
            f"📝 নতুন GMAIL জমা পড়েছে!\n\n"
            f"ব্যবহারকারী: @{user.username or user.first_name}\n"
            f"GMAIL ID: {script_id}\n"
            f"PASSWORD: {title}\n"
            f"প্রিভিউ: {preview}\n\n"
            f"Admin Panel এ গিয়ে অনুমোদন করুন।"
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

    keyboard = [[InlineKeyboardButton("← মেনুতে ফিরে যান", callback_data="back_menu")]]

    await update.message.reply_text(
        f"✅ GMAIL জমা হয়েছে! (ID: {script_id})\n\n"
        f"আমরা শীঘ্রই এটি পর্যালোচনা করব।\n"
        f"⏳ অনুমোদনের জন্য সর্বোচ্চ ৩০ মিনিট অপেক্ষা করুন।",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return ConversationHandler.END


# ---------- Withdrawal flow ----------

async def start_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balance = get_user_balance(update.effective_user.id)

    if balance < MIN_WITHDRAWAL:
        await update.message.reply_text(
            f"❌ উত্তোলনের জন্য সর্বনিম্ন {MIN_WITHDRAWAL} টাকা ব্যালেন্স থাকতে হবে।\n\n"
            f"আপনার বর্তমান ব্যালেন্স: {balance} টাকা।"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"💸 উত্তোলন অনুরোধ\n\n"
        f"আপনার বর্তমান ব্যালেন্স: {balance} টাকা\n\n"
        f"আপনার Bkash নম্বর পাঠান:"
    )
    return States.WAITING_BKASH_NUMBER


async def handle_bkash_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bkash_number = update.message.text.strip()

    # Simple validation
    if not bkash_number.isdigit() or len(bkash_number) < 10:
        await update.message.reply_text("❌ বৈধ Bkash নম্বর পাঠান (10+ ডিজিট)।")
        return States.WAITING_BKASH_NUMBER

    balance = get_user_balance(user.id)

    # Re-check minimum in case balance changed mid-flow
    if balance < MIN_WITHDRAWAL:
        await update.message.reply_text(
            f"❌ উত্তোলনের জন্য সর্বনিম্ন {MIN_WITHDRAWAL} টাকা ব্যালেন্স থাকতে হবে। "
            f"আপনার বর্তমান ব্যালেন্স: {balance} টাকা।"
        )
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

        # Notify admin directly on Telegram
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"💳 নতুন উত্তোলন অনুরোধ এসেছে!\n\n"
                f"ব্যবহারকারী: @{user.username or user.first_name}\n"
                f"পরিমাণ: {amount} টাকা\n"
                f"Bkash: {bkash_number}\n\n"
                f"Admin Panel এ গিয়ে অনুমোদন করুন।"
            )
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")

        await query.edit_message_text(
            f"✅ উত্তোলন অনুরোধ পাঠানো হয়েছে!\n\n"
            f"আপনার অনুরোধ প্রক্রিয়া করা হচ্ছে।\n"
            f"⏳ অনুমোদনের জন্য সর্বোচ্চ ৩০ মিনিট অপেক্ষা করুন।"
        )

    await start(update, context)
    return ConversationHandler.END


# ---------- Balance / Admin panel (persistent bottom-menu buttons) ----------

async def balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_user_balance(user_id)
    user_info = get_user_info(user_id)
    total_sold = user_info[3] if user_info else 0

    text = (
        f"💰 আপনার অ্যাকাউন্ট\n\n"
        f"বর্তমান ব্যালেন্স: {balance} টাকা\n"
        f"মোট বিক্রিত স্ক্রিপ্ট: {total_sold} টি\n"
        f"সর্বমোট আয়: {total_sold * SCRIPT_PRICE} টাকা\n\n"
        f"💸 উত্তোলনের জন্য সর্বনিম্ন ব্যালেন্স লাগবে: {MIN_WITHDRAWAL} টাকা"
    )
    await update.message.reply_text(text)


async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("📝 পেন্ডিং GMAIL", callback_data="admin_scripts")],
        [InlineKeyboardButton("💳 উত্তোলন অনুরোধ", callback_data="admin_withdrawals")],
    ]
    await update.message.reply_text("⚙️ Admin Panel", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_scripts(query, context):
    pending = get_pending_scripts()
    if not pending:
        await query.edit_message_text("কোনো পেন্ডিং GMAIL নেই।")
        return

    # Telegram messages are capped at ~4096 chars; only list the oldest few
    # in the summary and let the admin approve/reject one at a time.
    text = "📝 পেন্ডিং GMAIL:\n\n"
    keyboard = []

    for script_id, user_id, username, title, script_text, submitted_at in pending[:10]:
        preview = script_text if len(script_text) <= 120 else script_text[:120] + "..."
        text += f"[ID {script_id}] @{username}\nটাইটেল: {title}\nস্ক্রিপ্ট: {preview}\nজমা: {submitted_at}\n\n"

        keyboard.append([
            InlineKeyboardButton(f"✅ অনুমোদন {script_id}", callback_data=f"approve_{script_id}_{user_id}"),
            InlineKeyboardButton(f"❌ প্রত্যাখ্যান {script_id}", callback_data=f"reject_{script_id}")
        ])

    if len(pending) > 10:
        text += f"... আরও {len(pending) - 10} টি পেন্ডিং আছে, এগুলো অনুমোদনের পর দেখা যাবে।"

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_withdrawals(query, context):
    pending_ws = get_pending_withdrawals()
    if not pending_ws:
        await query.edit_message_text("কোনো পেন্ডিং উত্তোলন অনুরোধ নেই।")
        return

    text = "💳 পেন্ডিং উত্তোলন:\n\n"
    keyboard = []

    for withdrawal_id, user_id, username, amount, bkash_number, requested_at in pending_ws:
        text += f"[ID {withdrawal_id}] @{username}\n"
        text += f"  পরিমাণ: {amount} টাকা\n"
        text += f"  Bkash: {bkash_number}\n"
        text += f"  অনুরোধ: {requested_at}\n\n"

        keyboard.append([InlineKeyboardButton(f"✅ অনুমোদন {withdrawal_id}", callback_data=f"approve_w_{withdrawal_id}")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles admin sub-panel callback buttons: script/withdrawal lists, approve/reject,
    plus the generic 'back to menu' button shown after a submission."""
    query = update.callback_query
    await query.answer()

    if query.data == "back_menu":
        await start(update, context)

    elif query.data == "admin_scripts":
        await show_admin_scripts(query, context)

    elif query.data == "admin_withdrawals":
        await show_admin_withdrawals(query, context)

    elif query.data.startswith("approve_w_"):
        withdrawal_id = int(query.data.split("_")[2])

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id, amount, bkash_number, status FROM withdrawals WHERE withdrawal_id = ?", (withdrawal_id,))
        row = cur.fetchone()
        conn.close()

        if not row or row[3] != "pending":
            await query.answer("এই অনুরোধটি ইতিমধ্যে প্রসেস করা হয়েছে।", show_alert=True)
            await show_admin_withdrawals(query, context)
            return

        user_id, amount, bkash_number, _ = row
        approve_withdrawal(withdrawal_id)

        await context.bot.send_message(
            user_id,
            f"✅ আপনার উত্তোলন অনুমোদিত হয়েছে!\n\n"
            f"{amount} টাকা আপনার Bkash নম্বরে পাঠানো হবে।\n"
            f"Bkash: {bkash_number}"
        )

        await query.answer("✅ উত্তোলন অনুমোদিত হয়েছে।", show_alert=True)
        await show_admin_withdrawals(query, context)

    elif query.data.startswith("approve_"):
        parts = query.data.split("_")
        script_id = int(parts[1])
        user_id = int(parts[2])

        row = get_script(script_id)
        if not row or row[4] != "pending":
            await query.answer("এই স্ক্রিপ্টটি ইতিমধ্যে প্রসেস করা হয়েছে।", show_alert=True)
            await show_admin_scripts(query, context)
            return

        approve_script(script_id, user_id)

        await context.bot.send_message(
            user_id,
            f"✅ আপনার স্ক্রিপ্ট অনুমোদিত হয়েছে!\n\n"
            f"আপনার অ্যাকাউন্টে {SCRIPT_PRICE} টাকা যোগ হয়েছে।"
        )

        await query.answer("✅ স্ক্রিপ্ট অনুমোদিত হয়েছে।", show_alert=True)
        await show_admin_scripts(query, context)

    elif query.data.startswith("reject_"):
        script_id = int(query.data.split("_")[1])

        row = get_script(script_id)
        if not row or row[4] != "pending":
            await query.answer("এই স্ক্রিপ্টটি ইতিমধ্যে প্রসেস করা হয়েছে।", show_alert=True)
            await show_admin_scripts(query, context)
            return

        reject_script(script_id)

        try:
            await context.bot.send_message(
                row[1],
                f"❌ দুঃখিত, আপনার স্ক্রিপ্ট (ID: {script_id}) প্রত্যাখ্যাত হয়েছে।"
            )
        except Exception as e:
            logger.error(f"Failed to notify user of rejection: {e}")

        await query.answer("❌ স্ক্রিপ্ট প্রত্যাখ্যান করা হয়েছে।", show_alert=True)
        await show_admin_scripts(query, context)


# ---------- Main ----------

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing in .env")

    init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Script submission conversation — entry point is the persistent
    # bottom-menu "Sell Script" text button (a real message), not an inline callback
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

    # Bkash withdrawal conversation — entry point is the persistent
    # bottom-menu "Withdrawal" text button
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

    # Persistent bottom-menu buttons that don't need a multi-step conversation
    app.add_handler(MessageHandler(filters.Text([BALANCE_BTN]), balance_handler))
    app.add_handler(MessageHandler(filters.Text([ADMIN_BTN]), admin_handler))

    # Admin sub-panel inline buttons (script/withdrawal lists, approve/reject, back-to-menu)
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
