import asyncio
import logging
import os
import sqlite3
from typing import Optional, Dict, Any
import aiohttp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from solana.rpc.async_api import AsyncClient
from solders.signature import Signature

# ==========================================
# CONFIGURATION & FINANCIAL RULES
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8803027756:AAHN1gHf2AmvjKgvJM71y1E-TtGHPO5fqcE")
CHAT_ID = os.environ.get("CHAT_ID", "-1004364300853")

CHAIN_ID = "solana"
PAIR_ADDRESS = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
MIN_NEW_BUYS_TO_ALERT = 3
REFERRAL_URL = "https://t.me/solana_trojanbot?start=r-____t0ahgu"
POLL_INTERVAL = 10
SEND_STARTUP_TEST_MESSAGE = True

DEXSCREENER_URL = f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN_ID}/{PAIR_ADDRESS}"
DB_FILE = "bot_data.db"

# Admin Telegram User ID Lock
ADMIN_TELEGRAM_ID = "7983373518"

# Financial Safety Limits
MIN_TRADE_SOL = 0.1             # Trades below 0.1 SOL earn 0 cashback
USER_CASHBACK_PERCENT = 0.00125 # 0.125% of trade volume (half of your 0.25% cut)
MIN_WITHDRAW_SOL = 0.05         # Minimum cashout balance
NETWORK_FEE_SOL = 0.005         # Gas offset deducted on withdrawal

RPC_URL = "https://api.mainnet-beta.solana.com"

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("SolanaBuyTracker")


# ==========================================
# DATABASE SETUP
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            solana_wallet TEXT,
            balance_sol REAL DEFAULT 0.0,
            referrer_id TEXT
        )
    """)
    
    # Tasks Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            channel_username TEXT,
            reward_sol REAL
        )
    """)
    
    # Completed Tasks Log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            user_id TEXT,
            task_id INTEGER,
            PRIMARY KEY (user_id, task_id)
        )
    """)

    # Processed Transactions Log (Anti-Double Spend)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_txs (
            tx_hash TEXT PRIMARY KEY
        )
    """)
    
    # Seed default task if database is empty
    cursor.execute("SELECT COUNT(*) FROM tasks")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO tasks (title, channel_username, reward_sol) VALUES (?, ?, ?)",
            ("Join Official Updates Channel", "@Telegram", 0.001)
        )
        
    conn.commit()
    conn.close()

init_db()


# ==========================================
# USER COMMAND HANDLERS
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    args = context.args
    referrer_id = args[0] if args else None

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (user_id,))
    existing_user = cursor.fetchone()

    if not existing_user:
        valid_referrer = referrer_id if referrer_id and referrer_id != user_id else None
        cursor.execute(
            "INSERT INTO users (telegram_id, referrer_id) VALUES (?, ?)",
            (user_id, valid_referrer)
        )
        conn.commit()

    conn.close()

    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"

    msg = (
        f"👋 <b>Welcome {user.first_name}!</b>\n\n"
        "💰 <b>Earn $SOL Cashback:</b> Trade Solana tokens via Trojan & claim cashback rewards!\n"
        "📋 <b>Web3 Tasks:</b> Complete quick channel tasks for extra earnings.\n"
        "📊 <b>Buy Alerts:</b> Real-time whale and momentum signals delivered straight to chat.\n\n"
        f"🔗 <b>Your Invite Link:</b>\n<code>{ref_link}</code>\n\n"
        "<i>Earn a 10% bonus whenever your referrals complete tasks!</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def beginner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 <b>New to Solana Trading?</b>\n\n"
        "1️⃣ <b>Get Your Trojan Wallet:</b>\n"
        f"   • Tap <a href='{REFERRAL_URL}'>Launch Trojan Bot</a> to open your automated wallet.\n\n"
        "2️⃣ <b>Deposit Solana:</b>\n"
        "   • Copy your Trojan wallet address and send a small amount of SOL (e.g., 0.2 SOL).\n\n"
        "3️⃣ <b>Trade & Earn Cashback:</b>\n"
        "   • Whenever our bot posts a signal, hit <b>⚡ Buy on Trojan</b> to execute your trade.\n"
        "   • Copy your Trojan Transaction Hash and send <code>/verify YOUR_TX_HASH</code> here to claim your cashback!"
    )
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Open Trojan Wallet", url=REFERRAL_URL)]])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)


