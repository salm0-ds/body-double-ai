import os
import requests
import json
from datetime import datetime
from fastapi import FastAPI, Header, HTTPException, status, Request
from datetime import datetime

app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

CRON_SECRET = os.getenv("CRON_SECRET", "some-secret-token")



def send_telegram_message(chat_id: int, text: str):
    """Sends a text response back to the user on Telegram."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    requests.post(url, json=payload)

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

@app.post("/trigger-scheduled-check")
async def trigger_scheduled_check(authorization: str = Header(None)):
  # Security check so random people can't trigger your bot
  if authorization != f"Bearer {CRON_SECRET}":
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
    )

  # Optional: Restrict to your 12-hour active window (e.g., 8 AM to 8 PM)
  current_hour = datetime.now().hour
  if not (8 <= current_hour < 24):
    return {"status": "skipped", "reason": "Outside active 12-hour window"}

  # TODO: Put your background check logic here
  # (e.g., check emails, scan tasks, and push a message to your Telegram)
  ai_summary_message = (
      "🤖 **Automated Check:** Here is your scheduled update from your backend!"
  )

  # 3. Push the message straight to your personal Telegram chat ID
  YOUR_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
  send_telegram_message(YOUR_CHAT_ID, ai_summary_message)

  return {"status": "success", "message": "Check executed successfully"}