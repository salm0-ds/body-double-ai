import os
from datetime import datetime, timezone
from typing import Annotated
from openai import AsyncOpenAI
from pydantic import AfterValidator, BaseModel, Field

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT=os.getenv("SYSTEM_PROMPT")

# iPhone lock-screen banners show roughly 2-3 lines (~150 chars) before cutting off
NUDGE_MAX_CHARS = 120


def _cap_length(text: str) -> str:
  """Hard cap: trims to NUDGE_MAX_CHARS at a word boundary if the model overshoots."""
  text = text.strip()
  if len(text) > NUDGE_MAX_CHARS:
    text = text[:NUDGE_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
  return text


class YoutubeNudge(BaseModel):
  message: Annotated[
      str,
      Field(description=(
          f"One short sentence, max {NUDGE_MAX_CHARS} characters. "
          "No markdown, no greeting.")),
      AfterValidator(_cap_length),
  ]


async def generate_body_double_nudge(app_name: str = "YouTube") -> str:
  """Calls OpenAI to generate a short accountability message that fits a phone notification."""

  current_time = datetime.now(timezone.utc).strftime("%H:%M UTC")

  response = await openai_client.responses.parse(
      model="gpt-5-mini",
      instructions=SYSTEM_PROMPT,
      reasoning={"effort": "minimal"},
      input=f"Time is: {current_time}. User opened {app_name}",
      text_format=YoutubeNudge,
    )
  return response.output_parsed.message