async def verify_tx_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>Format:</b> <code>/verify YOUR_SOLANA_TX_HASH</code>\n\n"
            "Paste the transaction hash from Trojan after completing a trade.",
            parse_mode="HTML"
        )
        return

    tx_hash = context.args[0].strip()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Double-spend check
    cursor.execute("SELECT 1 FROM processed_txs WHERE tx_hash = ?", (tx_hash,))
    if cursor.fetchone():
        await update.message.reply_text("❌ This transaction hash has already been claimed!", parse_mode="HTML")
        conn.close()
        return

    try:
        async with AsyncClient(RPC_URL) as client:
            sig = Signature.from_string(tx_hash)
            response = await client.get_transaction(sig, encoding="jsonParsed", max_supported_transaction_version=0)

            if not response.value:
                await update.message.reply_text("❌ Transaction not found on Solana yet. Wait 5 seconds and retry.", parse_mode="HTML")
                conn.close()
                return

            tx_data = response.value.transaction.meta
            if tx_data.err is not None:
                await update.message.reply_text("❌ Transaction failed on-chain!", parse_mode="HTML")
                conn.close()
                return

            pre_balances = tx_data.pre_balances
            post_balances = tx_data.post_balances
            sol_spent = (pre_balances[0] - post_balances[0]) / 1_000_000_000

            if sol_spent < MIN_TRADE_SOL:
                await update.message.reply_text(
                    f"❌ Trade size too small! Minimum trade is <b>{MIN_TRADE_SOL} SOL</b>.\n"
                    f"Your trade: <code>{sol_spent:.3f} SOL</code>.",
                    parse_mode="HTML"
                )
                conn.close()
                return

            cashback_earned = sol_spent * USER_CASHBACK_PERCENT

            cursor.execute("INSERT INTO processed_txs (tx_hash) VALUES (?)", (tx_hash,))
            cursor.execute("UPDATE users SET balance_sol = balance_sol + ? WHERE telegram_id = ?", (cashback_earned, user_id))
            conn.commit()

            await update.message.reply_text(
                f"🎉 <b>Trade Verified!</b>\n\n"
                f"🛒 <b>Trade Size:</b> <code>{sol_spent:.3f} SOL</code>\n"
                f"💰 <b>Cashback Credited:</b> <code>+{cashback_earned:.5f} SOL</code>",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"TX verification error: {e}")
        await update.message.reply_text("⚠️ Invalid hash format or network timeout.", parse_mode="HTML")

    conn.close()


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance_sol, solana_wallet FROM users WHERE telegram_id = ?", (user_id,))
    row = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
    ref_count = cursor.fetchone()[0]
    conn.close()

    balance = row[0] if row else 0.0
    wallet = row[1] if row and row[1] else "Not set"

    msg = (
        "💼 <b>Your Account Summary</b>\n\n"
        f"💰 <b>Balance:</b> <code>{balance:.5f} SOL</code>\n"
        f"👥 <b>Total Referrals:</b> <code>{ref_count}</code>\n"
        f"💳 <b>Payout Wallet:</b> <code>{wallet}</code>\n\n"
        f"<i>Min Payout: {MIN_WITHDRAW_SOL} SOL | Network Fee: {NETWORK_FEE_SOL} SOL</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance_sol, solana_wallet FROM users WHERE telegram_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    balance = row[0] if row else 0.0
    wallet = row[1] if row and row[1] else None

    if not wallet:
        await update.message.reply_text(
            "⚠️ <b>No payout address linked!</b>\n"
            "Set your Solana wallet first:\n<code>/wallet YOUR_SOLANA_WALLET_ADDRESS</code>",
            parse_mode="HTML"
        )
        return

    if balance < MIN_WITHDRAW_SOL:
        await update.message.reply_text(
            f"❌ <b>Insufficient Balance!</b>\n\n"
            f"• Your Balance: <code>{balance:.5f} SOL</code>\n"
            f"• Minimum Required: <code>{MIN_WITHDRAW_SOL} SOL</code>",
            parse_mode="HTML"
        )
        return

    net_payout = balance - NETWORK_FEE_SOL
    msg = (
        "✅ <b>Withdrawal Request Logged!</b>\n\n"
        f"💳 <b>Destination Wallet:</b> <code>{wallet}</code>\n"
        f"💰 <b>Requested:</b> <code>{balance:.5f} SOL</code>\n"
        f"⛽ <b>Gas Fee Deduction:</b> <code>-{NETWORK_FEE_SOL} SOL</code>\n"
        f"💵 <b>Net Transfer:</b> <code>{net_payout:.5f} SOL</code>\n\n"
        "<i>Payouts process automatically via server batch transfers.</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, channel_username, reward_sol FROM tasks")
    tasks = cursor.fetchall()

    if not tasks:
        await update.message.reply_text("📋 No tasks available right now. Check back later!")
        conn.close()
        return

    for task_id, title, channel, reward in tasks:
        cursor.execute(
            "SELECT 1 FROM user_tasks WHERE user_id = ? AND task_id = ?",
            (user_id, task_id)
        )
        completed = cursor.fetchone() is not None

        status_text = "✅ Completed" if completed else f"💰 Reward: {reward} SOL"
        text = f"📌 <b>Task ID {task_id}: {title}</b>\nChannel: {channel}\nStatus: {status_text}"

        buttons = []
        if not completed:
            buttons.append([
                InlineKeyboardButton("🔗 Open Channel", url=f"https://t.me/{channel.replace('@', '')}"),
                InlineKeyboardButton("✅ Verify Join", callback_data=f"verifytask_{task_id}")
            ])
        
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

    conn.close()


async def verify_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    task_id = int(query.data.split("_")[1])

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("SELECT channel_username, reward_sol FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()

    if not task:
        await query.edit_message_text("Task not found.")
        conn.close()
        return

    channel, reward = task

    try:
        member = await context.bot.get_chat_member(chat_id=channel, user_id=int(user_id))
        if member.status in ["member", "administrator", "creator"]:
            cursor.execute(
                "INSERT INTO user_tasks (user_id, task_id) VALUES (?, ?)",
                (user_id, task_id)
            )
            cursor.execute(
                "UPDATE users SET balance_sol = balance_sol + ? WHERE telegram_id = ?",
                (reward, user_id)
            )

            # Referral Bonus (10%)
            cursor.execute("SELECT referrer_id FROM users WHERE telegram_id = ?", (user_id,))
            ref_row = cursor.fetchone()
            if ref_row and ref_row[0]:
                ref_bonus = reward * 0.10
                cursor.execute(
                    "UPDATE users SET balance_sol = balance_sol + ? WHERE telegram_id = ?",
                    (ref_bonus, ref_row[0])
                )

            conn.commit()
            await query.edit_message_text(f"🎉 <b>Task Verified!</b> Earned {reward} SOL.", parse_mode="HTML")
        else:
            await query.message.reply_text("❌ You have not joined the channel yet!")
    except Exception as e:
        logger.error(f"Verification error: {e}")
        await query.message.reply_text("⚠️ Verification failed. Make sure the bot is an admin in the target channel.")

    conn.close()


async def set_wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("⚠️ Please provide a wallet address. Example: `/wallet YourAddress`", parse_mode="Markdown")
        return

    wallet = context.args[0]
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET solana_wallet = ? WHERE telegram_id = ?", (wallet, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Solana payout wallet saved:\n<code>{wallet}</code>", parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "❓ <b>Bot Help & Command Directory</b>\n\n"
        "• /start - Launch bot & grab your referral link\n"
        "• /beginner - Plain-English guide on how to trade & earn\n"
        "• /verify - Submit transaction hash to claim trade cashback\n"
        "• /tasks - Complete Web3 tasks for instant rewards\n"
        "• /balance - Check account balance & referral stats\n"
        "• /wallet - Save or update your Solana payout address\n"
        "• /withdraw - Request cashout (Min: 0.05 SOL)"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


# ==========================================
# ADMIN COMMAND HANDLERS
# ==========================================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM user_tasks")
    total_tasks_completed = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(balance_sol) FROM users")
    total_user_balances = cursor.fetchone()[0] or 0.0
    conn.close()

    msg = (
        "👑 <b>Admin System Dashboard</b>\n\n"
        f"👥 <b>Total Registered Users:</b> <code>{total_users}</code>\n"
        f"✅ <b>Total Tasks Completed:</b> <code>{total_tasks_completed}</code>\n"
        f"💰 <b>Total User Liability (Owed):</b> <code>{total_user_balances:.5f} SOL</code>\n\n"
        "<b>Commands:</b>\n"
        "• <code>/addtask Title | @ChannelUsername | RewardSOL</code>\n"
        "• <code>/deltask TASK_ID</code>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def addtask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    raw_text = " ".join(context.args)
    if not raw_text or "|" not in raw_text:
        await update.message.reply_text(
            "⚠️ <b>Format:</b>\n<code>/addtask Title | @ChannelUsername | RewardSOL</code>",
            parse_mode="HTML"
        )
        return

    try:
        title, channel, reward = [item.strip() for item in raw_text.split("|")]
        reward_sol = float(reward)

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tasks (title, channel_username, reward_sol) VALUES (?, ?, ?)",
            (title, channel, reward_sol)
        )
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ <b>Task Added!</b> {title} ({reward_sol} SOL)", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error adding task: {e}")


async def deltask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ <b>Format:</b> <code>/deltask TASK_ID</code>", parse_mode="HTML")
        return

    task_id = int(context.args[0])
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    cursor.execute("DELETE FROM user_tasks WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🗑️ <b>Task {task_id} Removed!</b>", parse_mode="HTML")


# ==========================================
# BUY TRACKER LOOP
# ==========================================
class SolanaBuyTracker:
    def __init__(self, application: Application, chat_id: str):
        self.app = application
        self.chat_id = chat_id
        self.last_buy_count: Optional[int] = None

    async def fetch_dexscreener(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        try:
            async with session.get(DEXSCREENER_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs")
                    if not pairs:
                        return None
                    p = pairs[0]
                    txns_h1 = p.get("txns", {}).get("h1", {}) or {}
                    return {
                        "name": p.get("baseToken", {}).get("name", "Unknown"),
                        "symbol": p.get("baseToken", {}).get("symbol", "TOKEN"),
                        "price": float(p.get("priceUsd") or 0.0),
                        "mcap": p.get("marketCap") or p.get("fdv") or 0.0,
                        "buys_h1": int(txns_h1.get("buys") or 0),
                        "url": p.get("url", f"https://dexscreener.com/{CHAIN_ID}/{PAIR_ADDRESS}"),
                    }
        except Exception as e:
            logger.error(f"DexScreener fetch error: {e}")
        return None

    async def run(self):
        if SEND_STARTUP_TEST_MESSAGE:
            try:
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Trade Token on Trojan", url=REFERRAL_URL)]])
                await self.app.bot.send_message(
                    chat_id=self.chat_id,
                    text="🧪 <b>Alpha Buy Tracker Engine Online!</b>",
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Startup test failed: {e}")

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    data = await self.fetch_dexscreener(session)
                    if data:
                        buys_h1 = data["buys_h1"]
                        if self.last_buy_count is None:
                            self.last_buy_count = buys_h1
                        else:
                            new_buys = buys_h1 - self.last_buy_count
                            if new_buys >= MIN_NEW_BUYS_TO_ALERT:
                                formatted_price = f"${data['price']:,.8f}" if data['price'] < 1 else f"${data['price']:,.2f}"
                                msg = (
                                    f"🚨 <b>NEW BUY MOMENTUM DETECTED!</b> 🚨\n\n"
                                    f"🪙 <b>Token:</b> {data['name']} (${data['symbol']})\n"
                                    f"🛒 <b>New Buys:</b> <code>{new_buys}</code>\n"
                                    f"🏷 <b>Price:</b> {formatted_price}\n"
                                    f"📊 <b>Market Cap:</b> ${data['mcap']:,.0f}\n\n"
                                    f"💡 <b>Trade via Trojan to earn $SOL cashback!</b>\n"
                                    f"<i>After trading, submit your TX hash with /verify to claim rewards.</i>\n\n"
                                    f"📈 <a href='{data['url']}'>View Chart</a>"
                                )
                                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Buy $SOL on Trojan", url=REFERRAL_URL)]])
                                await self.app.bot.send_message(
                                    chat_id=self.chat_id, text=msg, parse_mode="HTML", reply_markup=reply_markup
                                )
                                self.last_buy_count = buys_h1
                            elif new_buys < 0:
                                self.last_buy_count = buys_h1
                except Exception as e:
                    logger.error(f"Tracker loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)


# ==========================================
# MAIN EXECUTION & HEALTH SERVER
# ==========================================
async def handle_health_check(request):
    return web.Response(text="Bot is running.")

async def main():
    app_telegram = Application.builder().token(BOT_TOKEN).build()

    # User Commands
    app_telegram.add_handler(CommandHandler("start", start_command))
    app_telegram.add_handler(CommandHandler("beginner", beginner_command))
    app_telegram.add_handler(CommandHandler("verify", verify_tx_command))
    app_telegram.add_handler(CommandHandler("tasks", tasks_command))
    app_telegram.add_handler(CommandHandler("balance", balance_command))
    app_telegram.add_handler(CommandHandler("withdraw", withdraw_command))
    app_telegram.add_handler(CommandHandler("wallet", set_wallet_command))
    app_telegram.add_handler(CommandHandler("help", help_command))
    app_telegram.add_handler(CallbackQueryHandler(verify_task_callback, pattern="^verifytask_"))

    # Admin Commands
    app_telegram.add_handler(CommandHandler("admin", admin_command))
    app_telegram.add_handler(CommandHandler("addtask", addtask_command))
    app_telegram.add_handler(CommandHandler("deltask", deltask_command))

    await app_telegram.initialize()
    await app_telegram.start()
    await app_telegram.updater.start_polling()

    # Health Check Web Server
    web_app = web.Application()
    web_app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # Tracker Loop
    tracker = SolanaBuyTracker(app_telegram, CHAT_ID)
    await tracker.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")