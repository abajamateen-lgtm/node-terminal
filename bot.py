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
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import transfer, TransferParams
from solders.transaction import VersionedTransaction
from solders.message import MessageV0

# ==========================================
# CONFIGURATION & FINANCIAL RULES
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8803027756:AAHN1gHf2AmvjKgvJM71y1E-TtGHPO5fqcE")
CHAT_ID = os.environ.get("CHAT_ID", "-1004364300853")
MASTER_WALLET_PRIVATE_KEY = os.environ.get("MASTER_WALLET_PRIVATE_KEY")

CHAIN_ID = "solana"
PAIR_ADDRESS = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
MIN_NEW_BUYS_TO_ALERT = 3
REFERRAL_URL = "https://t.me/solana_trojanbot?start=r-____t0ahgu"
POLL_INTERVAL = 10
SEND_STARTUP_TEST_MESSAGE = True

DEXSCREENER_URL = f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN_ID}/{PAIR_ADDRESS}"
DB_FILE = "bot_data.db"
ADMIN_TELEGRAM_ID = "7983373518"

# Financial Controls
MIN_TRADE_SOL = 0.1             # Trades below 0.1 SOL earn 0 cashback
USER_CASHBACK_PERCENT = 0.00125 # 0.125% of trade volume
MIN_WITHDRAW_SOL = 0.05         # Minimum payout threshold
NETWORK_FEE_SOL = 0.005         # Network gas fee offset

RPC_URL = "https://api.mainnet-beta.solana.com"

