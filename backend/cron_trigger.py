"""Calls the deployed app's /trigger-scheduled-check endpoint.

Run on a schedule (GitHub Actions, Render Cron Job, etc.). Needs two env vars:
  APP_URL      e.g. https://your-app.onrender.com
  CRON_SECRET  same value as on the Render web service

Exits non-zero on failure so the scheduler marks the run as failed.
"""
import logging
import os
import sys
import time

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("cron_trigger")

MAX_ATTEMPTS = 3
# Render's free tier sleeps idle apps; the first request can take ~1 minute.
TIMEOUT_SECONDS = 90


def main() -> int:
    app_url = os.getenv("APP_URL", "").rstrip("/")
    secret = os.getenv("CRON_SECRET")
    if not app_url or not secret:
        log.error("APP_URL and CRON_SECRET must both be set")
        return 1

    # Wake the app first so the real call doesn't time out on a cold start.
    try:
        requests.get(f"{app_url}/health", timeout=TIMEOUT_SECONDS)
    except requests.RequestException as e:
        log.warning("Wake-up ping failed (will still try the check): %s", e)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                f"{app_url}/trigger-scheduled-check",
                headers={"Authorization": f"Bearer {secret}"},
                timeout=TIMEOUT_SECONDS,
            )
            if response.status_code == 401:
                log.error("Unauthorized: CRON_SECRET doesn't match the app's")
                return 1  # retrying won't help
            response.raise_for_status()
            log.info("Success: %s", response.json())
            return 0
        except requests.RequestException as e:
            log.warning("Attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, e)
            if attempt < MAX_ATTEMPTS:
                time.sleep(10 * attempt)

    log.error("All %d attempts failed", MAX_ATTEMPTS)
    return 1


if __name__ == "__main__":
    sys.exit(main())
