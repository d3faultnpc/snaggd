"""Test QuestionsHandler._extract_label() logic against a live questionnaire page.
Opens the vacancy, clicks Apply, waits for the questionnaire, extracts labels via JS.
Does NOT fill or submit anything.

Usage: python scripts/test_questions_labels.py <vacancy_url>
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG

VACANCY_URL = sys.argv[1] if len(sys.argv) > 1 else ""
OUT_DIR = Path("debug_screenshots/test_labels")
OUT_DIR.mkdir(parents=True, exist_ok=True)

if not VACANCY_URL:
    print("Usage: python scripts/test_questions_labels.py <url>")
    sys.exit(1)

EXTRACT_JS = """el => {
    const body = el.closest('[data-qa="task-body"]');
    if (body) {
        const q = body.querySelector('[data-qa="task-question"]');
        if (q && q.innerText.trim()) return q.innerText.trim();
    }
    return '';
}"""

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    with open(CONFIG.cookies_path, "r", encoding="utf-8") as f:
        ctx.add_cookies(json.load(f))
    page = ctx.new_page()

    print(f"Opening: {VACANCY_URL}")
    page.goto(VACANCY_URL, timeout=60000, wait_until="domcontentloaded")
    time.sleep(4)

    page.screenshot(path=str(OUT_DIR / "01_vacancy.png"))
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

    print("Clicking Apply (same tab)...")
    apply_btn.click()
    print("Waiting 5s for questionnaire page...")
    time.sleep(5)

    page.screenshot(path=str(OUT_DIR / "02_questionnaire.png"), full_page=True)
    (OUT_DIR / "02_questionnaire.html").write_text(page.inner_html("body"), encoding="utf-8")
    print("📸 02_questionnaire saved")

    # Extract labels using the same logic as QuestionsHandler._extract_label()
    inputs = page.query_selector_all('input[type="text"], input[type="radio"], textarea')
    print(f"\n--- Found {len(inputs)} input elements ---")

    results = []
    for i, inp in enumerate(inputs[:CONFIG.max_questions_per_form]):
        visible = inp.is_visible()
        placeholder = inp.get_attribute("placeholder") or ""
        field_type = inp.get_attribute("type") or inp.evaluate("el => el.tagName.toLowerCase()")

        # Run the _extract_label JS
        try:
            label_from_js = inp.evaluate(EXTRACT_JS)
        except Exception as e:
            label_from_js = f"[ERROR: {e}]"

        # Fallback: xpath label search
        label_from_xpath = ""
        if not label_from_js:
            for xpath in ("xpath=..//label", "xpath=..//..//label"):
                try:
                    el = inp.query_selector(xpath)
                    if el:
                        t = el.inner_text().strip()
                        if t:
                            label_from_xpath = t[:300]
                            break
                except Exception:
                    pass

        final_label = label_from_js or label_from_xpath or placeholder or "[NO LABEL]"

        status = "✅" if label_from_js else ("⚠️" if label_from_xpath else "❌")
        source = "aria-labelledby/task-question" if label_from_js else ("xpath" if label_from_xpath else "placeholder/none")

        results.append({
            "idx": i, "visible": visible, "type": field_type,
            "label": final_label, "source": source
        })

        if visible:
            print(f"  [{status} #{i+1}] type={field_type} | source={source}")
            print(f"         label: {final_label[:100]}")

    visible_count = sum(1 for r in results if r["visible"] and r["label"] != "[NO LABEL]")
    print(f"\n✅ {visible_count}/{len(results)} visible fields with labels")
    print(f"All data saved to {OUT_DIR}/")

    time.sleep(3)
    browser.close()
