#!/usr/bin/env python3
"""
The Atlee API Discovery Script
Run this ONCE locally to capture the API endpoints and response format.
This tells us exactly how to build the lightweight monitor.

Usage:
    pip install playwright beautifulsoup4
    playwright install chromium
    python discover.py
"""

import json
from playwright.sync_api import sync_playwright

URLS = [
    ("Brookhurst (2BR)", "https://www.theatlee.com/floorplans/brookhurst"),
    ("Grandview (2BR)", "https://www.theatlee.com/floorplans/grandview"),
    ("All Available", "https://www.theatlee.com/availableunits"),
]

captured = []


def on_response(response):
    """Capture all JSON-ish API responses."""
    url = response.url
    ct = response.headers.get("content-type", "")
    if "json" in ct or "javascript" in ct:
        try:
            data = response.json()
            captured.append({
                "url": url,
                "status": response.status,
                "data": data,
            })
        except Exception:
            pass


def main():
    print("=" * 60)
    print("The Atlee — API Discovery")
    print("=" * 60)

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
        page.on("response", on_response)

        for label, url in URLS:
            print(f"\nLoading: {label}")
            print(f"  URL: {url}")
            before = len(captured)
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(4000)
            after = len(captured)
            print(f"  Captured {after - before} API response(s)")

            # Also dump the visible text for context
            text = page.inner_text("body")
            # Save page text for analysis
            safe_label = label.replace(" ", "_").replace("(", "").replace(")", "")
            with open(f"page_text_{safe_label}.txt", "w") as f:
                f.write(text)
            print(f"  Page text saved to page_text_{safe_label}.txt")

        browser.close()

    # Save all captured API responses
    with open("discovered_apis.json", "w") as f:
        json.dump(captured, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"Done. Captured {len(captured)} total API response(s).")
    print(f"Results saved to: discovered_apis.json")
    print(f"\nAPI endpoints found:")
    for i, c in enumerate(captured):
        data_preview = json.dumps(c["data"])[:120]
        print(f"  [{i}] {c['url'][:100]}")
        print(f"       Status: {c['status']} | Preview: {data_preview}...")
    print(f"\nNext step: share discovered_apis.json so we can finalize the monitor.")


if __name__ == "__main__":
    main()
