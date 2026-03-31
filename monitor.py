#!/usr/bin/env python3
"""
The Atlee 2BR Availability Monitor

Checks theatlee.com for 2-bedroom apartment availability every 5 minutes
during business hours (7am-9pm CT). Sends SMS alerts via Gmail SMTP →
Verizon email-to-SMS gateway ONLY when new units appear.

Designed to run as a GitHub Actions scheduled workflow.

Environment variables (set as GitHub Secrets):
    SMTP_EMAIL      - Gmail address used to send SMS
    SMTP_PASSWORD   - Gmail App Password (not your regular password)
    PHONE_NUMBERS   - Comma-separated phone numbers (digits only)
"""

import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ─── Configuration ───────────────────────────────────────────────────────────

FLOORPLAN_URLS = [
    ("Brookhurst", "https://www.theatlee.com/floorplans/brookhurst"),
    ("Grandview", "https://www.theatlee.com/floorplans/grandview"),
]

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_state.json")
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

def fetch_availability():
    """
    Load each floor plan page in a headless browser.
    Two extraction strategies run in parallel:
      1. Intercept API responses (JSON) returned by Apartments247
      2. Parse the rendered HTML for unit data
    Returns a list of unit dicts.
    """
    api_data = []

    def capture_response(response):
        try:
            ct = response.headers.get("content-type", "")
            if "json" in ct:
                body = response.json()
                api_data.append({"url": response.url, "data": body})
        except Exception:
            pass

    all_units = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        page.on("response", capture_response)

        for plan_name, url in FLOORPLAN_URLS:
            api_data.clear()

            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(4000)  # let dynamic content settle
            except Exception as e:
                print(f"  Warning: failed to load {url}: {e}")
                continue

            # Strategy 1: Parse intercepted API JSON
            for api in api_data:
                all_units.extend(parse_api_response(api["data"], plan_name))

            # Strategy 2: Parse rendered HTML
            html = page.content()
            all_units.extend(parse_html(html, plan_name))

        browser.close()

    # Deduplicate by unit ID
    seen = set()
    unique = []
    for u in all_units:
        if u["id"] not in seen:
            seen.add(u["id"])
            unique.append(u)

    return unique


# ─── API Response Parsing ────────────────────────────────────────────────────

def parse_api_response(data, plan_name):
    """
    Extract 2BR unit info from a JSON API response.
    Handles common Apartments247 / RentCafe / Yardi response shapes.
    """
    units = []
    items = _extract_unit_list(data)

    for item in items:
        if not isinstance(item, dict):
            continue

        # Check bedroom count — try every common key name
        beds = _get_first(item, [
            "bedrooms", "beds", "Beds", "NumberOfBedrooms",
            "BedroomCount", "bed_count", "numBedrooms",
        ])
        if str(beds).strip() not in ("2", "2.0"):
            continue

        unit_id = str(_get_first(item, [
            "id", "unitId", "UnitId", "apartmentId", "ApartmentId",
            "unit_id", "UnitNumber",
        ]) or "")

        unit = {
            "id": unit_id or f"{plan_name}-{item.get('name', hash(str(item)))}",
            "plan": plan_name,
            "unit": str(_get_first(item, [
                "unitName", "name", "UnitNumber", "ApartmentName",
                "unit_name", "unit_number", "unitNumber",
            ]) or ""),
            "price": str(_get_first(item, [
                "rent", "price", "MinimumRent", "Rent",
                "monthlyRent", "effectiveRent", "min_rent",
            ]) or ""),
            "sqft": str(_get_first(item, [
                "sqft", "squareFeet", "SQFT", "MaximumSQFT",
                "square_feet", "area", "sqFt",
            ]) or ""),
            "available_date": str(_get_first(item, [
                "availableDate", "moveInDate", "AvailableDate",
                "MoveInDate", "available_date", "move_in_date",
            ]) or ""),
        }
        units.append(unit)

    return units


def _extract_unit_list(data):
    """Dig into a JSON blob and find the list of unit objects."""
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    # Direct unit arrays
    for key in ("units", "availableUnits", "apartments", "results", "data"):
        if key in data and isinstance(data[key], list):
            return data[key]

    # Nested: floorplans → units
    for fp_key in ("floorplans", "floorPlans", "floor_plans"):
        if fp_key in data and isinstance(data[fp_key], list):
            items = []
            for fp in data[fp_key]:
                if isinstance(fp, dict):
                    for u_key in ("units", "availableUnits", "apartments"):
                        if u_key in fp and isinstance(fp[u_key], list):
                            items.extend(fp[u_key])
            if items:
                return items

    return []


