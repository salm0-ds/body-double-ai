import datetime
import json
import logging
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
log = logging.getLogger("telegram-agent.google_cal")


def get_calendar_service():
    creds = None
    token_env = os.getenv("GOOGLE_TOKEN_JSON")
    if token_env:
        # On the server (Render): paste the contents of token.json into this env var
        log.info("Loading Google credentials from GOOGLE_TOKEN_JSON")
        creds = Credentials.from_authorized_user_info(json.loads(token_env), SCOPES)
    elif os.path.exists("token.json"):
        # Locally: use token.json to avoid logging in in the browser every time
        log.info("Loading Google credentials from token.json")
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    else:
        log.warning("No GOOGLE_TOKEN_JSON env var and no token.json in %s", os.getcwd())

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Google token expired, refreshing")
            creds.refresh(Request())
        elif os.getenv("RENDER"):
            # Browser login can't work on a server - fail with a clear message instead
            raise RuntimeError(
                "No valid Google credentials on the server. Set GOOGLE_TOKEN_JSON "
                "to the contents of a token.json created by logging in locally."
            )
        else:
            # 1. Fetch credentials from environment variables
            client_id = os.getenv("GOOGLE_CLIENT_ID")
            client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

            if not client_id or not client_secret:
                raise ValueError(
                    "Missing environment variables: GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET"
                )

            # 2. Build the client configuration dictionary dynamically
            client_config = {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }

            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token for future runs (only locally, the server uses the env var)
        if not token_env:
            with open("token.json", "w") as token:
                token.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def get_today_events():
    """Return today's timed events as rows ready for the schedule_blocks table."""
    service = get_calendar_service()

    # Time bounds for today, in this machine's local time zone
    today = datetime.date.today()
    start = datetime.datetime.combine(today, datetime.time.min).astimezone().isoformat()
    end = datetime.datetime.combine(today, datetime.time.max).astimezone().isoformat()

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    rows = []
    for event in events_result.get("items", []):
        # All-day events only have "date", not "dateTime" - skip them
        if "dateTime" not in event["start"]:
            continue
        rows.append({
            "google_event_id": event["id"],
            "title": event.get("summary", "(no title)"),
            "start_time": event["start"]["dateTime"],
            "end_time": event["end"]["dateTime"],
        })
    return rows


def main():
    try:
        rows = get_today_events()
        print(f"\n--- Schedule for {datetime.date.today()} ---")
        if not rows:
            print("No events scheduled for today.")
        for row in rows:
            print(f"[{row['start_time'].split('T')[-1][:5]}] {row['title']}")
    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
