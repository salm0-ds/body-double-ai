import os
import logging
import requests
from datetime import date, datetime, timezone
from fastapi import FastAPI, Header, HTTPException, status, Request

from database_conn import create_db_conn
from schedule_check import run_schedule_check
from agent import handle_chat_message
from calendar_sync import sync_day_from_google
from planner import run_nightly_plan
from llm_model import generate_body_double_nudge
from messaging import get_recent_messages, notify_me, save_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("telegram-agent")

app = FastAPI()

APPLE_SHORTCUT_SECRET = os.getenv("APPLE_SHORTCUT_SECRET")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
CRON_SECRET = os.getenv("CRON_SECRET", "some-secret-token")



CHECK_IN_BUTTONS = {
    "inline_keyboard": [[
        {"text": "✅ Yes", "callback_data": "answer_yes"},
        {"text": "❌ No", "callback_data": "answer_no"},
    ]]
}

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
    Always returns 200 so Telegram doesn't keep retrying the same update.
    """
    data = await request.json()
    log.info("/webhook update: %s", data)

    message = data.get("message")
    if not message or not message.get("text"):
        return {"status": "ok"}

    chat_id = message["chat"]["id"]
    user_text = message["text"]

    # Only talk to you - otherwise anyone who finds the bot could spend OpenAI credits / touch your calendar
    if str(chat_id) != os.getenv("TELEGRAM_CHAT_ID"):
        log.warning("/webhook ignored message from unknown chat %s", chat_id)
        return {"status": "ok"}

    # Load history BEFORE saving this message, so it isn't sent to the AI twice
    history = await get_recent_messages()
    await save_message("user", user_text, metadata={"telegram_message_id": message.get("message_id")})

    if user_text.lower() == "check in":
        await notify_me("Have you completed your current task?", "check_in", reply_markup=CHECK_IN_BUTTONS)
    elif user_text.lower() == "notion":
        try:
            notion_text = get_notion_info()
        except Exception:
            log.exception("Fetching Notion page failed")
            notion_text = "Couldn't load the Notion page. Check the logs."
        await notify_me(notion_text, "chat")
    else:
        try:
            reply = await handle_chat_message(user_text, history)
        except Exception:
            log.exception("Chat agent failed")
            reply = "Sorry, something went wrong on my side. Check the logs."
        await notify_me(reply or "Done.", "chat")

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
      "timestamp": datetime.now(timezone.utc).isoformat()
      }
   try:
      supabase = await create_db_conn()
      await supabase.table("app_activity_logs").insert(data).execute()
      log.info("Logged %s %s to Supabase", app_name, event_type)
   except Exception:
      log.exception("Supabase insert failed for %s %s", app_name, event_type)


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
      message = await generate_body_double_nudge(app_name, await get_recent_messages())
   except Exception:
      log.exception("OpenAI nudge generation failed for %s, using fallback", app_name)
      message = f"You opened {app_name}. Is this what you should be doing right now?"

   await notify_me(message, "nudge", related_app=app_name)
   return {"status": "success", "message": "Check executed successfully"}


async def handle_app_closed(endpoint: str, app_name: str, authorization: str):
   log.info("%s called", endpoint)
   _check_shortcut_auth(endpoint, authorization)

   current_hour = datetime.now().hour
   if not (6 <= current_hour < 24):
      log.info("%s skipped: server hour is %s", endpoint, current_hour)
      return {"status": "skipped", "reason": "Outside active 12-hour window"}

   await _log_app_event(app_name, "closed")

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
   """Make today's schedule_blocks match Google Calendar (adds, edits and removes)."""
   log.info("/daily-schedule-download called")
   _check_shortcut_auth("/daily-schedule-download", authorization)
   try:
      result = await sync_day_from_google(date.today())
   except Exception as e:
      log.exception("Calendar sync failed")
      raise HTTPException(status_code=500, detail=f"Calendar sync error: {e}")
   return {"status": "success", "count": result["upserted"], **result}


@app.post("/nightly-plan")
async def nightly_plan(authorization: str = Header(None)):
   """Run at ~10pm: fill tomorrow's calendar from Todoist if it has under 7h scheduled."""
   log.info("/nightly-plan called")
   if authorization != f"Bearer {CRON_SECRET}":
      raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized cron trigger")
   try:
      return await run_nightly_plan()
   except Exception as e:
      log.exception("Nightly plan failed")
      await notify_me("⚠️ Nightly planning failed. Check the logs.", "plan")
      raise HTTPException(status_code=500, detail=f"Nightly plan error: {e}")

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

  try:
    supabase = await create_db_conn()
    result = await run_schedule_check(supabase)
  except Exception as e:
    log.exception("Scheduled check failed")
    raise HTTPException(status_code=500, detail=f"Scheduled check error: {e}")

  return {"status": "success", **result}