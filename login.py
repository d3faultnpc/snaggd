from playwright.sync_api import sync_playwright
import json
from config import CONFIG

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://hh.ru", timeout=0, wait_until="commit")
    print("Залогинься на hh.ru. Закрой браузер — куки сохранятся автоматически.")

    try:
        page.wait_for_event("close", timeout=0)
    except Exception:
        pass

    try:
        cookies = context.cookies()
        with open(CONFIG.cookies_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"✅ Cookies saved → {CONFIG.cookies_path}")
    except Exception as e:
        print(f"❌ Failed to save cookies: {e}")

    browser.close()
