import asyncio
import logging
from typing import Optional, Dict, Any
import aiohttp
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = "8803027756:AAHN1gHf2AmvjKgvJM71y1E-TtGHPO5fqcE"
CHAT_ID = "-5328643185"

# Target Token/Pair Settings
CHAIN_ID = "solana"
PAIR_ADDRESS = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"

# Buy Alert Threshold (in USD)
MIN_BUY_THRESHOLD_USD = 10.0

# Trojan Affiliate Referral Link
REFERRAL_URL = "https://t.me/solana_trojanbot?start=r-____t0ahgu"

# Polling Interval & API Config
POLL_INTERVAL = 5  # seconds
DEXSCREENER_API_URL = f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN_ID}/{PAIR_ADDRESS}"

# Rate-limit Backoff Settings
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0  # seconds

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("AlphaBuyTrackerBot")


class BuyTrackerBot:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        
        # Internal state tracking
        self.last_buy_count: Optional[int] = None
        self.last_volume_m5: Optional[float] = None

    async def fetch_pair_data(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        """
        Fetches pair stats from DexScreener API with exponential backoff for rate-limits.
        """
        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(DEXSCREENER_API_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs")
                        if pairs and len(pairs) > 0:
                            return pairs[0]
                        else:
                            logger.warning(f"No pair found on chain '{CHAIN_ID}' for address '{PAIR_ADDRESS}'")
                            return None
                    elif resp.status == 429:
                        logger.warning(f"Rate limited (429). Retrying in {backoff}s... (Attempt {attempt}/{MAX_RETRIES})")
                        await asyncio.sleep(backoff)
                        backoff *= 2
                    else:
                        logger.error(f"DexScreener API returned HTTP {resp.status}")
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"Network error on attempt {attempt}: {e}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                else:
                    logger.error("Max retries reached while querying DexScreener.")
                    return None
        return None

    def format_alert_message(self, pair: Dict[str, Any], buy_amount_usd: float) -> str:
        """
        Formats HTML Telegram alert.
        """
        base_token = pair.get("baseToken", {})
        token_name = base_token.get("name", "Unknown Token")
        token_symbol = base_token.get("symbol", "TOKEN")
        
        price_usd_str = pair.get("priceUsd", "0.00")
        try:
            price_usd = float(price_usd_str)
            formatted_price = f"${price_usd:,.6f}" if price_usd < 1 else f"${price_usd:,.2f}"
        except ValueError:
            formatted_price = f"${price_usd_str}"

        mcap = pair.get("marketCap") or pair.get("fdv") or 0.0
        chart_url = pair.get("url", f"https://dexscreener.com/{CHAIN_ID}/{PAIR_ADDRESS}")

        message = (
            f"🚨 <b>ALPHA BUY DETECTED!</b> 🚨\n\n"
            f"🪙 <b>Token:</b> {token_name} (${token_symbol})\n"
            f"💰 <b>Buy Amount:</b> <code>${buy_amount_usd:,.2f} USD</code>\n"
            f"🏷 <b>Current Price:</b> {formatted_price}\n"
            f"📊 <b>Market Cap:</b> ${mcap:,.0f}\n\n"
            f"📈 <a href='{chart_url}'>View Chart on DexScreener</a>"
        )
        return message

    async def send_telegram_alert(self, message: str):
        """
        Sends formatted alert with inline trade button to targeted Chat ID.
        """
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
            logger.info("Buy alert successfully dispatched to Telegram.")
        except TelegramError as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    async def process_pair_updates(self, pair: Dict[str, Any]):
        """
        Tracks short-term transaction and volume deltas to isolate individual buys.
        """
        txns = pair.get("txns", {})
        m5_txns = txns.get("m5", {})
        current_buys = m5_txns.get("buys", 0)

        volume = pair.get("volume", {})
        current_vol_m5 = float(volume.get("m5", 0.0))

        # First run: set baseline
        if self.last_buy_count is None or self.last_volume_m5 is None:
            self.last_buy_count = current_buys
            self.last_volume_m5 = current_vol_m5
            logger.info(f"Tracking initialized. 5m Buys: {current_buys}, 5m Vol: ${current_vol_m5:,.2f}")
            return

        # Rollover check (when DexScreener rolling 5m window updates)
        if current_buys < self.last_buy_count or current_vol_m5 < self.last_volume_m5:
            self.last_buy_count = current_buys
            self.last_volume_m5 = current_vol_m5
            return

        # Calculate transaction & volume deltas
        buy_delta = current_buys - self.last_buy_count
        vol_delta = current_vol_m5 - self.last_volume_m5

        if buy_delta > 0 and vol_delta > 0:
            estimated_buy_usd = vol_delta / buy_delta
            logger.info(f"New Buy Delta: {buy_delta} | Est. Vol: ${vol_delta:,.2f} | Avg Buy: ${estimated_buy_usd:,.2f}")

            if estimated_buy_usd >= MIN_BUY_THRESHOLD_USD:
                alert_text = self.format_alert_message(pair, estimated_buy_usd)
                await self.send_telegram_alert(alert_text)

            self.last_buy_count = current_buys
            self.last_volume_m5 = current_vol_m5

    async def run(self):
        """
        Main continuous polling loop.
        """
        logger.info(f"Bot starting up for chain: '{CHAIN_ID}' | Pair: '{PAIR_ADDRESS}'...")
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    pair_data = await self.fetch_pair_data(session)
                    if pair_data:
                        await self.process_pair_updates(pair_data)
                except Exception as e:
                    logger.exception(f"Unexpected error in polling cycle: {e}")
                
                await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    tracker = BuyTrackerBot(token=BOT_TOKEN, chat_id=CHAT_ID)
    try:
        asyncio.run(tracker.run())
    except KeyboardInterrupt:
        logger.info("Bot manually terminated.")
