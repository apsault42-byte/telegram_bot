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
from urllib.parse import urlsplit
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

# A completion notice is a short plain message ("You have already completed
# this offer."). Anything at or above this size is treated as a full page,
# i.e. the offer flow was served and the offer is NOT done yet.
FULL_PAGE_MIN_BYTES = 1500

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
def is_http_url(value: str) -> bool:
    """Return True only for absolute HTTP(S) URLs with a hostname."""
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except ValueError:
        return False


VMTRK_HOSTS = ("vmtrk.com", "www.vmtrk.com")


def parse_two_links(text: str) -> dict | None:
    """Extract the vmtrk pipeline URL and the completion-check URL.

    Two accepted input forms:

    1. Separated (preferred, unambiguous)::

           <vmtrk url> <whitespace/newline> <check url>

    2. Single vmtrk URL whose last query parameter is a nested URL::

           https://www.vmtrk.com/click?...&devid=https://check.example/path?u=1

    Concatenating two URLs with no separator is NOT supported: when the vmtrk
    link already contains a nested ``https://`` there is no reliable way to
    tell where the first URL ends, so the old parser silently truncated it.
    """
    text = text.strip()

    # --- Form 1: whitespace separated ---
    parts = [p for p in re.split(r"\s+", text) if p]
    if len(parts) == 2:
        vmtrk_link, check_link = parts
        if (
            is_http_url(vmtrk_link)
            and is_http_url(check_link)
            and urlsplit(vmtrk_link).hostname in VMTRK_HOSTS
        ):
            print(f"Parsed (separated):\n  Vmtrk: {vmtrk_link}\n  Check: {check_link}")
            return {"vmtrk_link": vmtrk_link, "check_link": check_link}
        return None

    if len(parts) != 1:
        return None

    # --- Form 2: single vmtrk URL, nested URL as the trailing parameter ---
    vmtrk_link = parts[0]
    if not is_http_url(vmtrk_link):
        return None
    if urlsplit(vmtrk_link).hostname not in VMTRK_HOSTS:
        return None

    devid_match = re.search(r"[?&]devid=(https?://\S+)$", vmtrk_link, re.IGNORECASE)
    if not devid_match:
        return None

    check_link = devid_match.group(1)
    if not is_http_url(check_link):
        return None

    # The nested URL is not percent-encoded, so its boundary is only knowable
    # when nothing ambiguous follows it. Reject rather than guess:
    #   "...?u=5https://offers.com/s" -> two URLs glued together
    #   "...&sub1=abc"                -> outer params captured into the check URL
    if "://" in check_link[len("https://"):] or "&" in check_link:
        print("Ambiguous nested devid URL - ask for space-separated input")
        return None

    print(f"Parsed (nested devid):\n  Vmtrk: {vmtrk_link}\n  Check: {check_link}")
    return {"vmtrk_link": vmtrk_link, "check_link": check_link}


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
def check_offer_completed(check_link: str) -> dict:
    """Fetch the check URL and classify its response by SHAPE, not rendering.

    The site answers in one of two distinguishable forms, both fully visible
    in the raw bytes -- no JavaScript execution is required:

      * Offer NOT completed -> a full page: real HTML, a JS/meta redirect into
        the offer flow, or simply a large body.
      * Offer COMPLETED     -> a short plain message, e.g.
        "You have already completed this offer."

    Status values:
        completed: short message matching a completion phrase
        pending:   full HTML page / redirect shell -> offer not done yet
        rejected:  short message that is an explicit site error
        short:     short message we do not recognise -> surfaced, not looped
        error:     network failure or HTTP 5xx
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://rewardtk.com/",
    }
    try:
        resp = requests.get(check_link, headers=headers, timeout=15,
                            allow_redirects=True, verify=False)
    except requests.RequestException as e:
        return {
            "status": "error",
            "http": 0,
            "raw_len": 0,
            "final_url": check_link,
            "text": f"Fetch error: {str(e)[:200]}",
        }

    raw = resp.text or ""

    # Visible text: strip tags, drop script/style bodies, normalise whitespace.
    stripped = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', raw)
    text = re.sub(r'<[^>]+>', ' ', stripped)
    text = html_mod.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()

    result = {
        "http": resp.status_code,
        "raw_len": len(raw),
        "final_url": resp.url,
        "text": text[:500] if text else "(empty response)",
    }

    if resp.status_code >= 500:
        return {**result, "status": "error"}

    # 1. An explicit completion phrase is decisive, whatever the status code.
    #    (Checked BEFORE the HTTP branch: some panels serve the "already
    #    completed" notice with a 4xx status.)
    text_lower = text.lower()
    for keyword in COMPLETION_KEYWORDS:
        if keyword.lower() in text_lower:
            return {**result, "status": "completed", "keyword": keyword}

    # 2. A full page means the offer flow was served -> not completed yet.
    #    This covers the JS-redirect shell too: a redirect page is the site
    #    sending us INTO the offer, which is itself the "not done" signal.
    if _looks_like_full_page(raw):
        return {**result, "status": "pending"}

    if 400 <= resp.status_code < 500:
        return {**result, "status": "rejected"}

    # 3. Short plain message that is an explicit error, e.g. "Error 3835".
    if re.search(r"\berror\s*(?:code\s*)?\d+\b", text, re.IGNORECASE):
        return {**result, "status": "rejected"}

    # 4. Short plain message we do not recognise. Surface it immediately so a
    #    new wording can be added to COMPLETION_KEYWORDS, instead of silently
    #    retrying 30 times against a response that will never change.
    return {**result, "status": "short"}


def _looks_like_full_page(raw: str) -> bool:
    """True when the body is a rendered page rather than a short notice."""
    if len(raw) >= FULL_PAGE_MIN_BYTES:
        return True
    lowered = raw.lower()
    page_markers = (
        "<html", "<body", "<head", "<!doctype",
        "<script", "<meta http-equiv=\"refresh\"", "<meta http-equiv='refresh'",
        "window.location", "location.href", "location.replace",
    )
    return any(marker in lowered for marker in page_markers)


# ======================================================================
# BOT HANDLERS
# ======================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    help_text = (
        "🚀 <b>Smart Offer Completion Bot</b>\n\n"
        "Send me <b>TWO links separated by a space or newline</b>:\n"
        "1️⃣ Vmtrk tracking link\n"
        "2️⃣ Check link (to verify completion)\n\n"
        "<b>Example:</b>\n"
        "<code>https://www.vmtrk.com/click?... https://offers.com/status?...</code>\n\n"
        "If your vmtrk link already ends with <code>&amp;devid=https://...</code>, "
        "you can send just that one link.\n\n"
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
            "❌ Send the <b>Vmtrk link</b> and the <b>check link</b> "
            "separated by a space or newline.\n\n"
            "Do not paste them stuck together — the vmtrk link contains its own "
            "<code>https://</code> and cannot be split reliably.\n\n"
            "Type /start for help.",
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
            # Show detailed error immediately and stop
            error_msg = (
                f"PIPELINE FAILED at Stage {result['stage']}\n"
                f"HTTP Code: {result['http']}\n"
                f"Error: {result['msg']}"
            )
            print(f"Chat {chat_id} | Attempt {attempt} FAILED: {error_msg}")
            await status_msg.edit_text(error_msg)
            return  # Stop retrying on pipeline failure

        # Step 2: Check if offer completed
        check = await asyncio.to_thread(check_offer_completed, check_link)
        status = check["status"]
        debug_text = check["text"]

        if status == "completed":
            await status_msg.edit_text(
                f"✅ OFFER COMPLETED!\n\n"
                f"Completed on attempt: {attempt}/{MAX_RETRIES}\n"
                f"Matched: {check.get('keyword', '')}\n"
                f"Response: {debug_text[:200]}"
            )
            print(f"Chat {chat_id} | COMPLETED on attempt {attempt}")
            return

        if status == "rejected":
            # Terminal site rejection - retrying 30x would be misleading.
            await status_msg.edit_text(
                f"❌ CHECK LINK REJECTED (attempt {attempt})\n"
                f"HTTP: {check['http']} | {check['raw_len']} bytes\n\n"
                f"Response:\n{debug_text[:400]}"
            )
            print(f"Chat {chat_id} | REJECTED on attempt {attempt}: {debug_text[:200]}")
            return

        if status == "short":
            # Short plain message we don't recognise. Almost certainly a
            # completion notice with new wording - show it so it can be added.
            await status_msg.edit_text(
                f"⚠️ SHORT RESPONSE - not a full page, but no known phrase "
                f"matched (attempt {attempt}).\n"
                f"This is probably a completion message with different wording.\n\n"
                f"HTTP: {check['http']} | {check['raw_len']} bytes\n\n"
                f"Response:\n{debug_text[:400]}"
            )
            print(f"Chat {chat_id} | SHORT/UNKNOWN on attempt {attempt}: {debug_text[:200]}")
            return

        if status == "error":
            print(f"Chat {chat_id} | Attempt {attempt} | Check link error: {debug_text[:200]}")
        elif attempt == 1 or attempt % 5 == 0:
            print(f"Chat {chat_id} | Attempt {attempt} | Pending "
                  f"({check['raw_len']} bytes = full page). {debug_text[:150]}")

        # Not completed → wait and retry
        await asyncio.sleep(RETRY_DELAY)

    # Exhausted all retries - show last debug info
    await status_msg.edit_text(
        f"❌ Max retries ({MAX_RETRIES}) reached.\n"
        f"Offer did NOT complete.\n\n"
        f"Last check response:\n{debug_text[:500]}"
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
