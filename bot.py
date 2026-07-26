"""
Telegram Bot - Smart Offer Completion Pipeline
Vmtrk → AdPropel → Adsphire → Panel with auto-retry + completion check
Made by Slayer

Hosted on Render as a Web Service (free tier) using webhook mode.
Locally uses polling mode.
"""

import asyncio
import os
import re
import html as html_mod
import requests
import urllib3
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Disable SSL warnings (same behavior as CURLOPT_SSL_VERIFYPEER=false)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ======================================================================
# CONFIGURATION
# ======================================================================
BOT_TOKEN = "8650439495:AAE9-LmwE7gzWsJG5rneavUFiFRoirMlCZM"
PANEL_URL = "https://mr4u.iceiy.com/?id=152535&i=1"
MAX_RETRIES = 30
RETRY_DELAY = 3  # seconds between retries

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 16; V2437 Build/BP2A.250605.031.A3_V000L1) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Utgmqff/4.0 Chrome/149.0.7827.159 Mobile Safari/537.36"
)

SPOOF_IP = "103.156.19.178"  # Change this to your mobile proxy IP if needed

COMPLETION_KEYWORDS = [
    "already completed this offer",
    "you have already completed",
    "offer already completed",
    "you've already completed",
    "already completed",
    "already claimed this",
    "offer completed",
    "already claimed",
    "completed this offer",
    "already finished this",
    "you already completed",
    "this offer is completed",
    "already done",
]

# ======================================================================
# URL PARSER
# ======================================================================
def parse_two_links(text: str) -> dict | None:
    """Split two concatenated URLs (no space between them)."""
    text = text.strip()
    # Split before each http:// or https://
    parts = re.split(r"(?=https?://)", text)
    urls = [p.strip() for p in parts if re.match(r"^https?://", p.strip())]
    if len(urls) >= 2:
        return {"vmtrk_link": urls[0], "check_link": urls[1]}
    return None


# ======================================================================
# PIPELINE STAGES
# ======================================================================
def run_pipeline(vmtrk_link: str) -> dict:
    """
    Execute 3-stage redirect pipeline.
    Returns: {"ok": True, "link": final_url}
             or {"ok": False, "stage": N, "http": code, "msg": reason}
    """
    session = requests.Session()

    # --- STAGE 1: vmtrk.com → AdPropel redirect ---
    headers1 = {
        "User-Agent": USER_AGENT,
        "sec-ch-ua": '"Android WebView";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "upgrade-insecure-requests": "1",
        "x-requested-with": "com.mycompany.app.soulbrowser",
        "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "navigate",
        "sec-fetch-dest": "document",
        "referer": "https://rewardtk.com/",
        "accept-language": "en-US,en;q=0.9",
        "X-Forwarded-For": SPOOF_IP,
        "X-Real-IP": SPOOF_IP,
        "Client-IP": SPOOF_IP,
    }

    try:
        resp1 = session.get(vmtrk_link, headers=headers1, allow_redirects=False,
                            timeout=15, verify=False)
    except requests.RequestException as e:
        return {"ok": False, "stage": 1, "http": 0, "msg": str(e)[:80]}

    ad_propel_url = resp1.headers.get("Location", "")

    # Fallback: regex on response body
    if not ad_propel_url:
        body1 = resp1.text
        m = re.search(r'location:\s*([^\s\r\n]+)', body1, re.IGNORECASE)
        if m:
            ad_propel_url = m.group(1).strip()
        else:
            m = re.search(r'<a\s+href=["\']([^"\']+)["\']', body1, re.IGNORECASE)
            if m:
                ad_propel_url = m.group(1).strip()

    ad_propel_url = html_mod.unescape(ad_propel_url)

    if not ad_propel_url or not ad_propel_url.startswith("http"):
        raw = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', resp1.text)).strip()[:80]
        return {"ok": False, "stage": 1, "http": resp1.status_code, "msg": raw or "No redirect"}

    # --- STAGE 2: AdPropel → Adsphire redirect ---
    headers2 = {
        "User-Agent": USER_AGENT,
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "X-Requested-With": "com.mycompany.app.soulbrowser",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Forwarded-For": SPOOF_IP,
        "X-Real-IP": SPOOF_IP,
    }

    try:
        resp2 = session.get(ad_propel_url, headers=headers2, allow_redirects=False,
                            timeout=12, verify=False)
    except requests.RequestException as e:
        return {"ok": False, "stage": 2, "http": 0, "msg": str(e)[:80]}

    adsphire_url = resp2.headers.get("Location", "")

    if not adsphire_url:
        body2 = resp2.text
        m = re.search(r'location:\s*([^\s\r\n]+)', body2, re.IGNORECASE)
        if m:
            adsphire_url = m.group(1).strip()
        else:
            m = re.search(r'<a\s+href=["\']([^"\']+)["\']', body2, re.IGNORECASE)
            if m:
                adsphire_url = m.group(1).strip()

    adsphire_url = html_mod.unescape(adsphire_url)

    if not adsphire_url or not adsphire_url.startswith("http"):
        raw = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', resp2.text)).strip()[:80]
        return {"ok": False, "stage": 2, "http": resp2.status_code, "msg": raw or "No redirect"}

    # --- STAGE 3: POST final Adsphire link to Panel ---
    headers3 = {
        "Host": "mr4u.iceiy.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
        "Cookie": "__test=0e3273fa003e3c50fee09c3b59d1cc77",
        "Origin": "https://mr4u.iceiy.com",
        "Referer": "https://mr4u.iceiy.com/?id=152535&i=1",
        "X-Requested-With": "com.mycompany.app.soulbrowser",
    }

    post_data = {"link": adsphire_url, "submit": "SUBMIT"}

    try:
        resp3 = session.post(PANEL_URL, data=post_data, headers=headers3,
                             timeout=12, verify=False)
    except requests.RequestException as e:
        return {"ok": False, "stage": 3, "http": 0, "msg": str(e)[:80]}

    if resp3.status_code in (200, 302):
        return {"ok": True, "link": adsphire_url}
    else:
        return {"ok": False, "stage": 3, "http": resp3.status_code,
                "msg": f"Panel POST failed (HTTP {resp3.status_code})"}


