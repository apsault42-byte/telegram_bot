# Smart Offer Completion Bot

Telegram bot that automates offer completion via a multi-stage redirect pipeline
(Vmtrk → AdPropel → Adsphire → Panel) with intelligent retry logic.

## How It Works

1. Send **two links** in one message (no space between them):
   - **Link 1**: Vmtrk tracking URL
   - **Link 2**: Check URL (to verify completion)

2. Bot runs the pipeline once, then checks if the offer completed.

3. If not completed → retries automatically (up to 30 times, 3s apart).

4. Stops as soon as completion keywords are detected.

## Deploy to Render

1. Push this repo to GitHub.

2. Go to [Render Dashboard](https://dashboard.render.com/) → **New +** → **Background Worker**.

3. Connect your GitHub repo.

4. Configure:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Instance Type**: Free

5. Click **Create Background Worker**. Done!

## Local Testing

```bash
pip install -r requirements.txt
python bot.py
```

Made by Slayer
