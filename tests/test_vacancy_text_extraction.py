"""
Regression test for HHBrowser.get_vacancy_text().

Branded ("constructor") vacancy pages can render TWO elements carrying
data-qa="vacancy-description": an empty wrapper first, then the one holding the
actual copy. The original code used query_selector() — first DOM match only —
took the empty wrapper, got "", and the caller logged
'skipped_no_text: Could not extract vacancy text' on a page whose description
was fully present.

Live case: a live vacancy page (2026-08-11) — element [0] 0 chars,
element [1] 3787 chars. Same class of bug click_apply_button() was corrected for
in 2026-08-03 ("check EVERY match, not just the first DOM hit"); the correction
had never been carried across to this function.

Real browser, synthetic pages — no network.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

DUPLICATE_EMPTY_FIRST = """<html><body>
<div data-qa="vacancy-description" class="vacancy-branded-user-content"></div>
<div data-qa="vacancy-description" class="vacancy-branded-user-content">
  Здравствуй, кандидат! Мы в поисках крутых специалистов.
</div></body></html>"""

NORMAL = """<html><body>
<div data-qa="vacancy-description">Чем предстоит заниматься: управление платформой.</div>
</body></html>"""

ALL_EMPTY = """<html><body>
<div data-qa="vacancy-description"></div>
<div data-qa="vacancy-description">   </div>
</body></html>"""

ABSENT = "<html><body><div>no description here</div></body></html>"


def make_browser(page):
    with patch("adapters.hh.browser.sync_playwright"):
        from adapters.hh.browser import HHBrowser
        b = HHBrowser()
    b.vacancy_page = page
    return b


def run(pw):
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    hh = make_browser(page)
    failures = []

    page.set_content(DUPLICATE_EMPTY_FIRST)
    text = hh.get_vacancy_text()
    if not text or "кандидат" not in text:
        failures.append(f"duplicate-description page returned {text!r}, expected the real copy")
    else:
        print("  ✅ empty first wrapper skipped, real description returned")

    page.set_content(NORMAL)
    text = hh.get_vacancy_text()
    if not text or "заниматься" not in text:
        failures.append(f"ordinary page regressed: {text!r}")
    else:
        print("  ✅ ordinary single-element page unchanged")

    page.set_content(ALL_EMPTY)
    if hh.get_vacancy_text() is not None:
        failures.append("all-empty descriptions should return None, not a blank string")
    else:
        print("  ✅ all-empty descriptions correctly report no text")

    page.set_content(ABSENT)
    if hh.get_vacancy_text() is not None:
        failures.append("missing description should return None")
    else:
        print("  ✅ missing description correctly reports no text")

    browser.close()
    return failures


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ⚠️ playwright not installed — skipping (not a failure)")
        return 0
    with sync_playwright() as pw:
        failures = run(pw)
    if failures:
        for f in failures:
            print(f"  ❌ {f}")
        print(f"\n{len(failures)} failed")
        return 1
    print("\n4/4 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
