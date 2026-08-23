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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8803027756:AAHN1gHf2AmvjKgvJM71y1E-TtGHPO5fqcE")
CHAT_ID = os.environ.get("CHAT_ID", "-1004364300853")

CHAIN_ID = "solana"
PAIR_ADDRESS = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"

# Minimum number of NEW buy transactions (since last poll) to trigger an alert.
# This is the real fix: we now count actual buy txns, not 24h volume drift.
MIN_NEW_BUYS_TO_ALERT = 3

# Optional: also require a minimum estimated USD size per alert batch.
# Set to 0 to disable this filter entirely (recommended while testing).
MIN_BUY_THRESHOLD_USD = 0.0

REFERRAL_URL = "https://t.me/solana_trojanbot?start=r-____t0ahgu"

POLL_INTERVAL = 10

# Send one alert to confirm Telegram delivery works, at startup.
SEND_STARTUP_TEST_MESSAGE = True

DEXSCREENER_URL = f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN_ID}/{PAIR_ADDRESS}"
GECKOTERMINAL_URL = f"https://api.geckoterminal.com/api/v2/networks/{CHAIN_ID}/pools/{PAIR_ADDRESS}"

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("SolanaBuyTracker")


class SolanaBuyTracker:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id

        # State tracking — now based on transaction COUNTS, not volume dollars.
        # txns.h1.buys is a live rolling counter DexScreener updates frequently,
        # unlike volume.h24 which can sit flat for long stretches.
        self.last_buy_count: Optional[int] = None
        self.last_price: Optional[float] = None
        self.consecutive_fetch_failures: int = 0

    # ------------------------------------------------------------
    # DATA FETCHING
    # ------------------------------------------------------------
    async def fetch_dexscreener(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        try:
            async with session.get(DEXSCREENER_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs")
                    if not pairs:
                        logger.warning("DexScreener returned 200 but no 'pairs' data. Check PAIR_ADDRESS.")
                        return None
                    p = pairs[0]
                    txns_h1 = p.get("txns", {}).get("h1", {}) or {}
                    return {
                        "name": p.get("baseToken", {}).get("name", "Unknown"),
                        "symbol": p.get("baseToken", {}).get("symbol", "TOKEN"),
                        "price": float(p.get("priceUsd") or 0.0),
                        "mcap": p.get("marketCap") or p.get("fdv") or 0.0,
                        "volume_24h": float(p.get("volume", {}).get("h24") or 0.0),
                        "buys_h1": int(txns_h1.get("buys") or 0),
                        "sells_h1": int(txns_h1.get("sells") or 0),
                        "url": p.get("url", f"https://dexscreener.com/{CHAIN_ID}/{PAIR_ADDRESS}"),
                        "source": "DexScreener",
                    }
                elif resp.status == 429:
                    logger.warning("DexScreener rate limited (429). Falling back to GeckoTerminal.")
                else:
                    logger.warning(f"DexScreener returned unexpected status {resp.status}.")
        except asyncio.TimeoutError:
            logger.error("DexScreener request timed out.")
        except Exception as e:
            logger.error(f"DexScreener fetch error: {e}")
        return None

    async def fetch_geckoterminal(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        """
        Fallback provider. Note: GeckoTerminal's pool endpoint does not expose
        a per-transaction buy count the same way DexScreener does, so when we're
        on this fallback we detect buys via price upticks instead (see process_data).
        """
        try:
            async with session.get(GECKOTERMINAL_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    attr = res.get("data", {}).get("attributes", {})
                    if not attr:
                        logger.warning("GeckoTerminal returned 200 but no attributes. Check PAIR_ADDRESS.")
                        return None
                    return {
                        "name": attr.get("name", "Unknown Token"),
                        "symbol": (attr.get("name", "TOKEN").split(" / ") or ["TOKEN"])[0],
                        "price": float(attr.get("base_token_price_usd") or 0.0),
                        "mcap": float(attr.get("fdv_usd") or 0.0),
                        "volume_24h": float((attr.get("volume_usd") or {}).get("h24") or 0.0),
                        "buys_h1": None,  # not available from this endpoint
                        "sells_h1": None,
                        "url": f"https://www.geckoterminal.com/{CHAIN_ID}/pools/{PAIR_ADDRESS}",
                        "source": "GeckoTerminal",
                    }
                else:
                    logger.warning(f"GeckoTerminal returned unexpected status {resp.status}.")
        except asyncio.TimeoutError:
            logger.error("GeckoTerminal request timed out.")
        except Exception as e:
            logger.error(f"GeckoTerminal fetch error: {e}")
        return None

    async def fetch_pair_data(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        data = await self.fetch_dexscreener(session)
        if data:
            self.consecutive_fetch_failures = 0
            return data

        logger.info("Retrying with GeckoTerminal fallback...")
        data = await self.fetch_geckoterminal(session)
        if data:
            self.consecutive_fetch_failures = 0
            return data

        self.consecutive_fetch_failures += 1
        logger.error(
            f"Both providers failed. Consecutive failures: {self.consecutive_fetch_failures}"
        )
        return None

    # ------------------------------------------------------------
    # MESSAGE FORMATTING
    # ------------------------------------------------------------
    def format_alert_message(self, data: Dict[str, Any], new_buys: int) -> str:
        price_usd = data["price"]
        formatted_price = f"${price_usd:,.8f}" if price_usd < 1 else f"${price_usd:,.2f}"

        return (
            f"🚨 <b>NEW BUY ACTIVITY DETECTED!</b> 🚨\n\n"
            f"🪙 <b>Token:</b> {data['name']} (${data['symbol']})\n"
            f"🛒 <b>New Buys:</b> <code>{new_buys}</code>\n"
            f"🏷 <b>Price:</b> {formatted_price}\n"
            f"📊 <b>Market Cap:</b> ${data['mcap']:,.0f}\n"
            f"📡 <b>Source:</b> {data['source']}\n\n"
            f"📈 <a href='{data['url']}'>View Chart</a>"
        )

    # ------------------------------------------------------------
    # TELEGRAM DELIVERY
    # ------------------------------------------------------------
    async def send_telegram_alert(self, message: str) -> bool:
        """Returns True if the message was sent successfully, False otherwise."""
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
            logger.info("Telegram message sent successfully.")
            return True
        except TelegramError as e:
            # This is the exact failure point that was silent before —
            # now it's impossible to miss in the logs.
            logger.error(f"TELEGRAM SEND FAILED: {e}")
            logger.error(
                "Common causes: bot not added to the chat/channel, bot lacks "
                "post permission (needs admin in channels), wrong CHAT_ID, "
                "or bot was blocked/removed."
            )
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram message: {e}")
            return False

    # ------------------------------------------------------------
    # DETECTION LOGIC (the core fix)
    # ------------------------------------------------------------
    async def process_data(self, data: Dict[str, Any]):
        current_price = data["price"]
        buys_h1 = data["buys_h1"]

        logger.info(
            f"Poll check — source={data['source']} price=${current_price:,.8f} "
            f"buys_h1={buys_h1} last_buy_count={self.last_buy_count}"
        )

        # First poll ever: just set the baseline, nothing to compare against yet.
        if self.last_buy_count is None and buys_h1 is not None:
            self.last_buy_count = buys_h1
            self.last_price = current_price
            logger.info(f"Baseline set. Starting buys_h1 count: {buys_h1}")
            return

        # PRIMARY PATH: DexScreener gives us a real rolling buy-transaction count.
        if buys_h1 is not None and self.last_buy_count is not None:
            new_buys = buys_h1 - self.last_buy_count

            # h1 window can roll over and reset lower — treat a decrease as a
            # fresh baseline rather than a negative "new buys" count.
            if new_buys < 0:
                logger.info("Buy counter rolled over (new h1 window). Resetting baseline.")
                self.last_buy_count = buys_h1
                return

            if new_buys >= MIN_NEW_BUYS_TO_ALERT:
                message = self.format_alert_message(data, new_buys)
                sent = await self.send_telegram_alert(message)
                if sent:
                    self.last_buy_count = buys_h1
                # If send failed, we deliberately do NOT update last_buy_count,
                # so the same buys get retried on next poll instead of being lost.
            else:
                self.last_buy_count = buys_h1
            return

        # FALLBACK PATH: GeckoTerminal has no buy count, so detect via price uptick.
        if self.last_price is not None and current_price > self.last_price:
            pct_change = ((current_price - self.last_price) / self.last_price) * 100
            message = self.format_alert_message(data, new_buys=1)
            message += f"\n\n(⚠️ Fallback mode — detected via +{pct_change:.2f}% price uptick, not exact buy count)"
            sent = await self.send_telegram_alert(message)
            if sent:
                self.last_price = current_price
        else:
            self.last_price = current_price

    # ------------------------------------------------------------
    # MAIN LOOP
    # ------------------------------------------------------------
    async def run(self):
        logger.info(f"Starting buy tracker for pair: {PAIR_ADDRESS}")

        if SEND_STARTUP_TEST_MESSAGE:
            logger.info("Sending startup test message to confirm Telegram delivery works...")
            ok = await self.send_telegram_alert(
                "🧪 <b>Bot connected.</b> If you see this, Telegram delivery works "
                "and any future silence means no qualifying buys were detected yet."
            )
            if not ok:
                logger.error(
                    "STARTUP TEST FAILED TO SEND. Fix Telegram delivery (bot membership/"
                    "permissions/CHAT_ID) before expecting any real alerts to arrive."
                )

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    data = await self.fetch_pair_data(session)
                    if data:
                        await self.process_data(data)
                    else:
                        if self.consecutive_fetch_failures and self.consecutive_fetch_failures % 6 == 0:
                            # Every ~minute of continuous failure at default POLL_INTERVAL, warn loudly.
                            logger.error(
                                f"No data for {self.consecutive_fetch_failures} consecutive polls. "
                                "Check PAIR_ADDRESS, network access, or provider rate limits."
                            )
                except Exception as e:
                    logger.exception(f"Unhandled error in polling loop: {e}")

                await asyncio.sleep(POLL_INTERVAL)


# ==========================================
# WEB SERVER FOR HOSTING PLATFORM HEALTH CHECKS
# ==========================================
async def handle_health_check(request):
    return web.Response(text="Bot is running.")


async def main():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check web server running on port {port}")

    tracker = SolanaBuyTracker(token=BOT_TOKEN, chat_id=CHAT_ID)
    await tracker.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot manually stopped.")
