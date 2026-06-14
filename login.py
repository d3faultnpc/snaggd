from playwright.sync_api import sync_playwright
import json
import re
from pathlib import Path
from config import CONFIG

_RESUMES_PATH = Path(CONFIG.cookies_path).parent / "hh_resumes.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://hh.ru", timeout=0, wait_until="commit")
    print("Залогинься на hh.ru. Закрой браузер — куки сохранятся, затем загрузим список твоих резюме.")

    try:
        page.wait_for_event("close", timeout=0)
    except Exception:
        pass

    cookies = []
    try:
        cookies = context.cookies()
        with open(CONFIG.cookies_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"✅ Cookies saved → {CONFIG.cookies_path}")
    except Exception as e:
        print(f"❌ Failed to save cookies: {e}")

    browser.close()

if not cookies:
    raise SystemExit(1)

# Headless pass: extract resume list from /applicant/resumes
print("🔍 Загружаем список резюме с вашего аккаунта HH...")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto("https://hh.ru/applicant/resumes", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(3_000)

        items = page.eval_on_selector_all(
            "a[href*='/resume/']",
            "els => els.map(e => ({href: e.href, text: e.innerText}))"
        )

        seen: set = set()
        resumes = []
        for item in items:
            href = item["href"]
            if "/edit/" in href or "/visibility" in href:
                continue
            m = re.search(r'/resume/([a-f0-9]{30,})', href)
            if not m:
                continue
            uuid = m.group(1)
            if uuid in seen:
                continue
            seen.add(uuid)
            title = item["text"].split("\n")[0].strip() or "Resume"
            resumes.append({"title": title, "uuid": uuid})

        browser.close()

    if resumes:
        _RESUMES_PATH.write_text(json.dumps(resumes, ensure_ascii=False, indent=2), encoding="utf-8")
        for r in resumes:
            print(f"  📄 {r['title']}")
        print(f"✅ {len(resumes)} resume(s) → {_RESUMES_PATH}")
    else:
        print("⚠️  Резюме не найдены — проверьте, что вы залогинились корректно")

except Exception as e:
    print(f"⚠️  Не удалось загрузить резюме: {e}")
    print("   Продолжайте без автоопределения — укажите wise link вручную в визарде")
