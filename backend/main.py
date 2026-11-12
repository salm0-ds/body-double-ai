import os
import requests
from fastapi import FastAPI, Request

app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def send_telegram_message(chat_id: int, text: str):
    """Sends a text response back to the user on Telegram."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    requests.post(url, json=payload)

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
            
    return {"status": "ok"}