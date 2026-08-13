"""
Regression tests for adapters/hh/dom.py — the shared "every match, not the
first" lookup, and the cover-textarea cascade that depends on it.

The bug class: `page.query_selector(sel)` returns the first element in document
order. hh.ru renders the same address several times per page, and the first copy
is regularly the hidden one. Code written against query_selector then reports
"not found" on a page where the thing is right there, visible. It was fixed once
in click_apply_button (2026-08-02) and left standing in five other files until
2026-08-11.

Two evidence sources, no network and no hh.ru account:
  * synthetic pages for the logic itself
  * the real captured pages under debug_screenshots/, when present, for the
    claims about hh's actual markup (skipped cleanly when they are not)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path(__file__).parent.parent

# The shape that broke everything: same selector twice, first one hidden.
FIRST_MATCH_HIDDEN = """<html><body>
<a data-qa="vacancy-response-link-view-topic" style="display:none">Перейти в чат</a>
<a data-qa="vacancy-response-link-view-topic">Перейти в чат</a>
</body></html>"""

# An employer questionnaire: several textareas, none of them a cover field.
# The bare 'textarea' at the end of SELECTORS['cover_textarea'] matches all of
# them, which is why the salary/question guard above it has to carry the weight.
QUESTIONNAIRE = """<html><body>
<div data-qa="task-body">
  <div data-qa="task-question">Почему вы хотите у нас работать?</div>
  <textarea name="task_374818333_text"></textarea>
</div>
<div data-qa="task-body">
  <div data-qa="task-question">Ваши зарплатные ожидания?</div>
  <textarea name="task_374818334_text"></textarea>
</div>
</body></html>"""

# A real cover form: the address hh actually ships, plus an unrelated textarea
# ahead of it in the DOM to make document order the wrong answer.
COVER_FORM = """<html><body>
<textarea id="decoy" name="task_1_text"></textarea>
<textarea data-qa="vacancy-response-popup-form-letter-input"></textarea>
<button data-qa="vacancy-response-submit-popup">Отправить</button>
</body></html>"""

# hh's own profile-enrichment survey, as captured 2026-08-11 after an apply
# click. Every button here except the X writes to the user's real hh profile.
DATA_COLLECTOR = """<html><body>
<div role="dialog">
  <button data-qa="additional-data-collector__popup-close">×</button>
  <span data-qa="additional-data-collector__popup-title">Какой формат удобнее?</span>
  <label><input type="checkbox" checked>Удалённо</label>
  <button data-qa="additional-data-collector__popup-save">Сохранить и продолжить</button>
</div>
<script>document.querySelector('[data-qa="additional-data-collector__popup-close"]')
  .onclick = function(){ document.querySelector('[role=dialog]').remove(); };</script>
</body></html>"""

# hh's real response modal, structure copied from a live response modal (2026-08-12).
# The point of this fixture: the submit button lives in a footer that is a
# SIBLING of the content wrapper, not inside it. Both the dialog root and the
# wrapper match MODAL_SELECTORS, so a "last match in document order" reading of
# "topmost" picks the wrapper and loses the submit button — which is exactly what
# happened live: a cover letter was typed into a form the run could no longer
# submit, reported as "Navigation buttons not found in HH modal".
RESPONSE_MODAL = """<html><body>
<div role="dialog">
  <div class="magritte-modal-content-wrapper___-eFo3">
    <div data-qa="modal-header">Отклик на вакансию</div>
    <textarea data-qa="vacancy-response-popup-form-letter-input">письмо</textarea>
    <button data-qa="generate-cover-letter">Сгенерировать</button>
  </div>
  <div role="separator"></div>
  <div data-qa="modal-footer">
    <button data-qa="vacancy-response-submit-popup">Откликнуться</button>
  </div>
