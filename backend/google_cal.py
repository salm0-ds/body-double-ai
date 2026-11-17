import datetime
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def get_calendar_service():
    creds = None
    # Use token.json to avoid logging in in the browser every time
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
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

        # Save token for future runs
        with open("token.json", "w") as token:
            token.write(creds.to_json()) 

    return build("calendar", "v3", credentials=creds)


def main():
    try:
        service = get_calendar_service()

        # Get time bounds for today
        today = datetime.date.today()
        start = (
            datetime.datetime.combine(today, datetime.time.min)
            .astimezone()
            .isoformat()
        )
        end = (
            datetime.datetime.combine(today, datetime.time.max)
            .astimezone()
            .isoformat()
        )

        # Query calendar
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

        events = events_result.get("items", [])

        print(f"\n--- Schedule for {today} ---")
        if not events:
            print("No events scheduled for today.")
            return

        for event in events:
            time_part = event["start"].get("dateTime", "All Day").split("T")[-1][:5]
            print(f"[{time_part}] {event['summary']}")

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
