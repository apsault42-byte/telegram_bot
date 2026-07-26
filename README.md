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

## Deploy to Render (FREE)

### Step 1: Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### Step 2: Create Web Service on Render

1. Go to [Render Dashboard](https://dashboard.render.com/) → Sign in with GitHub
2. Click **New +** → **Web Service**
3. Connect your GitHub repo
4. Configure:

   | Setting | Value |
   |---------|-------|
   | **Build Command** | `pip install -r requirements.txt` |
   | **Start Command** | `python bot.py` |
   | **Instance Type** | **Free** ($0/month) |

5. Scroll down to **Health Check Path** → set it to: `/telegram`
6. Click **Create Web Service**

> **Why Web Service instead of Background Worker?** Render's free tier is only available for Web Services. The bot runs in webhook mode — Telegram pushes updates to your Render URL, and your bot responds instantly.

### Step 3: Verify

Wait 1-2 minutes for the deploy to finish. Your bot will be live at:
```
https://your-service-name.onrender.com
```

Open your bot on Telegram and send `/start`.

## Free Tier Limitations

- **Spins down after 15 minutes of inactivity** — wakes up automatically when someone messages the bot
- **First message after inactivity takes ~30 seconds** (cold start)
- **750 hours/month** — enough for one bot 24/7

## Local Testing

```bash
pip install -r requirements.txt
python bot.py
```
Runs in polling mode automatically (no PORT env var set).

Made by Slayer
