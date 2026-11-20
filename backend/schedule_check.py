"""Cron-driven accountability check: current schedule block + app activity -> nudge / lock."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import AsyncClient

from messaging import notify_me
from twilio_actions import MAJOR_BLOCK, OPEN_NOTION_INSPO, send_command

log = logging.getLogger("telegram-agent.schedule_check")

# Thresholds (minutes on an app)
FREE_TIME_GRACE_MIN = 5      # no block: ignore short sessions
FREE_TIME_LOCK_MIN = 15      # no block: lock after this long
BLOCK_LOCK_MIN = 2           # during a block: lock after this long
MAJOR_BLOCK_RESEND_MIN = 10  # app still open this long after majorblock: resend it once

# An "opened" row older than this with no "closed" is treated as a missed close event,
# so one dropped Shortcut doesn't leave an app "open" forever.
STALE_OPEN_HOURS = 2


def _parse_ts(value: str) -> datetime:
  """Parse a Supabase timestamp; naive values are assumed to be UTC."""
  ts = datetime.fromisoformat(value)
  if ts.tzinfo is None:
    ts = ts.replace(tzinfo=timezone.utc)
  return ts


async def get_active_schedule_block(db: AsyncClient):
  """Return the schedule_blocks row covering right now, or None."""
  now_utc = datetime.now(timezone.utc).isoformat()
  response = (
      await db.table("schedule_blocks")
      .select("*")
      .lte("start_time", now_utc)
      .gte("end_time", now_utc)
      .order("start_time", desc=True)
      .limit(1)
      .execute()
  )
  return response.data[0] if response.data else None


async def get_open_apps(db: AsyncClient) -> list[dict]:
  """
  Return apps whose latest event is "opened", as [{"app_name", "opened_at", "minutes_open"}].
  Looks at each app's latest row separately, so opening Chrome doesn't hide that YouTube is still open.
  """
  now = datetime.now(timezone.utc)
  cutoff = (now - timedelta(hours=STALE_OPEN_HOURS)).isoformat()
  response = (
      await db.table("app_activity_logs")
      .select("app_name, event_type, timestamp")
      .gte("timestamp", cutoff)
      .order("timestamp", desc=True)
      .limit(200)
      .execute()
  )

  latest_per_app: dict[str, dict] = {}
  for row in response.data or []:
    latest_per_app.setdefault(row["app_name"], row)  # rows are newest-first

  open_apps = []
  for app_name, row in latest_per_app.items():
    if row["event_type"] != "opened":
      continue
    opened_at = _parse_ts(row["timestamp"])
    open_apps.append({
        "app_name": app_name,
        "opened_at": opened_at,
        "minutes_open": (now - opened_at).total_seconds() / 60,
    })
  return open_apps


async def log_action(db: AsyncClient, *, triggered_at: datetime, action_taken: str, stage: int,
                     reason: str, app: dict, block: Optional[dict], message_id: Optional[str] = None,
                     meta: Optional[dict] = None):
  """Append one row to assistant_actions. Failures are logged, never raised."""
  row = {
      "triggered_at": triggered_at.isoformat(),
      "acted_at": datetime.now(timezone.utc).isoformat(),
      "trigger_type": "cron",
      "trigger_name": "scheduled_check",
      "trigger_data": {
          "app": app["app_name"],
          "open_minutes": round(app["minutes_open"], 1),
          "block_title": block["title"] if block else None,
      },
      "action_taken": action_taken,
      "escalation_stage": stage,
      "reason": reason,
      "related_app": app["app_name"],
      "schedule_block_id": block["id"] if block else None,
      "message_id": message_id,
      "action_meta": meta or {},
  }
  try:
    await db.table("assistant_actions").insert(row).execute()
  except Exception:
    log.exception("Logging %s to assistant_actions failed", action_taken)


async def get_actions_this_session(db: AsyncClient, app: dict) -> list[dict]:
  """
  Actions already taken for this app since it was opened, oldest first.
  This is the check's memory: each action fires once per app session, and closing
  and reopening the app starts a new session. On a query failure returns [] (acts rather than stays silent).
  """
  try:
    response = (
        await db.table("assistant_actions")
        .select("action_taken, triggered_at")
        .eq("related_app", app["app_name"])
        .gte("triggered_at", app["opened_at"].isoformat())
        .order("triggered_at")
        .execute()
    )
    return response.data or []
  except Exception:
    log.exception("Loading past actions for %s failed", app["app_name"])
    return []


def _twilio_meta(command: str, sid: Optional[str]) -> dict:
  return {"twilio_command": command, "twilio_sid": sid, "twilio_ok": sid is not None}


async def run_schedule_check(db: AsyncClient) -> dict:
  """Decide what to do for each open app given the current schedule block."""
  triggered_at = datetime.now(timezone.utc)
  block = await get_active_schedule_block(db)
  open_apps = await get_open_apps(db)
  actions = []

  for app in open_apps:
    name, mins = app["app_name"], app["minutes_open"]
    m = int(mins)
    title = block["title"] if block else None
    block_id = block["id"] if block else None

    async def tell(text, message_type):
      return await notify_me(text, message_type, related_app=name, schedule_block_id=block_id)

    async def record(action_taken, stage, reason, message_id, meta=None):
      await log_action(db, triggered_at=triggered_at, action_taken=action_taken, stage=stage,
                       reason=reason, app=app, block=block, message_id=message_id, meta=meta)

    if block is None and mins < FREE_TIME_GRACE_MIN:
      action = "none"
      log.info("Schedule check: %s open %.1f min, block=%s -> %s", name, mins, title, action)
      actions.append({"app_name": name, "minutes_open": round(mins, 1), "action": action})
      continue

    # Memory: what has already been done for this app since it was opened
    past = await get_actions_this_session(db, app)
    done = {row["action_taken"] for row in past}

    if block is None and mins < FREE_TIME_LOCK_MIN:
      if "sent_nudge_open_notion" in done:
        action = "skip_already_nudged"
      else:
        action = "nudge_and_notion"
        reason = f"{m} min on {name}, nothing scheduled"
        msg_id = await tell(f"You've been on {name} for {m} min. Nothing's scheduled, but don't let it drag on.", "nudge")
        sid = await send_command(OPEN_NOTION_INSPO)
        await record("sent_nudge_open_notion", 2, reason, msg_id, _twilio_meta(OPEN_NOTION_INSPO, sid))

    elif block is not None and mins < BLOCK_LOCK_MIN:
      if "sent_nudge" in done:
        action = "skip_already_nudged"
      else:
        action = "nudge"
        msg_id = await tell(f"You're meant to be on \"{title}\" right now. Close {name} and get back to it.", "nudge")
        await record("sent_nudge", 1, f"{name} opened during \"{title}\"", msg_id)

    else:
      # Over the limit (block or not): Telegram first, then one "majorblock" SMS for the phone to act on.
      # Sent once per session, plus one resend if the app is still open MAJOR_BLOCK_RESEND_MIN later.
      reason = f"{m} min on {name} during \"{title}\"" if block else f"{m} min on {name}, nothing scheduled"
      first_block = next((row for row in past if row["action_taken"] == "major_block"), None)

      if first_block is None:
        action = "lock"
        msg_id = await tell(f"🔒 Lock time: {reason}. Close {name} now.", "lock_alert")
        sid = await send_command(MAJOR_BLOCK)
        await record("major_block", 3, reason, msg_id, _twilio_meta(MAJOR_BLOCK, sid))

      elif "major_block_resend" in done:
        action = "skip_already_resent"

      elif (triggered_at - _parse_ts(first_block["triggered_at"])) >= timedelta(minutes=MAJOR_BLOCK_RESEND_MIN):
        action = "lock_resend"
        msg_id = await tell(f"🔒 Still on {name} ({m} min) after the block. Sending it again. Close {name} now.", "lock_alert")
        sid = await send_command(MAJOR_BLOCK)
        await record("major_block_resend", 3, reason, msg_id, _twilio_meta(MAJOR_BLOCK, sid))

      else:
        action = "skip_already_locked"

    log.info("Schedule check: %s open %.1f min, block=%s -> %s", name, mins, title, action)
    actions.append({"app_name": name, "minutes_open": round(mins, 1), "action": action})

  return {"block": block["title"] if block else None, "actions": actions}
