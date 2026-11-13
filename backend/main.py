import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, Header, HTTPException, status, Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("telegram-agent")

app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# No default: if CRON_SECRET isn't set, the scheduled endpoint refuses every call.
CRON_SECRET = os.getenv("CRON_SECRET")
# Render servers run in UTC, so the active window uses your own timezone.
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "UTC"))
ACTIVE_START_HOUR = int(os.getenv("ACTIVE_START_HOUR", "8"))
ACTIVE_END_HOUR = int(os.getenv("ACTIVE_END_HOUR", "20"))


def send_telegram_message(chat_id: int, text: str):
    """Sends a text response back to the user on Telegram. Raises on failure."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()

def send_option_buttons(chat_id: int, question_text: str):
  """Sends a message with clickable Yes/No inline buttons."""
  url = f"{TELEGRAM_API_URL}/sendMessage"

  # Define the inline keyboard layout (1 row with two buttons)
  inline_keyboard = {
      "inline_keyboard": [[
          {"text": "✅ Yes", "callback_data": "answer_yes"},
          {"text": "❌ No", "callback_data": "answer_no"},
      ]]
  }

  payload = {
      "chat_id": chat_id,
      "text": question_text,
      "reply_markup": json.dumps(inline_keyboard),
  }

  response = requests.post(url, json=payload)
  return response.json()

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Receives incoming JSON updates from Telegram whenever you send a message.
    """
    data = await request.json()
    
    # Check if the incoming payload contains a standard message
    if "message" in data:
        message = data["message"]
        chat_id = message["chat"]["id"]
        
        # Extract user text (if it's a text message)
        user_text = message.get("text", "")
        
        if user_text:
            # TODO: Plug your custom AI engine / function-calling logic here!
            # Example response echoing back:
            ai_response = f"Custom AI Backend received: '{user_text}'"
            
            # Send the response back to your phone via Telegram API
            send_telegram_message(chat_id, ai_response)

        if user_text.lower() == "check in":
            send_option_buttons(chat_id, "Have you completed your current task?")
        else:
        # General AI reply for other messages
            requests.post(
               f"{TELEGRAM_API_URL}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"I heard you say: '{user_text}'. Processing...",
                },
            )
            
    return {"status": "ok"}

@app.get("/health")
async def health():
    """Cheap endpoint the cron script can use to wake the app up."""
    return {"status": "ok"}


@app.post("/trigger-scheduled-check")
async def trigger_scheduled_check(authorization: str = Header(None)):
    # Security check so random people can't trigger your bot
    if not CRON_SECRET or authorization != f"Bearer {CRON_SECRET}":
        logger.warning("Scheduled check rejected: bad or missing token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )

    # Only run inside your active window (in your timezone, not the server's)
    now = datetime.now(TIMEZONE)
    if not (ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR):
        logger.info("Scheduled check skipped at %s (outside active window)", now)
        return {"status": "skipped", "reason": "Outside active window"}

    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        logger.error("TELEGRAM_CHAT_ID is not set")
        raise HTTPException(status_code=500, detail="TELEGRAM_CHAT_ID is not set")

    try:
        # TODO: Put your background check logic here
        # (e.g., check emails, scan tasks, and push a message to your Telegram)
        ai_summary_message = (
            "🤖 Automated Check: Here is your scheduled update from your backend!"
        )
        send_telegram_message(int(chat_id), ai_summary_message)
    except Exception:
        logger.exception("Scheduled check failed")
        raise HTTPException(status_code=500, detail="Scheduled check failed")

    logger.info("Scheduled check sent at %s", now)
    return {"status": "success", "message": "Check executed successfully"}