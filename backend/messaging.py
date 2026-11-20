"""Telegram sending + the messages table, so every message in and out is stored and the AI can see recent history."""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from database_conn import create_db_conn

log = logging.getLogger("telegram-agent.messaging")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{os.getenv('TELEGRAM_TOKEN')}"

HISTORY_LIMIT = 10
HISTORY_HOURS = 6


def _send_telegram(chat_id: str, text: str, reply_markup: Optional[dict] = None) -> Optional[int]:
  """POST to Telegram. Returns Telegram's message_id, or None if it failed (never raises)."""
  payload = {"chat_id": chat_id, "text": text}
  if reply_markup:
    payload["reply_markup"] = json.dumps(reply_markup)
  try:
    response = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=10)
  except requests.RequestException:
    log.exception("Telegram sendMessage request errored")
    return None
  if not response.ok:
    log.error("Telegram sendMessage failed (%s): %s", response.status_code, response.text)
    return None
  log.info("Telegram message sent to %s", chat_id)
  return response.json()["result"]["message_id"]


async def save_message(role: str, content: str, message_type: str = "chat", *,
                       related_app: Optional[str] = None, schedule_block_id: Optional[str] = None,
                       metadata: Optional[dict] = None) -> Optional[str]:
  """Insert into the messages table. Returns the new row id, or None if it failed (never raises)."""
  row = {
      "role": role,
      "content": content,
      "message_type": message_type,
      "related_app": related_app,
      "schedule_block_id": schedule_block_id,
      "metadata": metadata or {},
  }
  try:
    db = await create_db_conn()
    response = await db.table("messages").insert(row).execute()
    return response.data[0]["id"] if response.data else None
  except Exception:
    log.exception("Saving %s message to Supabase failed", role)
    return None


async def notify_me(text: str, message_type: str = "chat", *, related_app: Optional[str] = None,
                    schedule_block_id: Optional[str] = None, reply_markup: Optional[dict] = None) -> Optional[str]:
  """Send a Telegram message to you and store it. Returns the stored message id (or None)."""
  chat_id = os.getenv("TELEGRAM_CHAT_ID")
  telegram_id = None
  if chat_id:
    telegram_id = await asyncio.to_thread(_send_telegram, chat_id, text, reply_markup)
  else:
    log.error("TELEGRAM_CHAT_ID is not set, message not sent: %s", text)
  return await save_message(
      "assistant", text, message_type,
      related_app=related_app, schedule_block_id=schedule_block_id,
      metadata={"telegram_message_id": telegram_id, "delivered": telegram_id is not None},
  )


async def get_recent_messages(limit: int = HISTORY_LIMIT, hours: int = HISTORY_HOURS) -> list[dict]:
  """Last `limit` messages from the past `hours`, oldest first, as OpenAI input items. [] on failure."""
  since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
  try:
    db = await create_db_conn()
    response = (
        await db.table("messages")
        .select("role, content, sent_at")
        .gte("sent_at", since)
        .order("sent_at", desc=True)
        .limit(limit)
        .execute()
    )
  except Exception:
    log.exception("Loading message history failed, continuing without it")
    return []
  rows = list(reversed(response.data or []))
  return [{"role": r["role"], "content": f"[{r['sent_at'][11:16]} UTC] {r['content']}"}
          for r in rows if r["role"] in ("user", "assistant")]
