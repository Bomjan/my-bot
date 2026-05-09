# GCIT VLE Calendar — Setup Guide

## Step 1 — Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

## Step 2 — Google Calendar API credentials

1. Go to https://console.cloud.google.com/
2. Create a new project (e.g. "VLE Calendar")
3. Go to **APIs & Services → Enable APIs** → search "Google Calendar API" → Enable
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Application type: **Desktop app**
6. Download the JSON file and save it as `google_credentials.json` in this folder

## Step 3 — First run (one-time Google login)

```bash
python main.py
```

A browser window will open asking you to log in to Google and allow access.
After that, a `token.json` is saved — future runs won't need the browser.

## Step 4 — Automate with cron (runs daily, no manual effort)

Open your crontab:
```bash
crontab -e
```

Add this line to run every day at 7 AM and 6 PM:
```
0 7,18 * * * /home/sundrabomjan/anaconda3/bin/python /home/sundrabomjan/Desktop/Projects/vle-calendar/main.py >> /home/sundrabomjan/Desktop/Projects/vle-calendar/cron.log 2>&1
```

Save and exit. The script now runs automatically — no terminal needed, no 24/7 process.

## How it works

- Script scrapes your VLE → pushes events directly to your Google Calendar
- Your phone syncs Google Calendar automatically
- If an event already exists, it gets updated (no duplicates)
- Runs twice a day to catch any new deadlines added by lecturers
