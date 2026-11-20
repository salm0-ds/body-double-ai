"""Send command SMS via Twilio to the phone, which runs the matching automation (major block, Notion)."""
import asyncio
import logging
import os
from typing import Optional

from twilio.rest import Client

log = logging.getLogger("telegram-agent.twilio")

OPEN_NOTION_INSPO = "open-notion-inspo"
# Sent when time on an app goes over the limit; the phone's Shortcut for this text does the blocking
MAJOR_BLOCK = "majorblock"


def _send_sms(body: str) -> str:
  client = Client(os.environ["ACCOUNT_SID"], os.environ["AUTH_TOKEN_TWILIO"])
  message = client.messages.create(
      body=body,
      from_=os.environ["TWILIO_NUM"],
      to=os.environ["RECEIVING_NUM"],
  )
  return message.sid


async def send_command(body: str) -> Optional[str]:
  """Send a command SMS. Returns the Twilio message SID, or None if it failed (never raises)."""
  try:
    sid = await asyncio.to_thread(_send_sms, body)  # Twilio client is blocking
  except KeyError as e:
    log.error("Twilio env var %s is not set, can't send %r", e, body)
    return None
  except Exception:
    log.exception("Twilio send failed for %r", body)
    return None
  log.info("Twilio sent %r (sid %s)", body, sid)
  return sid
