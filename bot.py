"""
Telegram Bot - Vmtrk Pipeline Runner
Sends a single vmtrk click URL through the 3-stage redirect chain
(Vmtrk -> AdPropel -> Adsphire -> Panel) N times in a row.
Same behaviour as xm3.php's "Execute 5x Pipeline".

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

# Matches xm3.php: run the pipeline 5 times with 500 ms between runs.
PIPELINE_RUNS = 5
RUN_GAP_SECONDS = 0.5

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 16; V2437 Build/BP2A.250605.031.A3_V000L1) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Utgmqff/4.0 Chrome/149.0.7827.159 Mobile Safari/537.36"
)

SPOOF_IP = "103.156.19.178"  # Change this to your mobile proxy IP if needed

VMTRK_HOSTS = ("vmtrk.com", "www.vmtrk.com")


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


def parse_vmtrk_link(text: str) -> str | None:
    """Extract the first vmtrk URL from the message.

    Just one link is needed - the same one xm3.php uses. The whole message
    is taken as the link, or the first whitespace-separated token if there
    is trailing text.
    """
    text = text.strip()
    first_token = re.split(r"\s+", text, maxsplit=1)[0].strip()
    if not is_http_url(first_token):
        return None
    if urlsplit(first_token).hostname not in VMTRK_HOSTS:
        return None
    return first_token


# ======================================================================
# PIPELINE STAGES
# ======================================================================
def run_pipeline(vmtrk_link: str) -> dict:
    """
    Execute the 3-stage redirect pipeline.

    Returns:
        {"ok": True,  "link": final_url}
        {"ok": False, "stage": N, "http": code, "msg": reason}
    """
    session = requests.Session()

    # --- STAGE 1: vmtrk.com -> AdPropel redirect ---
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

    # --- STAGE 2: AdPropel -> Adsphire redirect ---
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
# BOT HANDLERS
# ======================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    help_text = (
        "\u26a1 <b>Vmtrk Pipeline Bot</b>\n\n"
        "Send me a <b>vmtrk click link</b> and I will run the "
        "3-stage redirect chain <b>{runs}x</b>, with a {gap}s gap.\n\n"
        "<b>Example:</b>\n"
        "<code>https://www.vmtrk.com/click?offer_id=...</code>\n\n"
        "Made by Slayer"
    ).format(runs=PIPELINE_RUNS, gap=RUN_GAP_SECONDS)
    await update.message.reply_text(help_text, parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the pipeline PIPELINE_RUNS times on the supplied vmtrk link."""
    text = update.message.text
    chat_id = update.effective_chat.id

    vmtrk_link = parse_vmtrk_link(text)
    if not vmtrk_link:
        await update.message.reply_text(
            "\u274c Please send a <b>vmtrk click link</b>.\n\n"
            "Example:\n"
            "<code>https://www.vmtrk.com/click?offer_id=...</code>\n\n"
            "Type /start for help.",
            parse_mode="HTML",
        )
        return

    status_msg = await update.message.reply_text(
        f"Starting pipeline x{PIPELINE_RUNS}...\n\n"
        f"Link: {vmtrk_link[:80]}"
    )

    summary_lines = []

    for run_no in range(1, PIPELINE_RUNS + 1):
        try:
            await status_msg.edit_text(
                f"Pipeline x{PIPELINE_RUNS}\n"
                f"Run {run_no}/{PIPELINE_RUNS}..."
            )
        except Exception:
            pass

        print(f"Chat {chat_id} | Run {run_no}")
        result = await asyncio.to_thread(run_pipeline, vmtrk_link)

        if result["ok"]:
            summary_lines.append(f"Run {run_no}: SUCCESS  ->  {result['link']}")
        else:
            msg = result.get("msg", "")
            summary_lines.append(
                f"Run {run_no}: FAILED   stage={result['stage']} "
                f"http={result['http']}  {msg}"
            )

        if run_no < PIPELINE_RUNS:
            await asyncio.sleep(RUN_GAP_SECONDS)

    success_count = sum(1 for line in summary_lines if line.startswith("Run ") and "SUCCESS" in line)
    header = (
        f"\u2705 All {PIPELINE_RUNS} runs succeeded."
        if success_count == PIPELINE_RUNS
        else f"\u26a0\ufe0f Done. {success_count}/{PIPELINE_RUNS} runs succeeded."
    )

    final_text = f"{header}\n\n" + "\n".join(summary_lines)
    # Telegram message limit is 4096 chars.
    if len(final_text) > 4000:
        final_text = final_text[:4000] + "\n... (truncated)"

    await status_msg.edit_text(final_text)
    print(f"Chat {chat_id} | Done: {success_count}/{PIPELINE_RUNS} succeeded")


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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    port = os.environ.get("PORT")

    if port:
        # --- RENDER WEB SERVICE (FREE) - Webhook Mode ---
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
        if not render_url:
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