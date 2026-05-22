from playwright.sync_api import sync_playwright
import json
from config import CONFIG

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://hh.ru")
    print("Log in to HH.ru in the browser window. When ready, press Enter in the terminal...")
    input()

    cookies = context.cookies()
    cookies_path = CONFIG.cookies_path
    with open(cookies_path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"Cookies saved to {cookies_path}")
        browser.close()
