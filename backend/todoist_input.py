import os
import logging

from todoist_api_python.api_async import TodoistAPIAsync

log = logging.getLogger("telegram-agent.todoist")


async def fetch_tasks() -> list[dict]:
  """Return all active Todoist tasks as plain dicts. Returns [] (and logs) on failure."""
  token = os.getenv("TODOIST_API_TOKEN")
  if not token:
    log.error("TODOIST_API_TOKEN is not set, planning without Todoist tasks")
    return []

  tasks = []
  try:
    async with TodoistAPIAsync(token) as api:
      pages = await api.get_tasks()  # coroutine that returns an async iterator of pages
      async for page in pages:
        for t in page:
          tasks.append({
              "id": str(t.id),
              "content": t.content,
              "priority": t.priority,  # 4 = highest (p1) in the API, 1 = lowest
              "due_date": str(t.due.date) if t.due else None,
              "duration_min": t.duration.amount if t.duration and t.duration.unit == "minute" else None,
          })
  except Exception:
    log.exception("Fetching Todoist tasks failed, planning without them")
    return []

  log.info("Fetched %d Todoist tasks", len(tasks))
  return tasks
