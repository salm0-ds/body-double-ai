"""Nightly (~10pm) planner: fills tomorrow's calendar from Todoist + context, via an LLM."""
import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from pydantic import BaseModel, Field

from calendar_sync import get_blocks_for_date, save_blocks, scheduled_hours
from google_cal import day_bounds, insert_events
from llm_model import openai_client
from todoist_input import fetch_tasks

log = logging.getLogger("telegram-agent.planner")

FULL_DAY_HOURS = 7


class PlannedEvent(BaseModel):
  title: str = Field(description="Title of the calendar block, e.g. 'Deep Work: Build MVP'")
  start_time: datetime = Field(description="Start time, ISO 8601 with timezone offset")
  end_time: datetime = Field(description="End time, ISO 8601 with timezone offset")
  description: Optional[str] = Field(description="Short goal for the block, or null")


class DailySchedulePlan(BaseModel):
  events: list[PlannedEvent] = Field(description="Only the NEW blocks to add for tomorrow")


PLANNER_PROMPT = """You plan tomorrow's schedule as time blocks.
Rules:
- Only output NEW blocks for tomorrow ({tomorrow}). Never repeat or overlap the existing blocks.
- Use the timezone offset {offset} on every start_time and end_time.
- Every block starts exactly on the hour (e.g. 08:00, 13:00), never at :15, :30 or :45.
- Work blocks are 1 to 3 whole hours long, placed back to back with no breaks (the user adds breaks).
- The first block of the day starts at 08:00 (unless an existing block already covers 08:00). No block ends after 22:00.
- Include one "Gym" block of exactly 1.5 hours, starting on the hour between 10:00 and 14:00 so it ends by 16:00,
  unless the existing blocks already contain a gym session. The block after the gym starts at the next full hour.
- Prioritise Todoist tasks that are overdue or due tomorrow, then by priority (4 = most urgent).
- Aim for about {target_hours:.1f} more hours of scheduled work."""



def _valid_new_events(plan: DailySchedulePlan, tomorrow: date, existing: list[dict]) -> list[dict]:
  """Drop events the LLM got wrong (wrong day, end before start, overlapping existing/each other)."""
  day_start, day_end = day_bounds(tomorrow)
  taken = [(datetime.fromisoformat(b["start_time"]), datetime.fromisoformat(b["end_time"])) for b in existing]

  good = []
  for ev in sorted(plan.events, key=lambda e: e.start_time):
    s, e = ev.start_time, ev.end_time
    if s.tzinfo is None or e.tzinfo is None:
      s, e = s.replace(tzinfo=day_start.tzinfo), e.replace(tzinfo=day_start.tzinfo)
    if not (day_start <= s < e <= day_end):
      log.warning("Dropping planned event outside tomorrow or end<=start: %s", ev)
      continue
    if any(s < te and ts < e for ts, te in taken):
      log.warning("Dropping planned event that overlaps another block: %s", ev)
      continue
    taken.append((s, e))
    good.append({
        "title": ev.title,
        "start_time": s.isoformat(),
        "end_time": e.isoformat(),
        "description": ev.description,
    })
  return good


async def run_nightly_plan(send: Callable[[str], None]) -> dict:
  today = date.today()
  tomorrow = today + timedelta(days=1)

  tomorrow_blocks = await get_blocks_for_date(tomorrow)
  hours = scheduled_hours(tomorrow_blocks)
  log.info("Tomorrow (%s) has %d blocks, %.1f hours", tomorrow, len(tomorrow_blocks), hours)

  if hours >= FULL_DAY_HOURS:
    send(f"Tomorrow already has {hours:.1f}h scheduled across {len(tomorrow_blocks)} blocks. Leaving it as is.")
    return {"status": "skipped", "reason": "already full", "hours": round(hours, 1)}

  # Empty -> use today's calendar as context. Partly filled -> use tomorrow's existing blocks.
  if not tomorrow_blocks:
    context_label, context_blocks = "Today's schedule (for context on what you're working on)", await get_blocks_for_date(today)
  else:
    context_label, context_blocks = "Tomorrow's existing blocks (do NOT overlap these)", tomorrow_blocks
  tasks = await fetch_tasks()

  offset = day_bounds(tomorrow)[0].strftime("%z")
  offset = f"{offset[:3]}:{offset[3:]}"
  instructions = PLANNER_PROMPT.format(tomorrow=tomorrow, offset=offset, target_hours=FULL_DAY_HOURS - hours)
  user_input = json.dumps({
      "today": str(today),
      "tomorrow": str(tomorrow),
      context_label: context_blocks,
      "todoist_tasks": tasks,
  }, default=str)

  try:
    response = await openai_client.responses.parse(
        model="gpt-5-mini",
        instructions=instructions,
        input=user_input,
        text_format=DailySchedulePlan,
    )
    plan = response.output_parsed
  except Exception:
    log.exception("Planner LLM call failed")
    send("⚠️ Couldn't plan tomorrow: the AI planner failed. Check the logs.")
    return {"status": "error", "reason": "llm failed"}

  events = _valid_new_events(plan, tomorrow, tomorrow_blocks)
  log.info("LLM proposed %d events, %d valid", len(plan.events), len(events))
  if not events:
    send("Planner ran but had nothing valid to add for tomorrow.")
    return {"status": "success", "added": 0}

  # Google first (one-by-one), so each Supabase row carries its google_event_id.
  rows = await asyncio.to_thread(insert_events, events)
  try:
    await save_blocks(rows)
  except Exception:
    log.exception("Saving planned blocks to Supabase failed (they ARE in Google Calendar)")
    send("⚠️ Added blocks to Google Calendar but saving to Supabase failed. Message me to resync tomorrow.")
    return {"status": "error", "reason": "supabase failed", "added_to_google": len(rows)}

  lines = "\n".join(
      f"{datetime.fromisoformat(r['start_time']):%H:%M}–{datetime.fromisoformat(r['end_time']):%H:%M} {r['title']}"
      for r in rows
  )
  failed = len(events) - len(rows)
  note = f"\n({failed} block(s) failed to add, see logs)" if failed else ""
  send(f"📅 Added {len(rows)} blocks for tomorrow:\n{lines}{note}")
  return {"status": "success", "added": len(rows), "failed": failed}
