import os
import json
import asyncio
import logging
import requests
from datetime import datetime
from fastapi import FastAPI, Header, HTTPException, status, Request

from database_conn import create_db_conn
from google_cal import get_today_events
from llm_model import generate_body_double_nudge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("telegram-agent")

app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
APPLE_SHORTCUT_SECRET = os.getenv("APPLE_SHORTCUT_SECRET")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
CRON_SECRET = os.getenv("CRON_SECRET", "some-secret-token")



def send_telegram_message(chat_id: int, text: str):
    """Sends a text response back to the user on Telegram."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if not response.ok:
            log.error("Telegram sendMessage failed (%s): %s", response.status_code, response.text)
        else:
            log.info("Telegram message sent to %s", chat_id)
    except requests.RequestException:
        log.exception("Telegram sendMessage request errored")

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

def get_notion_info():
   home_page_id = "35122950-dc11-8046-b89f-d4c0370816db"
   url = f"https://api.notion.com/v1/pages/{home_page_id}/markdown"
   headers = {
      "Notion-Version": "2026-03-11",
      "Authorization": f"Bearer {NOTION_API_KEY}"
      }
   output = requests.get(url, headers=headers).text
   return output



@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Receives incoming JSON updates from Telegram whenever you send a message.
    """
    data = await request.json()
    log.info("/webhook update: %s", data)

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
        if user_text.lower() == "notion":
           output = get_notion_info()
           send_option_buttons(chat_id, output)
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

def _check_shortcut_auth(endpoint: str, authorization: str):
   if authorization != f"Bearer {APPLE_SHORTCUT_SECRET}":
      log.warning("%s rejected: bad or missing Authorization header", endpoint)
      raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized apple trigger"
        )


async def _log_app_event(app_name: str, event_type: str):
   """Append an open/close event to Supabase. Failures are logged, never raised."""
   data = {
      "app_name": app_name,
      "event_type": event_type,
      "timestamp": datetime.utcnow().isoformat()
      }
   try:
      supabase = await create_db_conn()
      await supabase.table("app_activity_logs").insert(data).execute()
      log.info("Logged %s %s to Supabase", app_name, event_type)
   except Exception:
      log.exception("Supabase insert failed for %s %s", app_name, event_type)


def _send_to_me(text: str):
   chat_id = os.getenv("TELEGRAM_CHAT_ID")
   if not chat_id:
      log.error("TELEGRAM_CHAT_ID is not set")
      raise HTTPException(status_code=500, detail="TELEGRAM_CHAT_ID not set")
   send_telegram_message(int(chat_id), text)


async def handle_app_opened(endpoint: str, app_name: str, authorization: str):
   log.info("%s called", endpoint)
   _check_shortcut_auth(endpoint, authorization)

   current_hour = datetime.now().hour
   if not (6 <= current_hour < 24):
      log.info("%s skipped: server hour is %s", endpoint, current_hour)
      return {"status": "skipped", "reason": "Outside active 12-hour window"}

   await _log_app_event(app_name, "opened")

   # Generate the nudge — fall back to a plain message if OpenAI fails
   try:
      message = await generate_body_double_nudge(app_name)
   except Exception:
      log.exception("OpenAI nudge generation failed for %s, using fallback", app_name)
      message = f"You opened {app_name}. Is this what you should be doing right now?"

   _send_to_me(message)
   return {"status": "success", "message": "Check executed successfully"}


async def handle_app_closed(endpoint: str, app_name: str, authorization: str):
   log.info("%s called", endpoint)
   _check_shortcut_auth(endpoint, authorization)

   current_hour = datetime.now().hour
   if not (8 <= current_hour < 24):
      log.info("%s skipped: server hour is %s", endpoint, current_hour)
      return {"status": "skipped", "reason": "Outside active 12-hour window"}

   await _log_app_event(app_name, "closed")

   _send_to_me(f"You closed {app_name}")
   return {"status": "success", "message": "Check executed successfully"}


@app.post("/youtube-opened")
async def youtube_opened(authorization: str = Header(None)):
   return await handle_app_opened("/youtube-opened", "YouTube", authorization)

@app.post("/youtube-closed")
async def youtube_closed(authorization: str = Header(None)):
   return await handle_app_closed("/youtube-closed", "YouTube", authorization)

@app.post("/chrome-opened")
async def chrome_opened(authorization: str = Header(None)):
   return await handle_app_opened("/chrome-opened", "Chrome", authorization)

@app.post("/chrome-closed")
async def chrome_closed(authorization: str = Header(None)):
   return await handle_app_closed("/chrome-closed", "Chrome", authorization)

@app.post("/ig-opened")
async def ig_opened(authorization: str = Header(None)):
   return await handle_app_opened("/ig-opened", "Instagram", authorization)

@app.post("/ig-closed")
async def ig_closed(authorization: str = Header(None)):
   return await handle_app_closed("/ig-closed", "Instagram", authorization)

@app.post("/snapchat-opened")
async def snapchat_opened(authorization: str = Header(None)):
   return await handle_app_opened("/snapchat-opened", "Snapchat", authorization)

@app.post("/snapchat-closed")
async def snapchat_closed(authorization: str = Header(None)):
   return await handle_app_closed("/snapchat-closed", "Snapchat", authorization)

@app.post("/google-opened")
async def google_opened(authorization: str = Header(None)):
   return await handle_app_opened("/google-opened", "Google", authorization)

@app.post("/google-closed")
async def google_closed(authorization: str = Header(None)):
   return await handle_app_closed("/google-closed", "Google", authorization)

@app.post("/daily-schedule-download")
async def daily_schedule_download(authorization: str = Header(None)):
   """Pull today's Google Calendar events into schedule_blocks (upsert = no duplicates)."""
   log.info("/daily-schedule-download called")
   _check_shortcut_auth("/daily-schedule-download", authorization)

   # Google client is blocking, so run it off the event loop
   try:
      rows = await asyncio.to_thread(get_today_events)
   except Exception as e:
      log.exception("Fetching Google Calendar events failed")
      raise HTTPException(status_code=500, detail=f"Google Calendar error: {e}")
   log.info("Fetched %d events from Google Calendar", len(rows))

   now = datetime.utcnow().isoformat()
   for row in rows:
      row["updated_at"] = now

   if rows:
      try:
         supabase = await create_db_conn()
         await supabase.table("schedule_blocks").upsert(rows, on_conflict="google_event_id").execute()
      except Exception as e:
         log.exception("Supabase upsert into schedule_blocks failed")
         raise HTTPException(status_code=500, detail=f"Supabase error: {e}")

   log.info("Synced %d calendar events to schedule_blocks", len(rows))
   return {"status": "success", "count": len(rows), "events": rows}

@app.post("/trigger-scheduled-check")
async def trigger_scheduled_check(authorization: str = Header(None)):
  # Security check so random people can't trigger your bot
  if authorization != f"Bearer {CRON_SECRET}":
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized cron trigger"
    )

  # Optional: Restrict to your 12-hour active window (e.g., 8 AM to 8 PM)
  current_hour = datetime.now().hour
  if not (8 <= current_hour < 24):
    return {"status": "skipped", "reason": "Outside active 12-hour window"}

  # TODO: Put your background check logic here
  # (e.g., check emails, scan tasks, and push a message to your Telegram)
  ai_summary_message = (
      "🤖 this is the new one **Automated Check:** Here is your scheduled update from your backend!"
  )

  # 3. Push the message straight to your personal Telegram chat ID
  YOUR_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
  send_telegram_message(YOUR_CHAT_ID, ai_summary_message)

  return {"status": "success", "message": "Check executed successfully"}