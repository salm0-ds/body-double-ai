"""Telegram chat agent: OpenAI decides whether to call tools (e.g. sync Google Calendar -> Supabase)."""
import json
import logging
from datetime import date, timedelta
from typing import Optional

from calendar_sync import sync_day_from_google
from llm_model import openai_client
from todoist_input import fetch_tasks

log = logging.getLogger("telegram-agent.agent")

AGENT_PROMPT = (
    "You are a concise accountability assistant chatting over Telegram. "
    "If the user says they changed, updated or edited their Google Calendar, call "
    "sync_google_calendar for the day they mean (default 'today'), then confirm briefly. "
    "If the user asks for help with, sorting or prioritising their tasks, call get_todoist_tasks "
    "and work from that list (it is their p1 tasks only). "
    "Otherwise just reply in one or two short sentences. "
    "Earlier messages are included for context; their [HH:MM UTC] prefixes are timestamps, never write them yourself."
)

TOOLS = [{
    "type": "function",
    "name": "sync_google_calendar",
    "description": "Copy the user's Google Calendar events for a day into the schedule database "
                   "(adds new, updates edited, removes deleted events).",
    "parameters": {
        "type": "object",
        "properties": {
            "day": {"type": "string", "enum": ["today", "tomorrow"]},
        },
        "required": ["day"],
        "additionalProperties": False,
    },
    "strict": True,
}, {
    "type": "function",
    "name": "get_todoist_tasks",
    "description": "Get the user's current top-priority (p1) Todoist tasks, with due dates and durations.",
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    "strict": True,
}]


async def _run_tool(name: str, arguments: str) -> dict:
  try:
    args = json.loads(arguments)
    if name == "sync_google_calendar":
      day = date.today() + timedelta(days=1 if args["day"] == "tomorrow" else 0)
      result = await sync_day_from_google(day)
      result.pop("events", None)  # the LLM only needs the counts
      return result
    if name == "get_todoist_tasks":
      return {"tasks": await fetch_tasks()}
    return {"error": f"Unknown tool {name}"}
  except Exception as e:
    log.exception("Tool %s failed", name)
    return {"error": str(e)}


async def handle_chat_message(text: str, history: Optional[list[dict]] = None) -> str:
  """
  Run one user message through the LLM, executing any tool calls. Returns the reply text.
  `history` is recent messages (oldest first) from messaging.get_recent_messages, not including `text`.
  """
  response = await openai_client.responses.create(
      model="gpt-5-mini",
      instructions=AGENT_PROMPT,
      input=(history or []) + [{"role": "user", "content": text}],
      tools=TOOLS,
  )

  calls = [item for item in response.output if item.type == "function_call"]
  if not calls:
    return response.output_text

  outputs = []
  for call in calls:
    log.info("Agent calling tool %s(%s)", call.name, call.arguments)
    result = await _run_tool(call.name, call.arguments)
    outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result)})

  # Send the tool results back so the model can write the final reply
  follow_up = await openai_client.responses.create(
      model="gpt-5-mini",
      instructions=AGENT_PROMPT,
      previous_response_id=response.id,
      input=outputs,
      tools=TOOLS,
  )
  return follow_up.output_text
