import asyncio
import logging
import os
import re
import sqlite3
import time
from typing import Optional, Dict, Any, List

import aiohttp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # set this in your env, never hardcode
CHAIN_ID = "solana"
REFERRAL_URL = "https://t.me/solana_trojanbot?start=r-____t0ahgu"
POLL_INTERVAL = 15                 # seconds between watchlist scans
MIN_NEW_BUYS_TO_ALERT = 3          # h1 buy delta needed to fire a momentum alert
MIN_LIQUIDITY_USD = 1000           # ignore pairs with less liquidity than this when resolving a token
DB_FILE = "bot_data.db"

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"
DEXSCREENER_PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair}"

SOLANA_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58, no 0/O/I/l

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("MemeTrackerBot")

# ==========================================
# DATABASE
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            chat_id TEXT,
            token_address TEXT,
            symbol TEXT,
            added_at INTEGER,
            last_buy_h1 INTEGER,
            last_price REAL,
            PRIMARY KEY (chat_id, token_address)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            token_address TEXT,
            symbol TEXT,
            entry_price REAL,
            size_sol REAL,
            opened_at INTEGER,
            closed INTEGER DEFAULT 0,
            exit_price REAL,
            closed_at INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

def db():
    return sqlite3.connect(DB_FILE)

# ==========================================
# DEXSCREENER HELPERS
# ==========================================
async def fetch_json(session: aiohttp.ClientSession, url: str) -> Optional[dict]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        logger.error(f"Fetch error {url}: {e}")
    return None

def pick_best_pair(pairs: List[dict]) -> Optional[dict]:
    """Pick the Solana pair with the highest liquidity from a list."""
    sol_pairs = [p for p in pairs if p.get("chainId") == CHAIN_ID]
    if not sol_pairs:
        return None
    sol_pairs.sort(key=lambda p: (p.get("liquidity") or {}).get("usd", 0) or 0, reverse=True)
    return sol_pairs[0]

async def resolve_pair(session: aiohttp.ClientSession, address: str) -> Optional[dict]:
    """Accepts either a token mint address or a pair address, returns the best matching pair dict."""
    data = await fetch_json(session, DEXSCREENER_TOKEN_URL.format(address=address))
    if data and data.get("pairs"):
        best = pick_best_pair(data["pairs"])
        if best:
            return best

    data = await fetch_json(session, DEXSCREENER_PAIR_URL.format(chain=CHAIN_ID, pair=address))
    if data and data.get("pairs"):
        return data["pairs"][0]

    return None

def extract_stats(p: dict) -> Dict[str, Any]:
    txns_h1 = (p.get("txns") or {}).get("h1", {}) or {}
    txns_h24 = (p.get("txns") or {}).get("h24", {}) or {}
    change = p.get("priceChange") or {}
    return {
        "name": (p.get("baseToken") or {}).get("name", "Unknown"),
        "symbol": (p.get("baseToken") or {}).get("symbol", "TOKEN"),
        "address": (p.get("baseToken") or {}).get("address", ""),
        "price": float(p.get("priceUsd") or 0.0),
        "mcap": p.get("marketCap") or p.get("fdv") or 0.0,
        "liquidity": (p.get("liquidity") or {}).get("usd", 0.0),
        "volume_h24": (p.get("volume") or {}).get("h24", 0.0),
        "buys_h1": int(txns_h1.get("buys") or 0),
        "sells_h1": int(txns_h1.get("sells") or 0),
        "buys_h24": int(txns_h24.get("buys") or 0),
        "sells_h24": int(txns_h24.get("sells") or 0),
        "chg_m5": change.get("m5", 0.0),
        "chg_h1": change.get("h1", 0.0),
        "chg_h6": change.get("h6", 0.0),
        "chg_h24": change.get("h24", 0.0),
        "pair_created_at": p.get("pairCreatedAt"),
        "dex": p.get("dexId", "unknown"),
        "url": p.get("url", ""),
    }

def format_price(price: float) -> str:
    return f"${price:,.8f}" if price < 1 else f"${price:,.4f}"

def format_age(created_ms: Optional[int]) -> str:
    if not created_ms:
        return "unknown"
    seconds = time.time() - created_ms / 1000
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    if days > 0:
        return f"{days}d {hours}h"
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"

def build_insight_message(s: Dict[str, Any]) -> str:
    ratio_h1 = f"{s['buys_h1']}/{s['sells_h1']}"
    ratio_h24 = f"{s['buys_h24']}/{s['sells_h24']}"
    return (
        f"🔎 <b>{s['name']} (${s['symbol']})</b>\n\n"
        f"🏷 <b>Price:</b> {format_price(s['price'])}\n"
        f"📊 <b>Change:</b> 5m {s['chg_m5']}% | 1h {s['chg_h1']}% | 6h {s['chg_h6']}% | 24h {s['chg_h24']}%\n"
        f"💧 <b>Liquidity:</b> ${s['liquidity']:,.0f}\n"
        f"🧢 <b>Market Cap:</b> ${s['mcap']:,.0f}\n"
        f"💵 <b>24h Volume:</b> ${s['volume_h24']:,.0f}\n"
        f"🛒 <b>Buys/Sells (1h):</b> {ratio_h1}\n"
        f"🛒 <b>Buys/Sells (24h):</b> {ratio_h24}\n"
        f"⏳ <b>Pair Age:</b> {format_age(s['pair_created_at'])}\n"
        f"🔀 <b>DEX:</b> {s['dex']}\n\n"
        f"📈 <a href='{s['url']}'>View Chart</a>"
    )

