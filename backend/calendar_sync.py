"""Shared helpers for reading/writing a day's schedule_blocks and syncing it from Google Calendar."""
import asyncio
import logging
from datetime import date, datetime, timezone

from database_conn import create_db_conn
from google_cal import day_bounds, get_events_for_date

log = logging.getLogger("telegram-agent.calendar_sync")


async def get_blocks_for_date(day: date) -> list[dict]:
  """All schedule_blocks rows that start on the given (local) day, ordered by start time."""
  start, end = day_bounds(day)
  db = await create_db_conn()
  response = (
      await db.table("schedule_blocks")
      .select("title, start_time, end_time, google_event_id")
      .gte("start_time", start.isoformat())
      .lte("start_time", end.isoformat())
      .order("start_time")
      .execute()
  )
  return response.data or []


def scheduled_hours(blocks: list[dict]) -> float:
  """Total hours across blocks (overlaps are not merged — good enough for a threshold)."""
  total = 0.0
  for b in blocks:
    start = datetime.fromisoformat(b["start_time"])
    end = datetime.fromisoformat(b["end_time"])
    total += max((end - start).total_seconds(), 0) / 3600
  return total


async def save_blocks(rows: list[dict]):
  """Upsert schedule_blocks rows in one call (keyed on google_event_id)."""
  if not rows:
    return
  now = datetime.now(timezone.utc).isoformat()
  for row in rows:
    row["updated_at"] = now
  db = await create_db_conn()
  await db.table("schedule_blocks").upsert(rows, on_conflict="google_event_id").execute()


async def sync_day_from_google(day: date) -> dict:
  """
  Make Supabase match Google Calendar for one day: upsert every Google event
  (catches new + edited events) and delete rows for that day that are no longer in Google.
  """
  rows = await asyncio.to_thread(get_events_for_date, day)  # Google client is blocking
  log.info("Fetched %d Google events for %s", len(rows), day)
  await save_blocks(rows)

  start, end = day_bounds(day)
  db = await create_db_conn()
  query = (
      db.table("schedule_blocks")
      .delete()
      .gte("start_time", start.isoformat())
      .lte("start_time", end.isoformat())
  )
  ids = [r["google_event_id"] for r in rows]
  if ids:
    query = query.not_.in_("google_event_id", ids)
  deleted = (await query.execute()).data or []

  log.info("Synced %s: %d upserted, %d removed", day, len(rows), len(deleted))
  return {"date": str(day), "upserted": len(rows), "removed": len(deleted), "events": rows}