# ==========================================
# LOGGING & DATABASE SETUP
# ==========================================
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("SolanaBuyTracker")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            solana_wallet TEXT,
            balance_sol REAL DEFAULT 0.0,
            referrer_id TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            channel_username TEXT,
            reward_sol REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            user_id TEXT,
            task_id INTEGER,
            PRIMARY KEY (user_id, task_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_txs (
            tx_hash TEXT PRIMARY KEY
        )
    """)
    
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
# ON-CHAIN PAYOUT ENGINE
# ==========================================
def load_master_keypair(key_str: str) -> Keypair:
    if key_str.startswith("["):
        import json
        return Keypair.from_bytes(bytes(json.loads(key_str)))
    return Keypair.from_base58_string(key_str)

async def execute_sol_payout(user_wallet_address: str, payout_sol_amount: float) -> str:
    if not MASTER_WALLET_PRIVATE_KEY:
        raise ValueError("MASTER_WALLET_PRIVATE_KEY environment variable is not set.")

    master_keypair = load_master_keypair(MASTER_WALLET_PRIVATE_KEY)
    recipient_pubkey = Pubkey.from_string(user_wallet_address)
    lamports_to_send = int(payout_sol_amount * 1_000_000_000)

    async with AsyncClient(RPC_URL) as client:
        blockhash_resp = await client.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        transfer_ix = transfer(
            TransferParams(
                from_pubkey=master_keypair.pubkey(),
                to_pubkey=recipient_pubkey,
                lamports=lamports_to_send
            )
        )

        message = MessageV0.try_compile(
            payer=master_keypair.pubkey(),
            instructions=[transfer_ix],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash
        )

        tx = VersionedTransaction(message, [master_keypair])
        res = await client.send_transaction(tx)
        return str(res.value)

# ==========================================
# USER COMMAND HANDLERS
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    referrer_id = context.args[0] if context.args else None

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (user_id,))
    if not cursor.fetchone():
        valid_ref = referrer_id if referrer_id and referrer_id != user_id else None
        cursor.execute("INSERT INTO users (telegram_id, referrer_id) VALUES (?, ?)", (user_id, valid_ref))
        conn.commit()
    conn.close()

    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"

    msg = (
        f"👋 <b>Welcome {user.first_name}!</b>\n\n"
        "💰 <b>Earn $SOL Cashback:</b> Trade Solana tokens via Trojan & claim rewards!\n"
        "📋 <b>Web3 Tasks:</b> Complete quick channel tasks for bonus earnings.\n"
        "📊 <b>Buy Alerts:</b> Real-time whale and momentum signals in chat.\n\n"
        f"🔗 <b>Your Invite Link:</b>\n<code>{ref_link}</code>\n\n"
        "<i>Earn 10% bonus whenever your referrals complete tasks!</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def beginner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 <b>New to Solana Trading?</b>\n\n"
        "1️⃣ <b>Get Your Trojan Wallet:</b>\n"
        f"   • Tap <a href='{REFERRAL_URL}'>Launch Trojan Bot</a> to open your wallet.\n\n"
        "2️⃣ <b>Deposit Solana:</b>\n"
        "   • Send SOL (e.g., 0.2 SOL) to your Trojan wallet address.\n\n"
        "3️⃣ <b>Trade & Claim Cashback:</b>\n"
        "   • Tap <b>⚡ Buy on Trojan</b> on buy alerts to trade.\n"
        "   • Send <code>/verify YOUR_TX_HASH</code> here to claim your cashback!"
    )
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Open Trojan Wallet", url=REFERRAL_URL)]])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)

async def verify_tx_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("⚠️ <b>Format:</b> <code>/verify YOUR_SOLANA_TX_HASH</code>", parse_mode="HTML")
        return

    tx_hash = context.args[0].strip()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM processed_txs WHERE tx_hash = ?", (tx_hash,))
    if cursor.fetchone():
        await update.message.reply_text("❌ Transaction hash already claimed!", parse_mode="HTML")
        conn.close()
        return

    try:
        async with AsyncClient(RPC_URL) as client:
            sig = Signature.from_string(tx_hash)
            resp = await client.get_transaction(sig, encoding="jsonParsed", max_supported_transaction_version=0)

            if not resp.value or resp.value.transaction.meta.err is not None:
                await update.message.reply_text("❌ Transaction not found or failed on-chain.", parse_mode="HTML")
                conn.close()
                return

            tx_meta = resp.value.transaction.meta
            sol_spent = (tx_meta.pre_balances[0] - tx_meta.post_balances[0]) / 1_000_000_000

            if sol_spent < MIN_TRADE_SOL:
                await update.message.reply_text(
                    f"❌ Minimum trade for cashback is <b>{MIN_TRADE_SOL} SOL</b>.\nYour trade: <code>{sol_spent:.3f} SOL</code>.",
                    parse_mode="HTML"
                )
                conn.close()
                return

            cashback = sol_spent * USER_CASHBACK_PERCENT
            cursor.execute("INSERT INTO processed_txs (tx_hash) VALUES (?)", (tx_hash,))
            cursor.execute("UPDATE users SET balance_sol = balance_sol + ? WHERE telegram_id = ?", (cashback, user_id))
            conn.commit()

            await update.message.reply_text(
                f"🎉 <b>Trade Verified!</b>\n\n🛒 Trade Size: <code>{sol_spent:.3f} SOL</code>\n💰 Cashback: <code>+{cashback:.5f} SOL</code>",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Verification error: {e}")
        await update.message.reply_text("⚠️ Invalid transaction hash format or network timeout.", parse_mode="HTML")

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
        f"<i>Min Withdrawal: {MIN_WITHDRAW_SOL} SOL | Gas Fee Deduction: {NETWORK_FEE_SOL} SOL</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance_sol, solana_wallet FROM users WHERE telegram_id = ?", (user_id,))
    row = cursor.fetchone()

    balance = row[0] if row else 0.0
    wallet = row[1] if row and row[1] else None

    if not wallet:
        await update.message.reply_text("⚠️ Please set a payout wallet first using <code>/wallet YOUR_ADDRESS</code>", parse_mode="HTML")
        conn.close()
        return

    if balance < MIN_WITHDRAW_SOL:
        await update.message.reply_text(f"❌ Minimum cashout requirement is <b>{MIN_WITHDRAW_SOL} SOL</b>. Current balance: <code>{balance:.5f} SOL</code>.", parse_mode="HTML")
        conn.close()
        return

    net_payout = balance - NETWORK_FEE_SOL

    try:
        tx_sig = await execute_sol_payout(wallet, net_payout)
        cursor.execute("UPDATE users SET balance_sol = 0.0 WHERE telegram_id = ?", (user_id,))
        conn.commit()

        msg = (
            "💸 <b>Payout Sent Successfully!</b>\n\n"
            f"💳 <b>Recipient:</b> <code>{wallet}</code>\n"
            f"💵 <b>Amount Transferred:</b> <code>{net_payout:.5f} SOL</code>\n"
            f"⛽ <b>Gas Fee Offset:</b> <code>{NETWORK_FEE_SOL} SOL</code>\n\n"
            f"🔗 <a href='https://solscan.io/tx/{tx_sig}'>View Solscan Transaction</a>"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Payout failed: {e}")
        await update.message.reply_text("⚠️ Payout failed. Ensure master server wallet has sufficient SOL for transfers.", parse_mode="HTML")

    conn.close()

async def set_wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("⚠️ Provide a wallet address: <code>/wallet YOUR_SOLANA_WALLET</code>", parse_mode="HTML")
        return

    wallet = context.args[0].strip()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET solana_wallet = ? WHERE telegram_id = ?", (wallet, user_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Solana payout wallet saved:\n<code>{wallet}</code>", parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "❓ <b>Bot Command Directory</b>\n\n"
        "• /start - Launch bot & grab invite link\n"
        "• /beginner - Plain-English guide on trading & cashback\n"
        "• /verify - Submit transaction hash for cashback\n"
        "• /tasks - Complete Web3 tasks for SOL rewards\n"
        "• /balance - Check balance & referral stats\n"
        "• /wallet - Set payout wallet\n"
        "• /withdraw - Cashout SOL to wallet"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

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
            logger.error(f"DexScreener error: {e}")
        return None

    async def run(self):
        if SEND_STARTUP_TEST_MESSAGE:
            try:
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Trade Token on Trojan", url=REFERRAL_URL)]])
                await self.app.bot.send_message(
                    chat_id=self.chat_id,
                    text="🧪 <b>Buy Tracker Engine Connected & Online!</b>",
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
# MAIN EXECUTION
# ==========================================
async def handle_health_check(request):
    return web.Response(text="Bot is running.")

async def main():
    app_telegram = Application.builder().token(BOT_TOKEN).build()

    app_telegram.add_handler(CommandHandler("start", start_command))
    app_telegram.add_handler(CommandHandler("beginner", beginner_command))
    app_telegram.add_handler(CommandHandler("verify", verify_tx_command))
    app_telegram.add_handler(CommandHandler("balance", balance_command))
    app_telegram.add_handler(CommandHandler("withdraw", withdraw_command))
    app_telegram.add_handler(CommandHandler("wallet", set_wallet_command))
    app_telegram.add_handler(CommandHandler("help", help_command))

    await app_telegram.initialize()
    await app_telegram.start()
    await app_telegram.updater.start_polling()

    web_app = web.Application()
    web_app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    tracker = SolanaBuyTracker(app_telegram, CHAT_ID)
    await tracker.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot manually stopped.")
