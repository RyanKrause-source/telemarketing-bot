# TeleMarketing — Telegram bot

Send it documents, then ask questions about them. Free to run.

## What you need

- Your bot token from BotFather (bot is `@ryan_telemarketing_bot`)
- A free Gemini API key from https://aistudio.google.com/apikey
- A free Render.com account

## 1. Deploy to Render

1. In Render, click **New +** → **Blueprint**, point it at this repo. It will read `render.yaml` and set up the web service automatically.
2. In the service's **Environment** tab, set:
   - `TELEGRAM_BOT_TOKEN` — your bot token from BotFather
   - `GEMINI_API_KEY` — your Gemini API key
   - `WEBHOOK_SECRET` — any random string you make up, used to keep your webhook URL private
3. Deploy. Render will give you a URL like `https://telemarketing-bot-xxxx.onrender.com`.

## 2. Point Telegram at your bot

Once deployed, register the webhook by visiting this URL once in your browser:

```
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<YOUR-RENDER-URL>/webhook/<WEBHOOK_SECRET>
```

You should see `{"ok":true,"result":true,"description":"Webhook was set"}`.

## 3. Use it

Open Telegram, message `@ryan_telemarketing_bot`, send `/start`, then send it a PDF/DOCX/TXT file and ask a question about it.

## Notes on the free tier

- Render free web services sleep after ~15 minutes of no traffic. The first message after a quiet period may take 30-60 seconds to get a reply while it wakes up. A free uptime monitor (e.g. UptimeRobot) pinging the `/` URL every 10 minutes avoids this.
- Stored context lives in a local SQLite file on the Render instance. On the free plan there's no persistent disk, so a redeploy/restart clears it.
- Gemini's free tier has real rate limits. If you hit them, the bot passes along an error message.
- `/reset` clears what the bot remembers for that chat.
