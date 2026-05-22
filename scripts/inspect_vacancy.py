"""One-shot vacancy inspector: open → screenshot → click Apply → screenshot + selectors + text. No LLM."""
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG

VACANCY_URL = sys.argv[1] if len(sys.argv) > 1 else ""
OUT_DIR = Path("debug_screenshots/inspect")
OUT_DIR.mkdir(parents=True, exist_ok=True)

if not VACANCY_URL:
    print("Usage: python scripts/inspect_vacancy.py <url>")
    sys.exit(1)

import json
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    with open(CONFIG.cookies_path, "r", encoding="utf-8") as f:
        ctx.add_cookies(json.load(f))
    page = ctx.new_page()

    print(f"Opening: {VACANCY_URL}")
    page.goto(VACANCY_URL, timeout=60000, wait_until="domcontentloaded")
    print("Waiting 5s for page to render...")
    time.sleep(5)

    page.screenshot(path=str(OUT_DIR / "01_vacancy.png"), full_page=False)
    (OUT_DIR / "01_vacancy.html").write_text(page.inner_html("body"), encoding="utf-8")
    print("📸 01_vacancy saved")

    # Click Apply
    apply_btn = None
    for selector in ['a:has-text("Откликнуться")', 'button:has-text("Откликнуться")']:
        el = page.query_selector(selector)
        if el and el.is_visible():
            apply_btn = el
            break

    if not apply_btn:
        print("❌ Apply button not found")
        browser.close()
        sys.exit(1)

    print("Clicking Apply...")
    apply_btn.click()
    print("Waiting 5s for questionnaire to load...")
    time.sleep(5)

    page.screenshot(path=str(OUT_DIR / "02_after_apply.png"), full_page=True)
    (OUT_DIR / "02_after_apply.html").write_text(page.inner_html("body"), encoding="utf-8")

    data_qa = page.evaluate("""() => {
        const els = document.querySelectorAll('[data-qa]');
        const vals = new Set();
        els.forEach(el => vals.add(el.getAttribute('data-qa')));
        return Array.from(vals).sort();
    }""")
    (OUT_DIR / "02_after_apply_data_qa.txt").write_text("\n".join(data_qa), encoding="utf-8")

    text = page.inner_text("body")
    (OUT_DIR / "02_after_apply_text.txt").write_text(text[:8000], encoding="utf-8")

    print(f"📸 02_after_apply saved")
    print(f"\n--- data-qa ({len(data_qa)} unique) ---")
    for qa in data_qa:
        if any(k in qa for k in ["question", "response", "form", "vacancy", "survey", "answer", "input", "submit", "next"]):
            print(f"  ★ {qa}")
    print("\n--- page text (first 2000 chars) ---")
    print(text[:2000])

    print(f"\n✅ All files saved to {OUT_DIR}/")
    input("Press Enter to close browser...")
    browser.close()