# ==========================================
# COMMAND HANDLERS
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_dm = update.effective_chat.type == "private"
    if is_dm:
        msg = (
            "👋 <b>Meme Coin Tracker</b>\n\n"
            "Paste any Solana token or pair address here and I'll pull instant insights — "
            "price, liquidity, market cap, volume, buy/sell ratio, and pair age.\n\n"
            "<b>Other commands:</b>\n"
            "• /token <code>&lt;address&gt;</code> — manual lookup\n"
            "• /watch <code>&lt;address&gt;</code> — get momentum alerts here\n"
            "• /unwatch <code>&lt;address&gt;</code>\n"
            "• /watchlist — see what you're tracking\n"
            "• /logtrade <code>&lt;address&gt; &lt;entry_price&gt; &lt;size_sol&gt;</code>\n"
            "• /pnl — your open/closed positions"
        )
    else:
        msg = (
            "👋 <b>Meme Coin Tracker is live in this group.</b>\n\n"
            "Use /watch <code>&lt;address&gt;</code> to add a token — I'll post buy-momentum "
            "alerts here automatically. Use /token <code>&lt;address&gt;</code> for an on-demand snapshot."
        )
    await update.message.reply_text(msg, parse_mode="HTML")

async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: <code>/token &lt;address&gt;</code>", parse_mode="HTML")
        return
    await lookup_and_reply(update, context.args[0].strip())

async def lookup_and_reply(update: Update, address: str):
    async with aiohttp.ClientSession() as session:
        pair = await resolve_pair(session, address)
    if not pair:
        await update.message.reply_text("❌ Couldn't find that token/pair on DexScreener.", parse_mode="HTML")
        return
    stats = extract_stats(pair)
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Trade on Trojan", url=REFERRAL_URL)]])
    await update.message.reply_text(build_insight_message(stats), parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=False)

