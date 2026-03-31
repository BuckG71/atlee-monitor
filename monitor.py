#!/usr/bin/env python3
"""
The Atlee 2BR Availability Monitor

Checks the Knock CRM API for 2-bedroom apartment availability at The Atlee
(San Antonio, TX) every 5 minutes during business hours (7am-9pm CT).
Sends SMS alerts via Gmail SMTP → Verizon email-to-SMS gateway ONLY when
new units appear.

Designed to run as a GitHub Actions scheduled workflow.

Environment variables (set as GitHub Secrets):
    SMTP_EMAIL      - Gmail address used to send SMS
    SMTP_PASSWORD   - Gmail App Password (not your regular password)
    PHONE_NUMBERS   - Comma-separated phone numbers (digits only)
"""

import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

# ─── Configuration ───────────────────────────────────────────────────────────

KNOCK_API_URL = "https://doorway-api.knockrentals.com/v1/property/2017939/units"

STATE_DIR = os.environ.get("STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(STATE_DIR, "last_state.json")
TIMEZONE = ZoneInfo("America/Chicago")
BUSINESS_START = 7   # 7am CT
BUSINESS_END = 21    # 9pm CT
CARRIER_GATEWAY = "@vtext.com"  # Verizon

# From environment / GitHub Secrets
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
PHONE_NUMBERS = [
    n.strip() for n in os.environ.get("PHONE_NUMBERS", "").split(",") if n.strip()
]


# ─── Business Hours ─────────────────────────────────────────────────────────

def is_business_hours():
    now = datetime.now(TIMEZONE)
    return BUSINESS_START <= now.hour < BUSINESS_END


# ─── Fetch Availability ─────────────────────────────────────────────────────

def fetch_2br_units():
    """
    Call the Knock CRM API and return available 2-bedroom units.
    Returns list of dicts with: id, name, bedrooms, bathrooms, area, price, available_on
    """
    resp = requests.get(KNOCK_API_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    units = data.get("units_data", {}).get("units", [])

    available_2br = []
    for u in units:
        if u.get("bedrooms") == 2 and u.get("available") is True:
            available_2br.append({
                "id": u["id"],
                "name": u.get("name", ""),
                "bedrooms": u.get("bedrooms"),
                "bathrooms": u.get("bathrooms"),
                "area": u.get("area"),
                "price": u.get("displayPrice") or u.get("price", ""),
                "available_on": u.get("availableOn", ""),
            })

    return available_2br


# ─── State Management ────────────────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"units": [], "last_check": None, "initialized": False}


def save_state(units):
    state = {
        "units": units,
        "last_check": datetime.now(TIMEZONE).isoformat(),
        "initialized": True,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ─── SMS ─────────────────────────────────────────────────────────────────────

def send_sms(message):
    """Send SMS via Gmail SMTP → Verizon email-to-SMS gateway."""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print(f"  SMS not configured. Would send:\n  {message}")
        return

    if not PHONE_NUMBERS:
        print(f"  No phone numbers configured. Would send:\n  {message}")
        return

    for number in PHONE_NUMBERS:
        to_addr = f"{number}{CARRIER_GATEWAY}"
        msg = MIMEText(message)
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_addr
        msg["Subject"] = ""  # SMS doesn't use subject line

        try:
            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg)
            print(f"  SMS sent to {number}")
        except Exception as e:
            print(f"  FAILED to send SMS to {number}: {e}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(TIMEZONE)
    print(f"Atlee Monitor — {now.strftime('%a %b %d, %I:%M %p CT')}")

    # Business hours gate
    if not is_business_hours():
        print(f"Outside business hours (7am-9pm CT). Exiting.")
        return

    # Fetch current availability
    print("Checking 2BR availability via Knock API...")
    try:
        current_units = fetch_2br_units()
    except Exception as e:
        print(f"ERROR fetching availability: {e}")
        sys.exit(1)

    print(f"Found {len(current_units)} available 2BR unit(s)")
    for u in current_units:
        print(f"  Unit {u['name']} | {u['bedrooms']}bd/{u['bathrooms']}ba | "
              f"{u['area']} sqft | ${u['price']}/mo | Available {u['available_on']}")

    # Load previous state
    previous = load_state()

    # First run: save baseline, don't alert (avoids false positive)
    if not previous.get("initialized"):
        print("First run — saving baseline state (no alert).")
        save_state(current_units)
        return

    # Find NEW units not in previous state
    prev_ids = {u["id"] for u in previous.get("units", [])}
    new_units = [u for u in current_units if u["id"] not in prev_ids]

    if new_units:
        print(f"NEW 2BR unit(s) found: {len(new_units)}")

        # Build SMS — keep it concise for the 160-char SMS segment limit
        lines = [f"NEW 2BR at The Atlee!"]
        for u in new_units:
            lines.append(
                f"Unit {u['name']} | {u['area']}sf | ${u['price']}/mo | Avail {u['available_on']}"
            )
        lines.append("theatlee.com/floorplans")

        message = "\n".join(lines)
        print(f"Sending SMS:\n{message}")
        send_sms(message)
    else:
        print("No new units since last check.")

    # Always save current state
    save_state(current_units)
    print("Done.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        print("Sending test SMS...")
        send_sms(
            "Success! You will now receive a text message from Bryan's "
            "email address whenever a 2BR apartment becomes available at The Atlee."
        )
        print("Done.")
    else:
        main()
