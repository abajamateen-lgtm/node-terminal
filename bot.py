import asyncio
import logging
import os
from typing import Optional, Dict, Any
import aiohttp
from aiohttp import web
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = "8803027756:AAHN1gHf2AmvjKgvJM71y1E-TtGHPO5fqcE"
CHAT_ID = "-5328643185"

# Target Token & Network Settings
CHAIN_ID = "solana"
PAIR_ADDRESS = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"

# Set LOW for testing! (Change back to 100.0 or 250.0 after confirming alerts work)
MIN_BUY_THRESHOLD_USD = 1.0 

# Referral Link
REFERRAL_URL = "https://t.me/solana_trojanbot?start=r-____t0ahgu"

# Polling Interval (in seconds)
POLL_INTERVAL = 10 

# Free Endpoints (No API Keys Required)
DEXSCREENER_URL = f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN_ID}/{PAIR_ADDRESS}"
GECKOTERMINAL_URL = f"https://api.geckoterminal.com/api/v2/networks/{CHAIN_ID}/pools/{PAIR_ADDRESS}"

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("FreeSolanaBuyTracker")

class SolanaBuyTracker:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        
        # State Tracking
        self.last_volume_h24: Optional[float] = None
        self.using_fallback = False

    async def fetch_dexscreener(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        """Primary free provider: DexScreener"""
        try:
            async with session.get(DEXSCREENER_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs")
                    if pairs and len(pairs) > 0:
                        p = pairs[0]
                        return {
                            "name": p.get("baseToken", {}).get("name", "Unknown"),
                            "symbol": p.get("baseToken", {}).get("symbol", "TOKEN"),
                            "price": float(p.get("priceUsd", 0.0)),
                            "mcap": p.get("marketCap") or p.get("fdv") or 0.0,
                            "volume_24h": float(p.get("volume", {}).get("h24", 0.0)),
                            "url": p.get("url", f"https://dexscreener.com/{CHAIN_ID}/{PAIR_ADDRESS}")
                        }
                elif resp.status == 429:
                    logger.warning("DexScreener Rate Limited (429). Switching to fallback...")
        except Exception as e:
            logger.error(f"DexScreener Error: {e}")
        return None

    async def fetch_geckoterminal(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        """Fallback free provider: GeckoTerminal"""
        try:
            async with session.get(GECKOTERMINAL_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    attr = res.get("data", {}).get("attributes", {})
                    if attr:
                        return {
                            "name": attr.get("name", "Unknown Token"),
                            "symbol": attr.get("name", "TOKEN").split(" / ")[0],
                            "price": float(attr.get("base_token_price_usd", 0.0)),
                            "mcap": float(attr.get("fdv_usd", 0.0)),
                            "volume_24h": float(attr.get("volume_usd", {}).get("h24", 0.0)),
                            "url": f"https://www.geckoterminal.com/{CHAIN_ID}/pools/{PAIR_ADDRESS}"
                        }
        except Exception as e:
            logger.error(f"GeckoTerminal Error: {e}")
        return None

    async def fetch_pair_data(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        # Attempt primary first
        data = await self.fetch_dexscreener(session)
        if data:
            self.using_fallback = False
            return data
            
        # Fallback if DexScreener fails
        logger.info("Retrying with GeckoTerminal fallback...")
        data = await self.fetch_geckoterminal(session)
        if data:
            self.using_fallback = True
            return data
            
        return None

    def format_alert_message(self, data: Dict[str, Any], buy_amount_usd: float) -> str:
        price_usd = data["price"]
        formatted_price = f"${price_usd:,.6f}" if price_usd < 1 else f"${price_usd:,.2f}"

        return (
            f"🚨 <b>ALPHA BUY DETECTED!</b> 🚨\n\n"
            f"🪙 <b>Token:</b> {data['name']} (${data['symbol']})\n"
            f"💰 <b>Estimated Buy:</b> <code>${buy_amount_usd:,.2f} USD</code>\n"
            f"🏷 <b>Price:</b> {formatted_price}\n"
            f"📊 <b>Market Cap:</b> ${data['mcap']:,.0f}\n\n"
            f"📈 <a href='{data['url']}'>View Chart</a>"
        )

    async def send_telegram_alert(self, message: str):
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Trade Token", url=REFERRAL_URL)]
        ])
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=False
            )
            logger.info("Telegram notification sent successfully!")
        except TelegramError as e:
            logger.error(f"Telegram Dispatch Error: {e}")

    async def process_data(self, data: Dict[str, Any]):
        current_vol = data["volume_24h"]

        # Baseline Initialization
        if self.last_volume_h24 is None:
            self.last_volume_h24 = current_vol
            logger.info(f"Initialized tracking for {data['symbol']}. Initial 24h Vol: ${current_vol:,.2f}")
            return

        # Volume Increase Calculation
        vol_delta = current_vol - self.last_volume_h24

        if vol_delta >= MIN_BUY_THRESHOLD_USD:
            logger.info(f"Volume Increase Detected: +${vol_delta:,.2f}")
            alert_text = self.format_alert_message(data, vol_delta)
            await self.send_telegram_alert(alert_text)
            self.last_volume_h24 = current_vol
        elif current_vol > self.last_volume_h24:
            # Sync volume up quietly if under threshold
            self.last_volume_h24 = current_vol

    async def run(self):
        logger.info(f"Starting free tracking bot for pair: {PAIR_ADDRESS}")
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    data = await self.fetch_pair_data(session)
                    if data:
                        await self.process_data(data)
                except Exception as e:
                    logger.exception(f"Error in execution loop: {e}")
                
                await asyncio.sleep(POLL_INTERVAL)

# ==========================================
# WEB SERVER FOR RENDER / UPTIMEROBOT
# ==========================================
async def handle_health_check(request):
    return web.Response(text="Bot is running 24/7!")

async def main():
    # Bind Web Server to Render Port
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check web server running on port {port}")

    # Start Bot
    tracker = SolanaBuyTracker(token=BOT_TOKEN, chat_id=CHAT_ID)
    await tracker.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot manually stopped.")
