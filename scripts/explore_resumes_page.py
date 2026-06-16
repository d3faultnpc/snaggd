"""Dry-run: navigate to /applicant/resumes, grab HTML, take screenshot.

Usage:  python scripts/explore_resumes_page.py
Output: tmp/resumes_page.png  +  console HTML dump of resume cards
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright

COOKIES_PATH = Path("data/hh_cookies.json")
SCREENSHOT_PATH = Path("tmp/resumes_page.png")
TARGET_URL = "https://hh.ru/applicant/resumes"

def main():
    SCREENSHOT_PATH.parent.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=["--window-size=1280,900"],
        )
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})

        # Load cookies
        cookies = json.loads(COOKIES_PATH.read_text())
        ctx.add_cookies(cookies)
        print(f"✅ Cookies loaded ({len(cookies)} entries)")

        page = ctx.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(4_000)

        # Take screenshot
        page.screenshot(path=str(SCREENSHOT_PATH), full_page=True)
        print(f"📸 Screenshot saved: {SCREENSHOT_PATH}")

        # Current URL (check auth redirect)
        print(f"📍 Current URL: {page.url}")

        # Try common resume card selectors
        selectors_to_try = [
            "[data-qa='resume-title']",
            "[data-qa='resume-name']",
            "a[href*='/resume/']",
            ".resume-title",
            "[class*='resume']",
            "h3 a[href*='resume']",
        ]

        print("\n--- Selector probe ---")
        for sel in selectors_to_try:
            els = page.query_selector_all(sel)
            if els:
                print(f"  FOUND {len(els):2d}x  {sel}")
                for i, el in enumerate(els[:3]):
                    try:
                        href = el.get_attribute("href") or ""
                        text = el.inner_text().strip()[:80]
                        print(f"           [{i}] text={text!r}  href={href[:80]!r}")
                    except Exception:
                        pass
            else:
                print(f"  miss        {sel}")

        # --- Click test: navigate to the first resume ---
        resume_links = page.eval_on_selector_all(
            "a[href*='/resume/']",
            "els => els.map(e => e.href).filter(h => !h.includes('/edit/'))"
        )
        print(f"\n--- Resume links (filtered) ---")
        for lnk in resume_links:
            print(f"  {lnk}")

        if resume_links:
            import re
            m = re.search(r'/resume/([a-f0-9]+)', resume_links[0])
            uuid = m.group(1) if m else None
            wise_link = f"https://hh.ru/search/vacancy?resume={uuid}&from=resumelist"
            print(f"\n✅ Extracted UUID: {uuid}")
            print(f"✅ Wise link:      {wise_link}")

            print("\n🖱 Clicking resume link...")
            page.goto(resume_links[0], wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3_000)
            page.screenshot(path="tmp/resume_detail.png", full_page=True)
            print(f"📸 Screenshot: tmp/resume_detail.png")
            print(f"📍 URL after click: {page.url}")

        browser.close()

if __name__ == "__main__":
    main()