def _get_first(d, keys):
    """Return the first matching key's value from a dict."""
    for k in keys:
        if k in d and d[k] not in (None, "", "null"):
            return d[k]
    return None


# ─── HTML Parsing ────────────────────────────────────────────────────────────

def parse_html(html, plan_name):
    """
    Extract unit info from the fully rendered HTML page.
    Looks for common RentCafe / Apartments247 DOM patterns.
    """
    soup = BeautifulSoup(html, "html.parser")
    units = []

    # Try common CSS selectors for availability rows
    selectors = [
        ".availableUnitsRow",
        ".unit-row",
        ".available-unit",
        "[data-unit-id]",
        ".fp-unit",
        "tr.unit",
        ".unit-card",
        ".apartment-card",
        ".unit-item",
        ".floorplan-unit",
    ]

    for selector in selectors:
        for el in soup.select(selector):
            unit = _parse_unit_element(el, plan_name)
            if unit:
                units.append(unit)

    # Fallback: look for any container with price + date patterns
    if not units:
        for el in soup.find_all(["div", "tr", "li", "article"], recursive=True):
            text = el.get_text(" ", strip=True)
            # Only consider elements that look like unit listings
            if re.search(r'\$[\d,]+', text) and len(text) < 500:
                unit = _parse_unit_element(el, plan_name)
                if unit:
                    units.append(unit)

    return units


def _parse_unit_element(el, plan_name):
    """Parse a single unit element from the DOM."""
    text = el.get_text(" ", strip=True)
    if not text:
        return None

    price_match = re.search(r'\$([\d,]+)', text)
    price = price_match.group(0) if price_match else ""

    date_match = re.search(
        r'(\d{1,2}/\d{1,2}/\d{2,4}|\w+ \d{1,2},?\s*\d{4}|Available\s*Now)',
        text, re.IGNORECASE
    )
    date = date_match.group(0) if date_match else ""

    unit_match = re.search(r'(?:Unit|Apt|#)\s*(\S+)', text, re.IGNORECASE)
    unit_num = unit_match.group(1) if unit_match else ""

    sqft_match = re.search(r'([\d,]+)\s*(?:sq\.?\s*ft|SF)', text, re.IGNORECASE)
    sqft = sqft_match.group(1) if sqft_match else ""

    unit_id = el.get("data-unit-id", "") or el.get("id", "")
    if not unit_id:
        unit_id = f"{plan_name}-{unit_num or hash(text)}"

    # Only return if we found at least some meaningful data
    if price or date or unit_num:
        return {
            "id": str(unit_id),
            "plan": plan_name,
            "unit": unit_num,
            "price": price,
            "sqft": sqft,
            "available_date": date,
        }
    return None


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
        print(f"Outside business hours (7am–9pm CT). Exiting.")
        return

    # Fetch current availability
    print("Checking 2BR availability...")
    current_units = fetch_availability()
    print(f"Found {len(current_units)} 2BR unit(s)")
    for u in current_units:
        print(f"  {u['plan']} | {u.get('unit', '?')} | {u.get('price', '?')} | {u.get('available_date', '?')}")

    # Load previous state
    previous = load_state()

    # First run: just save state, don't alert (avoids false positive)
    if not previous.get("initialized"):
        print("First run — saving baseline state (no alert).")
        save_state(current_units)
        return

    # Find NEW units not in previous state
    prev_ids = {u["id"] for u in previous.get("units", [])}
    new_units = [u for u in current_units if u["id"] not in prev_ids]

    if new_units:
        print(f"NEW 2BR unit(s) found: {len(new_units)}")

        # Build SMS message (keep it short — 160 char SMS limit per segment)
        lines = [f"NEW 2BR at The Atlee!"]
        for u in new_units:
            parts = []
            if u.get("plan"):
                parts.append(u["plan"])
            if u.get("unit"):
                parts.append(f"#{u['unit']}")
            if u.get("price"):
                parts.append(u["price"])
            if u.get("available_date"):
                parts.append(u["available_date"])
            lines.append(" | ".join(parts))
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
    main()