async def dm_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-detect a pasted Solana address in a DM and run a lookup."""
    if update.effective_chat.type != "private":
        return
    text = (update.message.text or "").strip()
    if SOLANA_ADDR_RE.match(text):
        await lookup_and_reply(update, text)

async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: <code>/watch &lt;address&gt;</code>", parse_mode="HTML")
        return
    address = context.args[0].strip()
    chat_id = str(update.effective_chat.id)

    async with aiohttp.ClientSession() as session:
        pair = await resolve_pair(session, address)
    if not pair:
        await update.message.reply_text("❌ Couldn't find that token/pair on DexScreener.", parse_mode="HTML")
        return

    stats = extract_stats(pair)
    conn = db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO watchlist (chat_id, token_address, symbol, added_at, last_buy_h1, last_price) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, stats["address"] or address, stats["symbol"], int(time.time()), stats["buys_h1"], stats["price"]),
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"✅ Now watching <b>${stats['symbol']}</b> in this chat. I'll alert on buy momentum here.",
        parse_mode="HTML",
    )

async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: <code>/unwatch &lt;address&gt;</code>", parse_mode="HTML")
        return
    address = context.args[0].strip()
    chat_id = str(update.effective_chat.id)
    conn = db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist WHERE chat_id = ? AND token_address = ?", (chat_id, address))
    conn.commit()
    conn.close()
    await update.message.reply_text("🗑 Removed from watchlist (if it was there).")

async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = db()
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, token_address FROM watchlist WHERE chat_id = ?", (chat_id,))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📭 Nothing watched in this chat yet. Use /watch <address>.")
        return
    lines = "\n".join(f"• ${sym} — <code>{addr}</code>" for sym, addr in rows)
    await update.message.reply_text(f"👀 <b>Watchlist:</b>\n{lines}", parse_mode="HTML")

async def logtrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text(
            "⚠️ Usage: <code>/logtrade &lt;address&gt; &lt;entry_price&gt; &lt;size_sol&gt;</code>", parse_mode="HTML"
        )
        return
    address, entry_price_s, size_s = context.args[0], context.args[1], context.args[2]
    try:
        entry_price = float(entry_price_s)
        size_sol = float(size_s)
    except ValueError:
        await update.message.reply_text("❌ Entry price and size must be numbers.")
        return

    async with aiohttp.ClientSession() as session:
        pair = await resolve_pair(session, address)
    symbol = extract_stats(pair)["symbol"] if pair else "UNKNOWN"

    conn = db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO trades (user_id, token_address, symbol, entry_price, size_sol, opened_at) VALUES (?, ?, ?, ?, ?, ?)",
        (str(update.effective_user.id), address, symbol, entry_price, size_sol, int(time.time())),
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(f"📝 Logged: {size_sol} SOL into ${symbol} @ {format_price(entry_price)}", parse_mode="HTML")

async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    conn = db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT symbol, token_address, entry_price, size_sol, closed, exit_price FROM trades WHERE user_id = ? ORDER BY opened_at DESC LIMIT 15",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📭 No trades logged yet. Use /logtrade to start tracking.")
        return

    lines = []
    async with aiohttp.ClientSession() as session:
        for symbol, address, entry_price, size_sol, closed, exit_price in rows:
            if closed:
                pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price else 0
                lines.append(f"✅ ${symbol}: {pct:+.1f}% (closed)")
            else:
                pair = await resolve_pair(session, address)
                if pair:
                    current = extract_stats(pair)["price"]
                    pct = ((current - entry_price) / entry_price) * 100 if entry_price else 0
                    lines.append(f"🟢 ${symbol}: {pct:+.1f}% (open, {size_sol} SOL in)")
                else:
                    lines.append(f"🟡 ${symbol}: price unavailable (open)")

    await update.message.reply_text("📈 <b>Your Positions</b>\n\n" + "\n".join(lines), parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "❓ <b>Commands</b>\n\n"
        "• /token <code>&lt;address&gt;</code> — instant snapshot\n"
        "• /watch <code>&lt;address&gt;</code> — track for momentum alerts\n"
        "• /unwatch <code>&lt;address&gt;</code>\n"
        "• /watchlist — list tracked tokens here\n"
        "• /logtrade <code>&lt;address&gt; &lt;entry&gt; &lt;size_sol&gt;</code> — log a trade\n"
        "• /pnl — your positions and live P&L\n\n"
        "<i>In DMs, just paste an address — no command needed.</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

# ==========================================
# MOMENTUM ALERT LOOP (all watched tokens, all chats)
# ==========================================
class WatchlistTracker:
    def __init__(self, application: Application):
        self.app = application

    async def scan_once(self, session: aiohttp.ClientSession):
        conn = db()
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, token_address, symbol, last_buy_h1 FROM watchlist")
        rows = cursor.fetchall()
        conn.close()

        # cache lookups per token address so multiple chats watching the same token = 1 API call
        cache: Dict[str, Optional[dict]] = {}
        for chat_id, token_address, symbol, last_buy_h1 in rows:
            if token_address not in cache:
                cache[token_address] = await resolve_pair(session, token_address)
            pair = cache[token_address]
            if not pair:
                continue
            stats = extract_stats(pair)
            new_buys = stats["buys_h1"] - (last_buy_h1 or 0)

            conn = db()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE watchlist SET last_buy_h1 = ?, last_price = ? WHERE chat_id = ? AND token_address = ?",
                (stats["buys_h1"], stats["price"], chat_id, token_address),
            )
            conn.commit()
            conn.close()

            if last_buy_h1 is not None and new_buys >= MIN_NEW_BUYS_TO_ALERT:
                msg = (
                    f"🚨 <b>BUY MOMENTUM: ${stats['symbol']}</b> 🚨\n\n"
                    f"🛒 <b>New buys (1h window):</b> +{new_buys}\n"
                    f"🏷 <b>Price:</b> {format_price(stats['price'])} ({stats['chg_h1']:+.1f}% 1h)\n"
                    f"🧢 <b>Market Cap:</b> ${stats['mcap']:,.0f}\n"
                    f"💧 <b>Liquidity:</b> ${stats['liquidity']:,.0f}\n\n"
                    f"📈 <a href='{stats['url']}'>View Chart</a>"
                )
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Trade on Trojan", url=REFERRAL_URL)]])
                try:
                    await self.app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML", reply_markup=reply_markup)
                except Exception as e:
                    logger.error(f"Failed to alert chat {chat_id}: {e}")

    async def run(self):
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self.scan_once(session)
                except Exception as e:
                    logger.error(f"Tracker loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)

# ==========================================
# MAIN
# ==========================================
async def handle_health_check(request):
    return web.Response(text="Bot is running.")

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set the BOT_TOKEN environment variable before running.")

    app_telegram = Application.builder().token(BOT_TOKEN).build()

    app_telegram.add_handler(CommandHandler("start", start_command))
    app_telegram.add_handler(CommandHandler("token", token_command))
    app_telegram.add_handler(CommandHandler("watch", watch_command))
    app_telegram.add_handler(CommandHandler("unwatch", unwatch_command))
    app_telegram.add_handler(CommandHandler("watchlist", watchlist_command))
    app_telegram.add_handler(CommandHandler("logtrade", logtrade_command))
    app_telegram.add_handler(CommandHandler("pnl", pnl_command))
    app_telegram.add_handler(CommandHandler("help", help_command))
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dm_text_handler))

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

    tracker = WatchlistTracker(app_telegram)
    await tracker.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot manually stopped.")
