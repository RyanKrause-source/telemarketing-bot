"""
TeleMarketing - a Telegram bot that reads files you send it and answers
questions using them as context. Powered by Google Gemini's free tier.

How it works:
- Send the bot any document (PDF, DOCX, or TXT) and it extracts the text
  and remembers it, per-chat.
- Then just ask questions in the chat - it answers using everything you've
  sent so far as context.
- /reset clears the stored context for that chat.
- /start shows a short intro.

Runs as a Flask app that Telegram calls via webhook (see README.md for
deployment + webhook setup instructions).
"""

import io
import os
import sqlite3
import logging
import time

import requests
from flask import Flask, request, jsonify
from pypdf import PdfReader
import docx

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("telemarketing-bot")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_MODEL_FALLBACKS = ["gemini-3.5-flash-lite"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

MAX_CONTEXT_CHARS = 120_000

DB_PATH = os.environ.get("DB_PATH", "context.db")

app = Flask(__name__)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS context (
            chat_id TEXT PRIMARY KEY,
            content TEXT NOT NULL DEFAULT ''
        )"""
    )
    return conn


def get_context(chat_id: str) -> str:
    conn = db()
    row = conn.execute(
        "SELECT content FROM context WHERE chat_id = ?", (str(chat_id),)
    ).fetchone()
    conn.close()
    return row[0] if row else ""


def append_context(chat_id: str, label: str, text: str) -> None:
    conn = db()
    existing = get_context(chat_id)
    addition = f"\n\n--- {label} ---\n{text.strip()}\n"
    updated = (existing + addition)[-MAX_CONTEXT_CHARS:]
    conn.execute(
        """INSERT INTO context (chat_id, content) VALUES (?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET content = excluded.content""",
        (str(chat_id), updated),
    )
    conn.commit()
    conn.close()


def clear_context(chat_id: str) -> None:
    conn = db()
    conn.execute("DELETE FROM context WHERE chat_id = ?", (str(chat_id),))
    conn.commit()
    conn.close()


def send_message(chat_id, text: str) -> None:
    for i in range(0, len(text), 4000):
        chunk = text[i : i + 4000]
        r = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": chunk},
            timeout=30,
        )
        if not r.ok:
            log.error("sendMessage failed: %s", r.text)


def extract_text_from_bytes(filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if lower.endswith(".docx"):
        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs)
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def download_telegram_file(file_id: str) -> tuple[str, bytes]:
    r = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=30)
    r.raise_for_status()
    file_path = r.json()["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    content = requests.get(url, timeout=60).content
    return file_path.rsplit("/", 1)[-1], content


def ask_gemini(context_text: str, question: str) -> str:
    if context_text.strip():
        prompt = (
            "You are a helpful assistant answering questions using ONLY the "
            "reference material below, provided by the user. If the answer "
            "isn't in the material, say so plainly rather than guessing.\n\n"
            f"--- REFERENCE MATERIAL ---\n{context_text}\n--- END MATERIAL ---\n\n"
            f"Question: {question}"
        )
    else:
        prompt = (
            "No reference files have been provided yet. Politely let the "
            "user know they can send you a document (PDF, DOCX, or TXT) and "
            "then ask questions about it. Their message was:\n\n"
            f"{question}"
        )

    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    models_to_try = [GEMINI_MODEL] + [m for m in GEMINI_MODEL_FALLBACKS if m != GEMINI_MODEL]
    last_error = None
    for model in models_to_try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        retries = 2 if model == GEMINI_MODEL else 1
        for attempt in range(retries):
            r = requests.post(url, json=payload, timeout=25)
            if r.ok:
                data = r.json()
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    return "Sorry, I couldn't generate a response for that."
            last_error = r.text
            if r.status_code == 503 and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            break

    log.error("Gemini error: %s", last_error)
    return "Sorry, I hit an error talking to the AI service. Please try again in a moment."


@app.route("/", methods=["GET"])
def health():
    return jsonify(status="ok", bot="TeleMarketing")


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify(ok=True)

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    try:
        if text.startswith("/start"):
            send_message(
                chat_id,
                "Hi, I'm TeleMarketing. Send me documents (PDF, DOCX, or TXT) "
                "and I'll remember them. Then ask me anything and I'll answer "
                "using what you've sent. Use /reset to clear what I remember.",
            )
        elif text.startswith("/reset"):
            clear_context(chat_id)
            send_message(chat_id, "Cleared. I've forgotten everything you'd sent me.")
        elif "document" in message:
            doc = message["document"]
            filename = doc.get("file_name", "file")
            send_message(chat_id, f"Reading {filename}...")
            fname, data = download_telegram_file(doc["file_id"])
            extracted = extract_text_from_bytes(fname, data)
            if not extracted.strip():
                send_message(chat_id, f"Couldn't extract any text from {filename}.")
            else:
                append_context(chat_id, filename, extracted)
                send_message(
                    chat_id,
                    f"Got it - added {filename} ({len(extracted)} characters) "
                    f"to what I remember for this chat. Ask away.",
                )
        elif text:
            context_text = get_context(chat_id)
            answer = ask_gemini(context_text, text)
            send_message(chat_id, answer)
        else:
            send_message(chat_id, "I can only handle text and documents (PDF/DOCX/TXT) right now.")
    except Exception:
        log.exception("Error handling update")
        send_message(chat_id, "Something went wrong on my end handling that - please try again.")

    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
