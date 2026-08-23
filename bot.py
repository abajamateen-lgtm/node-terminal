import asyncio
import logging
import os
from typing import Optional, Dict, Any, List
import aiohttp
from aiohttp import web
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = "8803027756:AAHN1gHf2AmvjKgvJM71y1E-TtGHPO5fqcE"
CHAT_ID = "-5328643185"

CHAIN_ID = "solana"
PAIR_ADDRESS = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
MIN_BUY_THRESHOLD_USD = 1.0
REFERRAL_URL = "https://t.me/solana_trojanbot?start=r-____t0ahgu"
POLL_INTERVAL = 10 

# RPC/API Pool (Primary + Fallbacks)
DEXSCREENER_ENDPOINTS = [
    f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN_ID}/{PAIR_ADDRESS}",
    f"https://api.dexscreener.io/latest/dex/pairs/{CHAIN_ID}/{PAIR_ADDRESS}" # Fallback
]

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("AlphaBuyTrackerBot")

class ResilientBuyTrackerBot:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        self.endpoint_idx = 0
        self.last_seen_volume: Optional[float] = None

    def get_api_url(self) -> str:
        return DEXSCREENER_ENDPOINTS[self.endpoint_idx]

    def rotate_endpoint(self):
        self.endpoint_idx = (self.endpoint_idx + 1) % len(DEXSCREENER_ENDPOINTS)
        logger.warning(f"Switched endpoint to: {self.get_api_url()}")

    async def fetch_pair_data(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        for attempt in range(len(DEXSCREENER_ENDPOINTS)):
            url = self.get_api_url()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            return pairs[0]
                    elif resp.status in (429, 502, 503):
                        logger.warning(f"HTTP {resp.status} on {url}. Rotating...")
                        self.rotate_endpoint()
            except Exception as e:
                logger.error(f"Error connecting to {url}: {e}")
                self.rotate_endpoint()
            await asyncio.sleep(1)
        return None

    def format_alert_message(self, pair: Dict[str, Any], buy_amount_usd: float) -> str:
        base_token = pair.get("baseToken", {})
        token_name = base_token.get("name", "Unknown")
        token_symbol = base_token.get("symbol", "TOKEN")
        price_usd_str = pair.get("priceUsd", "0.00")
        mcap = pair.get("marketCap") or pair.get("fdv") or 0.0
        chart_url = pair.get("url", f"https://dexscreener.com/{CHAIN_ID}/{PAIR_ADDRESS}")

        return (
            f"🚨 <b>ALPHA BUY DETECTED!</b> 🚨\n\n"
            f"🪙 <b>Token:</b> {token_name} (${token_symbol})\n"
            f"💰 <b>Buy Amount:</b> <code>${buy_amount_usd:,.2f} USD</code>\n"
            f"🏷 <b>Current Price:</b> ${float(price_usd_str):,.6f}\n"
            f"📊 <b>Market Cap:</b> ${mcap:,.0f}\n\n"
            f"📈 <a href='{chart_url}'>View Chart on DexScreener</a>"
        )

    async def send_telegram_alert(self, message: str):
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Trade Token", url=REFERRAL_URL)]])
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=False
            )
            logger.info("Buy alert dispatched.")
        except TelegramError as e:
            logger.error(f"Telegram dispatch failed: {e}")

    async def process_pair_updates(self, pair: Dict[str, Any]):
        # Calculate overall volume delta rather than decaying rolling window
        total_vol = float(pair.get("volume", {}).get("h24", 0.0))

        if self.last_seen_volume is None:
            self.last_seen_volume = total_vol
            logger.info(f"Initialized tracking. 24h Vol: ${total_vol:,.2f}")
            return

        vol_delta = total_vol - self.last_seen_volume

        # If 24h volume increased, evaluate volume movement
        if vol_delta >= MIN_BUY_THRESHOLD_USD:
            logger.info(f"Volume surge detected: +${vol_delta:,.2f}")
            alert_text = self.format_alert_message(pair, vol_delta)
            await self.send_telegram_alert(alert_text)
            self.last_seen_volume = total_vol
        elif total_vol > self.last_seen_volume:
            self.last_seen_volume = total_vol

    async def run(self):
        logger.info("Bot execution started...")
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    pair_data = await self.fetch_pair_data(session)
                    if pair_data:
                        await self.process_pair_updates(pair_data)
                except Exception as e:
                    logger.exception(f"Loop exception: {e}")
                await asyncio.sleep(POLL_INTERVAL)

async def handle_health_check(request):
    return web.Response(text="Bot Active")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    tracker = ResilientBuyTrackerBot(token=BOT_TOKEN, chat_id=CHAT_ID)
    await tracker.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Terminated.")
