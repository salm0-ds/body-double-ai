"""Cron-driven accountability check: current schedule block + app activity -> nudge / lock."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import AsyncClient

from messaging import notify_me
from twilio_actions import LOCK_PHONE, OPEN_NOTION_INSPO, START_OPAL, send_command

log = logging.getLogger("telegram-agent.schedule_check")

# Thresholds (minutes on an app)
FREE_TIME_GRACE_MIN = 5      # no block: ignore short sessions
FREE_TIME_LOCK_MIN = 30      # no block: lock after this long
BLOCK_LOCK_MIN = 5           # during a block: lock after this long

# An "opened" row older than this with no "closed" is treated as a missed close event,
# so one dropped Shortcut doesn't leave an app "open" forever.
STALE_OPEN_HOURS = 3


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

    elif block is None and mins < FREE_TIME_LOCK_MIN:
      action = "nudge_and_notion"
      reason = f"{m} min on {name}, nothing scheduled"
      msg_id = await tell(f"You've been on {name} for {m} min. Nothing's scheduled, but don't let it drag on.", "nudge")
      sid = await send_command(OPEN_NOTION_INSPO)
      await record("sent_nudge_open_notion", 2, reason, msg_id, _twilio_meta(OPEN_NOTION_INSPO, sid))

    elif block is not None and mins < BLOCK_LOCK_MIN:
      action = "nudge"
      msg_id = await tell(f"You're meant to be on \"{title}\" right now. Close {name} and get back to it.", "nudge")
      await record("sent_nudge", 1, f"{name} opened during \"{title}\"", msg_id)

    else:
      # Same lock whether or not a block is running: Telegram first (before the phone locks), then Opal, then lock
      action = "lock"
      reason = f"{m} min on {name} during \"{title}\"" if block else f"{m} min on {name}, nothing scheduled"
      msg_id = await tell(f"🔒 Lock time: {reason}. Close {name} now.", "lock_alert")
      for command, action_taken in ((START_OPAL, "started_opal"), (LOCK_PHONE, "locked_phone")):
        sid = await send_command(command)
        await record(action_taken, 3, reason, msg_id, _twilio_meta(command, sid))

    log.info("Schedule check: %s open %.1f min, block=%s -> %s", name, mins, title, action)
    actions.append({"app_name": name, "minutes_open": round(mins, 1), "action": action})

  return {"block": block["title"] if block else None, "actions": actions}
