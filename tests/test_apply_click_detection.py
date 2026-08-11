"""
Regression tests for HHBrowser._apply_response_surface() — the check that
decides whether an Apply click actually landed.

The bug this pins down (live 2026-08-11, a live vacancy page): the previous
check asked "is the button I clicked still visible with the same label?" and read
yes as "the click never happened". On hh.ru the normal success path opens a modal
*over* the page, leaving that button exactly where it was — and Playwright's
is_visible() reports layout/CSS visibility, not occlusion, so a button underneath
a modal still counts as visible. Every modal-based apply was therefore declared a
failure, a forced re-click was fired through the open modal, and the vacancy was
logged 'skipped_no_apply_button' while the modal sat open and unhandled.

Real browser, synthetic pages — no network, no hh.ru, no account. The two shapes
that matter are (1) click opens a modal over the page, (2) click does nothing at
all; the fix must tell them apart, and must keep catching (2), which is what the
original check existed for.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# Success path: the button stays put and a modal appears above it.
MODAL_OVER_PAGE = """<html><body>
<button id="apply">Откликнуться</button>
<div id="m" style="display:none;position:fixed;inset:0;background:#fff">
  <div data-qa="modal-header">Отклик на вакансию</div>
  <textarea placeholder="Сопроводительное письмо"></textarea>
  <div data-qa="modal-footer"><button>Откликнуться</button></div>
</div>
<script>document.getElementById('apply').onclick=function(){
  document.getElementById('m').style.display='block';}</script>
</body></html>"""

# Failure path: a click that genuinely achieves nothing.
DEAD_BUTTON = """<html><body><button id="apply">Откликнуться</button></body></html>"""

# Success path via navigation rather than a modal.
NAVIGATES = """<html><body>
<button id="apply" onclick="location.hash='#responded'">Откликнуться</button>
</body></html>"""


def make_browser(page):
    with patch("adapters.hh.browser.sync_playwright"):
        from adapters.hh.browser import HHBrowser
        b = HHBrowser()
    b.vacancy_page = page
    return b


def old_predicate(button, before_text):
    """The shipped-and-wrong check, reproduced so the test proves the bug was
    real rather than only that the new code passes its own tests."""
    try:
        return bool(before_text
                    and button.is_visible()
                    and (button.inner_text() or "").strip() == before_text)
    except Exception:
        return False


def run(pw):
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    hh = make_browser(page)
    failures = []

    # 1 — modal over the page: the exact production failure
    page.set_content(MODAL_OVER_PAGE)
    btn = page.query_selector("#apply")
    before_text = (btn.inner_text() or "").strip()
    before_url = page.url
    btn.click()
    page.wait_for_timeout(200)

    if not old_predicate(btn, before_text):
        failures.append("old predicate no longer reproduces the bug — test is stale")
    else:
        print("  ✅ old predicate still mis-reads a modal-over-page click as 'no effect'")

    if not hh._apply_response_surface(before_url):
        failures.append("modal over page NOT detected as a landed click")
    else:
        print("  ✅ modal over page detected as a landed click")

    # 2 — genuinely dead click must still be caught (no regression on the
    #     protection the original check was added for)
    page.set_content(DEAD_BUTTON)
    btn = page.query_selector("#apply")
    before_url = page.url
    btn.click()
    page.wait_for_timeout(200)
    if hh._apply_response_surface(before_url):
        failures.append("a click that did nothing was reported as landed")
    else:
        print("  ✅ dead click still reported as 'nothing happened'")

    # 3 — navigation counts as landing
    page.set_content(NAVIGATES)
    btn = page.query_selector("#apply")
    before_url = page.url
    btn.click()
    page.wait_for_timeout(200)
    if not hh._apply_response_surface(before_url):
        failures.append("navigation NOT detected as a landed click")
    else:
        print("  ✅ navigation detected as a landed click")

    # 4 — a bare vacancy-like page with no response surface must not
    #     false-positive (breadth of the selector list is the risk here)
    page.set_content("<html><body><div>Обычная страница вакансии</div>"
                     "<a data-qa='vacancy-response-link-top'>Откликнуться</a></body></html>")
    before_url = page.url
    if hh._apply_response_surface(before_url):
        failures.append("plain vacancy page false-positived as a landed click")
    else:
        print("  ✅ plain vacancy page does not false-positive")

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
