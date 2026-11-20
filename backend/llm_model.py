import os
from datetime import datetime, timezone
from typing import Annotated, Optional
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


async def generate_body_double_nudge(app_name: str = "YouTube", history: Optional[list[dict]] = None) -> str:
  """
  Calls OpenAI to generate a short accountability message that fits a phone notification.
  `history` is recent chat messages (oldest first) so the nudge can build on the conversation.
  """

  current_time = datetime.now(timezone.utc).strftime("%H:%M UTC")

  response = await openai_client.responses.parse(
      model="gpt-5-mini",
      instructions=SYSTEM_PROMPT,
      reasoning={"effort": "minimal"},
      input=(history or []) + [{
          "role": "developer",
          "content": f"Time is: {current_time}. User just opened {app_name}. Write the nudge. "
                     "Earlier messages are context only; never include their [HH:MM UTC] prefixes.",
      }],
      text_format=YoutubeNudge,
    )
  return response.output_parsed.message