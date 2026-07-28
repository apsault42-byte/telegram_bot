"""
Telegram Bot - Vmtrk Pipeline Runner
3-stage redirect chain (Vmtrk -> AdPropel -> Adsphire -> Panel)
run PIPELINE_RUNS times in a row, identical timing/headers to xm3.php.

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

DEFAULT_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8"
)


# ======================================================================
# URL PARSER
# ======================================================================
def is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except ValueError:
        return False


def parse_vmtrk_link(text: str) -> str | None:
    text = text.strip()
    first_token = re.split(r"\s+", text, maxsplit=1)[0].strip()
    if not is_http_url(first_token):
        return None
    if urlsplit(first_token).hostname not in VMTRK_HOSTS:
        return None
    return first_token


# ======================================================================
# REDIRECT EXTRACTION (3-layer fallback, matches xm3.php exactly)
# ======================================================================
def extract_redirect(headers: dict, raw: str) -> str:
    """Three-layer fallback identical in spirit to xm3.php:

      1. Location: response header (handled by resp.headers below)
      2. regex on raw response for 'location: <url>'
      3. regex on raw response for '<a href="...">'
    """
    raw = raw or ""
    # Layer 1 caller already tried .headers.get("Location"). Here we do
    # layers 2 and 3, scanning the FULL response (headers + body) just like
    # xm3.php does with CURLOPT_HEADER=true on $res1/$res2.
    m = re.search(r"location:\s*([^\s\r\n]+)", raw, re.IGNORECASE)
    if m:
        url = m.group(1).strip()
        return html_mod.unescape(url)
    m = re.search(r'<a\s+href=["\']([^"\']+)["\']', raw, re.IGNORECASE)
    if m:
        url = m.group(1).strip()
        return html_mod.unescape(url)
    return ""


def get_full_response(resp: requests.Response) -> str:
    """Concatenate status line + headers + body, like cURL with HEADER=true.

    xm3.php's regex scans the WHOLE response (headers + body) for the
    'location: ...' string. Python's requests exposes parsed headers via
    resp.headers, but to be safe (and to match PHP exactly) we build the same
    blob. We only need it as a fallback path.
    """
    parts = [f"HTTP/1.1 {resp.status_code}"]
    for k, v in resp.headers.items():
        parts.append(f"{k}: {v}")
    parts.append("")
    parts.append(resp.text or "")
    return "\r\n".join(parts)


# ======================================================================
# PIPELINE STAGES
# ======================================================================
def run_pipeline(vmtrk_link: str) -> dict:
    """3-stage pipeline, one independent request per stage (no shared session
    cookies -- matches xm3.php which uses 3 separate cURL handles)."""
    ip_headers = {
        "X-Forwarded-For": SPOOF_IP,
        "X-Real-IP": SPOOF_IP,
        "Client-IP": SPOOF_IP,
    }

    # ---------- STAGE 1: vmtrk.com -> AdPropel ----------
    stage1_headers = {
        "User-Agent": USER_AGENT,
        "Accept": DEFAULT_ACCEPT,
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
        **ip_headers,
    }

    try:
        r1 = requests.get(
            vmtrk_link,
            headers=stage1_headers,
            allow_redirects=False,
            timeout=15,
            verify=False,
        )
    except requests.RequestException as e:
        return {"ok": False, "stage": 1, "http": 0, "msg": str(e)[:80]}

    ad_propel_url = r1.headers.get("Location", "") or extract_redirect(
        r1.headers, get_full_response(r1)
    )
    if not ad_propel_url.startswith("http"):
        raw_text = re.sub(
            r"\s+", " ", re.sub(r"<[^>]+>", "", r1.text or "")
        ).strip()[:70]
        return {
            "ok": False, "stage": 1, "http": r1.status_code,
            "msg": f"Stage 1 Failed (HTTP {r1.status_code}): {raw_text or 'Empty Response'}",
        }

    # ---------- STAGE 2: AdPropel -> Adsphire ----------
    stage2_headers = {
        "User-Agent": USER_AGENT,
        "Accept": DEFAULT_ACCEPT,
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "X-Requested-With": "com.mycompany.app.soulbrowser",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Forwarded-For": SPOOF_IP,
        "X-Real-IP": SPOOF_IP,
    }

    try:
        r2 = requests.get(
            ad_propel_url,
            headers=stage2_headers,
            allow_redirects=False,
            timeout=12,
            verify=False,
        )
    except requests.RequestException as e:
        return {"ok": False, "stage": 2, "http": 0, "msg": str(e)[:80]}

    adsphire_url = r2.headers.get("Location", "") or extract_redirect(
        r2.headers, get_full_response(r2)
    )
    if not adsphire_url.startswith("http"):
        raw_text = re.sub(
            r"\s+", " ", re.sub(r"<[^>]+>", "", r2.text or "")
        ).strip()[:60]
        return {
            "ok": False, "stage": 2, "http": r2.status_code,
            "msg": f"Stage 2 Failed (HTTP {r2.status_code}): {raw_text or 'Empty Response'}",
        }

    # ---------- STAGE 3: POST adsphire URL to Panel ----------
    panel_headers = {
        "Host": "mr4u.iceiy.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
        "Accept": DEFAULT_ACCEPT,
        "Cookie": "__test=0e3273fa003e3c50fee09c3b59d1cc77",
        "Origin": "https://mr4u.iceiy.com",
        "Referer": "https://mr4u.iceiy.com/?id=152535&i=1",
        "X-Requested-With": "com.mycompany.app.soulbrowser",
        "Accept-Language": "en-US,en;q=0.9",
    }
    post_data = {"link": adsphire_url, "submit": "SUBMIT"}

    try:
        r3 = requests.post(
            PANEL_URL,
            data=post_data,
            headers=panel_headers,
            timeout=12,
            verify=False,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        return {"ok": False, "stage": 3, "http": 0, "msg": str(e)[:80]}

    if r3.status_code in (200, 302):
        return {"ok": True, "link": adsphire_url}
    return {
        "ok": False, "stage": 3, "http": r3.status_code,
        "msg": f"Panel Post Fail (HTTP {r3.status_code})",
    }


# ======================================================================
# BOT HANDLERS
# ======================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        f"Starting pipeline x{PIPELINE_RUNS}...\n\nLink: {vmtrk_link[:80]}"
    )

    summary_lines = []

    for run_no in range(1, PIPELINE_RUNS + 1):
        try:
            await status_msg.edit_text(
                f"Pipeline x{PIPELINE_RUNS}\nRun {run_no}/{PIPELINE_RUNS}..."
            )
        except Exception:
            pass

        print(f"Chat {chat_id} | Run {run_no}")
        result = await asyncio.to_thread(run_pipeline, vmtrk_link)

        if result["ok"]:
            summary_lines.append(f"Run {run_no}: SUCCESS  ->  {result['link']}")
        else:
            summary_lines.append(
                f"Run {run_no}: FAILED   stage={result['stage']} "
                f"http={result['http']}  {result.get('msg','')}"
            )

        if run_no < PIPELINE_RUNS:
            await asyncio.sleep(RUN_GAP_SECONDS)

    success_count = sum(
        1 for ln in summary_lines if ln.startswith("Run ") and "SUCCESS" in ln
    )
    header = (
        f"\u2705 All {PIPELINE_RUNS} runs succeeded."
        if success_count == PIPELINE_RUNS
        else f"\u26a0\ufe0f Done. {success_count}/{PIPELINE_RUNS} runs succeeded."
    )
    final_text = f"{header}\n\n" + "\n".join(summary_lines)
    if len(final_text) > 4000:
        final_text = final_text[:4000] + "\n... (truncated)"
    await status_msg.edit_text(final_text)
    print(f"Chat {chat_id} | Done: {success_count}/{PIPELINE_RUNS} succeeded")


# ======================================================================
# MAIN
# ======================================================================
def main() -> None:
    print("Bot starting...")
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is empty!")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    port = os.environ.get("PORT")
    if port:
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
        print("Polling mode active (local testing). Press Ctrl+C to stop.")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()