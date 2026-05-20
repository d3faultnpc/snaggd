from playwright.sync_api import sync_playwright
import json

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://hh.ru")
    print("Залогинься в открывшемся окне. Когда готов — нажми Enter в терминале...")
    input()
 
    cookies = context.cookies()
    with open("hh_cookies.json", "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
        print("Cookies сохранены в hh_cookies.json")
        browser.close()
