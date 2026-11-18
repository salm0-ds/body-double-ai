"""Cron-driven accountability check: current schedule block + app activity -> nudge / lock."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from supabase import AsyncClient

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


async def trigger_lock(app_name: str, reason: str, send: Callable[[str], None]):
  """Placeholder for the real lock function — just messages for now."""
  log.info("LOCK placeholder for %s: %s", app_name, reason)
  send(f"🔒 Lock time: {reason} Close {app_name} now.")


async def run_schedule_check(db: AsyncClient, send: Callable[[str], None]) -> dict:
  """Decide what to do for each open app given the current schedule block."""
  block = await get_active_schedule_block(db)
  open_apps = await get_open_apps(db)
  actions = []

  for app in open_apps:
    name, mins = app["app_name"], app["minutes_open"]
    m = int(mins)

    if block is None:
      if mins < FREE_TIME_GRACE_MIN:
        action = "none"
      elif mins < FREE_TIME_LOCK_MIN:
        action = "nudge"
        send(f"You've been on {name} for {m} min. Nothing's scheduled, but don't let it drag on.")
      else:
        action = "lock"
        await trigger_lock(name, f"{m} min on {name} with nothing scheduled.", send)
    else:
      title = block["title"]
      if mins < BLOCK_LOCK_MIN:
        action = "nudge"
        send(f"You're meant to be on \"{title}\" right now. Close {name} and get back to it.")
      else:
        action = "lock"
        await trigger_lock(name, f"{m} min on {name} during \"{title}\".", send)

    log.info("Schedule check: %s open %.1f min, block=%s -> %s",
             name, mins, block["title"] if block else None, action)
    actions.append({"app_name": name, "minutes_open": round(mins, 1), "action": action})

  return {"block": block["title"] if block else None, "actions": actions}