</div>
</body></html>"""

# Two stacked dialogs — hh does this routinely (three in a row on 2026-08-11).
STACKED_MODALS = """<html><body>
<div role="dialog"><h2>Какой формат удобнее?</h2><button>Сохранить</button></div>
<div role="dialog"><h2>Сколько хотите получать?</h2><button>Назад</button></div>
</body></html>"""


def run(pw):
    from adapters.hh.dom import (DATA_COLLECTOR_CLOSE, DATA_COLLECTOR_MARKER, MODAL_SELECTORS,
                                 count_visible, find_chat_link, find_topmost_dialog,
                                 find_visible, is_data_collector,
                                 is_employer_question_field, iter_visible)
    from adapters.hh.handlers.cover_only import CoverOnlyHandler
    from adapters.hh.handlers.hh_modal import HHModalHandler
    from config import SELECTORS

    failures = []
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()

    # 1. The core bug: first match hidden, second visible.
    page.set_content(FIRST_MATCH_HIDDEN)
    if page.query_selector(SELECTORS['chat_link']).is_visible():
        failures.append("fixture is wrong — the first match should be hidden")
    if find_chat_link(page) is None:
        failures.append("find_chat_link missed a visible second match (the original bug)")
    else:
        print("  ✅ visible second match found where query_selector() gave up")

    if count_visible(page, SELECTORS['chat_link']) != 1:
        failures.append("count_visible counted hidden elements")
    else:
        print("  ✅ count_visible counts only what is on screen")

    # 2. Cascade order is honoured across all matches, not abandoned after the
    #    first selector's first match.
    page.set_content(COVER_FORM)
    el = find_visible(page, SELECTORS['cover_textarea'])
    if el is None:
        failures.append("cover_textarea cascade found nothing on a real cover form")
    elif el.get_attribute('data-qa') != 'vacancy-response-popup-form-letter-input':
        failures.append(f"cover_textarea resolved to {el.get_attribute('name') or el.get_attribute('id')!r}, "
                        "not the addressed field — address-first ordering is broken")
    else:
        print("  ✅ cover_textarea resolves by address, not by document order")

    send = find_visible(page, SELECTORS['send_button'])
    if send is None or send.get_attribute('data-qa') != 'vacancy-response-submit-popup':
        failures.append("send_button did not resolve to vacancy-response-submit-popup")
    else:
        print("  ✅ send_button resolves by address")

    # 3. A cover letter must never be typed into an employer's question field.
    #    The bare 'textarea' at the end of the cascade matches every question
    #    box on a questionnaire; the structural guard is what stops it.
    page.set_content(QUESTIONNAIRE)
    raw = find_visible(page, SELECTORS['cover_textarea'])
    if raw is None or not (raw.get_attribute('name') or '').startswith('task_'):
        failures.append("fixture is wrong — the raw cascade should reach a question field here")
    for el in page.query_selector_all('textarea'):
        if not is_employer_question_field(el):
            failures.append(f"question field {el.get_attribute('name')} not recognised as one")
    guarded = CoverOnlyHandler()._find_cover_field(page)
    if guarded is not None:
        failures.append(f"cover finder returned an employer question field: {guarded.get_attribute('name')}")
    else:
        print("  ✅ employer question fields are refused as cover boxes")

    #    ...and the real cover box is still found when it is there.
    page.set_content(COVER_FORM)
    guarded = CoverOnlyHandler()._find_cover_field(page)
    if guarded is None or guarded.get_attribute('data-qa') != 'vacancy-response-popup-form-letter-input':
        failures.append("the guard also rejected a legitimate cover field")
    else:
        print("  ✅ a real cover box is still found through the guard")

    # 4. Stacked dialogs: the topmost is the one the user can act on.
    page.set_content(STACKED_MODALS)
    dialogs = iter_visible(page, MODAL_SELECTORS)
    if len(dialogs) != 2:
        failures.append(f"expected 2 stacked dialogs, iter_visible found {len(dialogs)}")
    else:
        print("  ✅ stacked dialogs are all seen")

    top = find_topmost_dialog(page)
    if top is None:
        failures.append("find_topmost_dialog found no dialog at all")
    elif 'получать' not in top.inner_text():
        failures.append(f"find_topmost_dialog picked the wrong one of a stack: {top.inner_text()!r}")
    else:
        print("  ✅ the last-opened dialog is the one handed to the model")

    # Nesting: a content wrapper inside a role="dialog". Both match; either is a
    # safe answer, but it must not crash or return the earlier dialog of a stack.
    page.set_content("""<html><body>
      <div role="dialog"><h2>Первая</h2><button>ок</button></div>
      <div role="dialog"><div class="magritte-modal-content-wrapper___x1">
        <h2>Вторая</h2><button>Сохранить</button></div></div>
    </body></html>""")
    top = find_topmost_dialog(page)
    if top is None or 'Вторая' not in top.inner_text():
        failures.append("nested wrapper confused topmost-dialog selection")
    else:
        print("  ✅ nested modal wrapper still resolves to the last dialog")

    # 4a-bis. THE live regression: the dialog root must win over its own inner
    #         wrapper, or the submit button falls outside the search scope.
    page.set_content(RESPONSE_MODAL)
    top = find_topmost_dialog(page)
    if top is None:
        failures.append("no dialog found on a real hh response modal")
    elif top.get_attribute('role') != 'dialog':
        failures.append("topmost dialog resolved to a part of the modal, not its root — "
                        "the submit footer sits outside that scope")
    else:
        print("  ✅ the dialog ROOT wins over its own content wrapper")

    btn, how = HHModalHandler(data_dir=Path("/tmp/snaggd-test-profile"))._find_action_button(
        page, addresses=[SELECTORS['popup_submit'], SELECTORS['letter_submit']],
        keywords=['далее', 'продолжить', 'откликнуться'])
    if btn is None:
        failures.append("submit button not found on a real hh response modal "
                        "— this is the 2026-08-12 'Navigation buttons not found' failure")
    elif btn.get_attribute('data-qa') != 'vacancy-response-submit-popup':
        failures.append(f"wrong button chosen: {btn.get_attribute('data-qa')} "
                        "(hh's own paid 'Сгенерировать' is the trap here)")
    else:
        print(f"  ✅ the response modal's submit button is found (by {how}), "
              "not hh's paid generator")

    # 4b. hh's own profile survey is recognised and closed, not answered.
    #     "Сохранить и продолжить" in one of these writes to the user's real
    #     hh.ru profile — on 2026-08-11 the model pressed it three times.
    page.set_content(DATA_COLLECTOR)
    dialog = find_topmost_dialog(page)
    if dialog is None or not is_data_collector(dialog):
        failures.append("hh's profile survey was not recognised as one")
    else:
        print("  ✅ hh's profile survey is recognised by address")

    close = find_visible(page, DATA_COLLECTOR_CLOSE)
    if close is None:
        failures.append("no close button found on the profile survey")
    else:
        close.click()
        if find_visible(page, DATA_COLLECTOR_MARKER) is not None:
            failures.append("clicking close did not dismiss the survey")
        else:
            print("  ✅ the survey closes on its own X — the model is never asked")

    #     A genuine application modal must NOT be mistaken for a survey.
    page.set_content(STACKED_MODALS)
    dialog = find_topmost_dialog(page)
    if dialog is not None and is_data_collector(dialog):
        failures.append("an ordinary dialog was misread as hh's profile survey")
    else:
        print("  ✅ ordinary dialogs still reach the model as before")

    # 4c. The navigation-button hunt: address beats wording, wording is confined
    #     to the open dialog, and hh's own survey buttons are never eligible.
    handler = HHModalHandler(data_dir=Path("/tmp/snaggd-test-profile"))
    nav_keywords = ['далее', 'подтвердить', 'продолжить', 'готово', 'отправить', 'откликнуться']

    page.set_content("""<html><body>
      <button>Продолжить</button>                      <!-- page chrome, not our form -->
      <div role="dialog">
        <button data-qa="vacancy-response-submit-popup">Отправить</button>
      </div>
    </body></html>""")
    btn, how = handler._find_action_button(
        page, addresses=[SELECTORS['popup_submit'], SELECTORS['letter_submit']], keywords=nav_keywords)
    if how != "address" or btn.get_attribute('data-qa') != 'vacancy-response-submit-popup':
        failures.append(f"address tier lost to wording: how={how}")
    else:
        print("  ✅ address beats wording when hh gives us one")

    page.set_content("""<html><body>
      <button>Продолжить</button>                      <!-- outside the dialog -->
      <div role="dialog"><h2>Отклик</h2><button>Готово</button></div>
    </body></html>""")
    btn, how = handler._find_action_button(page, addresses=[SELECTORS['popup_submit']], keywords=nav_keywords)
    if btn is None or btn.inner_text().strip() != 'Готово':
        failures.append(f"wording tier reached outside the open dialog: {btn and btn.inner_text()!r}")
    else:
        print("  ✅ wording is confined to the form being acted on")

    page.set_content(DATA_COLLECTOR)
    btn, how = handler._find_action_button(page, addresses=[SELECTORS['popup_submit']], keywords=nav_keywords)
    if btn is not None:
        failures.append(f"a hh profile-survey button was offered as a form action: {btn.inner_text()!r}")
    else:
        print("  ✅ hh's survey buttons are never eligible as form actions")

    # 5. Against hh's own captured markup, when it is on disk.
    caps = sorted((REPO / "debug_screenshots").rglob("*.html")) if (REPO / "debug_screenshots").exists() else []
    dupes = 0
    checked = 0
    for cap in caps:
        html = cap.read_text(encoding="utf-8", errors="ignore")
        if 'vacancy-response-link-view-topic' not in html:
            continue
        checked += 1
        if html.count('data-qa="vacancy-response-link-view-topic"') > 1:
            dupes += 1
    if checked == 0:
        print("  ⚠️ no captured post-apply pages on disk — skipped the markup check")
    elif dupes != checked:
        failures.append(f"expected every post-apply capture to duplicate the chat link, "
                        f"got {dupes}/{checked}")
    else:
        print(f"  ✅ all {checked} captured post-apply pages carry the chat link more than once "
              "— single-match lookups there were a coin flip")

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
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
