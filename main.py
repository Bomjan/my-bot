import asyncio
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from playwright.async_api import async_playwright

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

VLE_URL = "https://vle.gcit.edu.bt"
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
OUTPUT_ICS = Path(__file__).parent / "gcit_events.ics"
GOOGLE_CREDS_FILE = Path(__file__).parent / "google_credentials.json"
TOKEN_FILE = Path(__file__).parent / "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def strip_html(html: str) -> str:
    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, data):
            self.parts.append(data)
    s = _Stripper()
    s.feed(html)
    return " ".join(s.parts).strip()


def load_credentials():
    if not CREDENTIALS_FILE.exists():
        print("credentials.json not found. Creating template...")
        template = {"username": "your_username", "password": "your_password"}
        CREDENTIALS_FILE.write_text(json.dumps(template, indent=2))
        print(f"Edit {CREDENTIALS_FILE} with your VLE credentials and re-run.")
        exit(1)
    creds = json.loads(CREDENTIALS_FILE.read_text())
    if creds["username"] == "your_username":
        print("Please update credentials.json with your actual VLE credentials.")
        exit(1)
    return creds


def get_calendar_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not GOOGLE_CREDS_FILE.exists():
                print("\ngoogle_credentials.json not found.")
                print("Follow SETUP.md to create it, then re-run.")
                exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def sync_to_google_calendar(events: list[dict]):
    service = get_calendar_service()

    print("\nSyncing to Google Calendar...")
    added = 0
    updated = 0

    for ev in events:
        uid = f"vle-gcit-{ev.get('id', abs(hash(ev['title'])))}"
        start = ev["start"].isoformat()
        end = ev["end"].isoformat()

        description = strip_html(ev.get("description", ""))
        if ev.get("url"):
            description += f"\n\n{ev['url']}"

        gcal_event = {
            "summary": ev["title"],
            "description": description.strip(),
            "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
            "iCalUID": uid + "@vle.gcit.edu.bt",
            "source": {"title": "GCIT VLE", "url": ev.get("url", VLE_URL)},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 120}],
            },
        }

        # Check if event already exists by iCalUID
        existing = service.events().list(
            calendarId="primary",
            iCalUID=uid + "@vle.gcit.edu.bt",
        ).execute().get("items", [])

        if existing:
            service.events().update(
                calendarId="primary",
                eventId=existing[0]["id"],
                body=gcal_event,
            ).execute()
            updated += 1
        else:
            service.events().insert(
                calendarId="primary",
                body=gcal_event,
            ).execute()
            added += 1

    print(f"  {added} event(s) added, {updated} event(s) updated.")


def to_ics_datetime(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build_ics(events: list[dict]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GCIT VLE Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for ev in events:
        uid = f"{ev.get('id', id(ev))}@vle.gcit.edu.bt"
        start = ev.get("start")
        end = ev.get("end", start)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{ev.get('title', 'No title')}",
            f"DTSTART:{to_ics_datetime(start)}",
            f"DTEND:{to_ics_datetime(end)}",
            f"DESCRIPTION:{ev.get('description', '')}",
            f"URL:{ev.get('url', '')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


async def fetch_events(username: str, password: str) -> list[dict]:
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print("Navigating to VLE login page...")
        await page.goto(f"{VLE_URL}/login/index.php", wait_until="networkidle")

        print("Logging in...")
        await page.fill("#username", username)
        await page.fill("#password", password)
        await page.click("#loginbtn")
        await page.wait_for_load_state("networkidle")

        if "login" in page.url:
            print("Login failed. Check your credentials in credentials.json.")
            await browser.close()
            return []

        print("Logged in successfully. Fetching calendar events...")

        now = datetime.now(timezone.utc)
        timestamp = int(now.timestamp())
        api_url = f"{VLE_URL}/lib/ajax/service.php?sesskey="

        sesskey = await page.evaluate(
            "() => window.M && window.M.cfg ? window.M.cfg.sesskey : null"
        )

        if not sesskey:
            content = await page.content()
            match = re.search(r'"sesskey":"([^"]+)"', content)
            sesskey = match.group(1) if match else None

        if sesskey:
            api_url += sesskey
            payload = [
                {
                    "index": 0,
                    "methodname": "core_calendar_get_calendar_upcoming_view",
                    "args": {"courseid": 0, "categoryid": 0},
                }
            ]
            response = await page.evaluate(
                """async ([url, body]) => {
                    const res = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(body)
                    });
                    return res.json();
                }""",
                [api_url, payload],
            )

            raw_events = (
                response[0].get("data", {}).get("events", [])
                if response and isinstance(response, list)
                else []
            )

            for ev in raw_events:
                start_ts = ev.get("timesort") or ev.get("timestart", timestamp)
                duration = ev.get("timeduration", 3600)
                start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
                end_dt = datetime.fromtimestamp(start_ts + duration, tz=timezone.utc)
                events.append(
                    {
                        "id": ev.get("id"),
                        "title": ev.get("name", "Untitled"),
                        "description": ev.get("description", ""),
                        "start": start_dt,
                        "end": end_dt,
                        "url": ev.get("url", VLE_URL),
                    }
                )
        else:
            print("Could not retrieve session key. Falling back to calendar page scrape...")
            await page.goto(f"{VLE_URL}/calendar/view.php?view=upcoming", wait_until="networkidle")
            event_els = await page.query_selector_all(".event")
            for el in event_els:
                title_el = await el.query_selector(".referer")
                title = await title_el.inner_text() if title_el else "Untitled"
                time_el = await el.query_selector(".date")
                time_text = await time_el.inner_text() if time_el else ""
                events.append(
                    {
                        "title": title.strip(),
                        "description": time_text.strip(),
                        "start": now,
                        "end": now,
                        "url": VLE_URL,
                    }
                )

        await browser.close()
    return events


async def main():
    creds = load_credentials()
    events = await fetch_events(creds["username"], creds["password"])

    if not events:
        print("No upcoming events found.")
        return

    print(f"\nFound {len(events)} upcoming event(s):")
    for ev in events:
        print(f"  - {ev['title']} | {ev['start'].strftime('%Y-%m-%d %H:%M UTC')}")

    # Save .ics backup
    ics_content = build_ics(events)
    OUTPUT_ICS.write_text(ics_content)
    print(f"\nCalendar backup saved to: {OUTPUT_ICS}")

    # Sync to Google Calendar
    sync_to_google_calendar(events)
    print("\nDone! Your Google Calendar is up to date.")
    print("Make sure Google Calendar is synced on your phone to see the events.")


if __name__ == "__main__":
    asyncio.run(main())