# ======================================================================
# COMPLETION CHECKER
# ======================================================================
def check_offer_completed(check_link: str) -> bool | None:
    """
    Fetch the check link and look for completion keywords.
    Returns: True = completed, False = not completed, None = error
    """
    headers = {
        "User-Agent": USER_AGENT,
    }
    try:
        resp = requests.get(check_link, headers=headers, timeout=15,
                            allow_redirects=True, verify=False)
    except requests.RequestException:
        return None

    if resp.status_code >= 500:
        return None

    # Strip HTML, normalize whitespace
    text = re.sub(r'<[^>]+>', '', resp.text)
    text = html_mod.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip().lower()

    for keyword in COMPLETION_KEYWORDS:
        if keyword.lower() in text:
            return True

    return False


# ======================================================================
# BOT HANDLERS
# ======================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    help_text = (
        "🚀 <b>Smart Offer Completion Bot</b>\n\n"
        "Send me <b>TWO links</b> in <b>ONE message</b> (no space between them):\n"
        "1️⃣ Vmtrk tracking link\n"
        "2️⃣ Check link (to verify completion)\n\n"
        "<b>Example:</b>\n"
        "<code>https://www.vmtrk.com/click?...https://offers.com/status?...</code>\n\n"
        "<b>How it works:</b>\n"
        "• Runs the pipeline once\n"
        "• Checks if offer completed\n"
        "• Retries until done (max 30 attempts)\n\n"
        "Made by Slayer"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages with two links."""
    text = update.message.text
    chat_id = update.effective_chat.id

    links = parse_two_links(text)
    if not links:
        await update.message.reply_text(
            "❌ Please send <b>TWO links</b> in ONE message (no space).\n\n"
            "First: Vmtrk link\nSecond: Check link\n\nType /start for help.",
            parse_mode="HTML"
        )
        return

    vmtrk_link = links["vmtrk_link"]
    check_link = links["check_link"]

    # Send initial status
    status_msg = await update.message.reply_text(
        f"Starting offer completion...\n\n"
        f"Vmtrk: {vmtrk_link[:60]}...\n"
        f"Check: {check_link[:60]}..."
    )

    # --- RETRY LOOP ---
    for attempt in range(1, MAX_RETRIES + 1):
        # Update progress every 5 attempts
        if attempt == 1 or attempt % 5 == 0 or attempt == MAX_RETRIES:
            try:
                await status_msg.edit_text(
                    f"Attempt {attempt}/{MAX_RETRIES}..."
                )
            except Exception:
                pass

        print(f"Chat {chat_id} | Attempt {attempt}")

        # Step 1: Run pipeline
        result = await asyncio.to_thread(run_pipeline, vmtrk_link)

        if not result["ok"]:
            print(f"Chat {chat_id} | Attempt {attempt} FAILED at Stage {result['stage']} "
                  f"(HTTP {result['http']})")
            await asyncio.sleep(RETRY_DELAY)
            continue

        # Step 2: Check if offer completed
        completed = await asyncio.to_thread(check_offer_completed, check_link)

        if completed is True:
            # SUCCESS!
            await status_msg.edit_text(
                f"OFFER COMPLETED!\n\n"
                f"Completed on attempt: {attempt}/{MAX_RETRIES}\n"
                f"Final URL: {result['link'][:80]}..."
            )
            print(f"Chat {chat_id} | COMPLETED on attempt {attempt}")
            return

        if completed is None:
            print(f"Chat {chat_id} | Attempt {attempt} | Check link unreachable, retrying...")

        # Not completed → wait and retry
        await asyncio.sleep(RETRY_DELAY)

    # Exhausted all retries
    await status_msg.edit_text(
        f"Max retries ({MAX_RETRIES}) reached.\n"
        f"Offer did NOT complete. Try again later or check your links."
    )
    print(f"Chat {chat_id} | FAILED after {MAX_RETRIES} attempts")


# ======================================================================
# MAIN
# ======================================================================
def main() -> None:
    """Start the bot. Auto-detects webhook (Render) vs polling (local)."""
    print("Bot starting...")

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is empty!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Detect environment: PORT = Render Web Service → webhook mode, else polling
    port = os.environ.get("PORT")

    if port:
        # --- RENDER WEB SERVICE (FREE) - Webhook Mode ---
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
        if not render_url:
            # Fallback if RENDER_EXTERNAL_URL not available
            service_name = os.environ.get("RENDER_SERVICE_NAME", "offer-bot")
            render_url = f"https://{service_name}.onrender.com"

        webhook_url = f"{render_url}/telegram"
        print(f"Webhook mode active: {webhook_url}")
        print(f"Listening on port {port}...")

        app.run_webhook(
            listen="0.0.0.0",
            port=int(port),
            url_path="telegram",
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )
    else:
        # --- LOCAL TESTING - Polling Mode ---
        print("Polling mode active (local testing). Press Ctrl+C to stop.")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
