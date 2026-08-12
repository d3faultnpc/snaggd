"""Shared DOM lookup helpers for the hh.ru adapter.

Exists because the same bug kept being written independently in five files.

The bug: `page.query_selector(sel)` returns the FIRST element in document order
that matches — and hh.ru pages routinely render the same address more than once.
A vacancy page carries 3-4 `vacancy-response-link-top` (recommendation cards
below the fold); a post-apply page carries exactly 2
`vacancy-response-link-view-topic` (verified 2026-08-11 across 24 captured
post-apply pages, every one of them n=2). When the first match happens to be the
hidden one, single-match code reports "not found" and the caller narrates a
blocked apply that was never blocked. That exact failure was diagnosed live on
2026-08-02 (a live vacancy page) and fixed in `browser.py::click_apply_button`
alone; every other site kept the original shape.

So: look at every match of every selector, in the caller's own priority order,
and take the first VISIBLE one. Strictly more permissive than single-match — it
can only find elements the old code missed.

`scope` is anything with `query_selector_all` — a Page or a Frame (chatik lives
in an iframe), which is why nothing here is typed to Page.
"""

from typing import Iterable, Optional, Union


def _as_list(selectors: Union[str, Iterable[str]]) -> list:
    """A single selector string and a cascade of them are both valid input."""
    if isinstance(selectors, str):
        return [selectors]
    return list(selectors)


def find_visible(scope, selectors: Union[str, Iterable[str]], visible_only: bool = True):
    """First visible element matching any selector, honouring cascade order.

    Tries EVERY match of selector #1 before moving to selector #2 — the whole
    point of this module. `visible_only=False` degrades to "first match at all",
    still across the full cascade.
    """
    fallback = None
    for selector in _as_list(selectors):
        try:
            for element in scope.query_selector_all(selector):
                try:
                    if element.is_visible():
                        return element
                except Exception:
                    continue
                if fallback is None:
                    fallback = element
            # `wait_for_selector`-free by design: callers that need to wait do it
            # themselves; this helper only reads the DOM as it stands right now.
        except Exception:
            continue
    return None if visible_only else fallback


# The one place that answers "what counts as a dialog on hh.ru".
#
# role="dialog"/"alertdialog" are proven live — the 2026-08-11 run dismissed
# three consecutive modals through them. They are NOT sufficient on their own:
# the magritte response modal captured the same day carries no role at all, and
# the debug snapshotter only reached it through a class match. Its root is
# `magritte-modal-content-wrapper___<hash>`; the hash changes every frontend
# deploy, so the prefix is matched by substring deliberately — it is a design
# system component name, not a generated class name.
#
# Order matters: entries are modal ROOTS, most specific first. Never add a part
# of a modal here (`[data-qa="modal-header"]` and friends) — callers read
# inner_text() and hunt for buttons INSIDE whatever this returns, and a header
# would silently give them a modal with no buttons in it.
MODAL_SELECTORS = (
    '[role="alertdialog"]',
    '[role="dialog"]',
    '[data-qa="magritte-alert"]',
    '[class*="magritte-modal-content-wrapper"]',
)


# hh's own profile-enrichment surveys: "Какой формат удобнее?", "В каком городе
# живёте", "Сколько хотите получать". They open OVER a vacancy after the apply
# click and have nothing to do with the application — hh is collecting profile
# data, and every button in them except the close X writes to the user's real
# hh.ru profile. Recognised by address so they can be closed rather than
# interpreted; see adapter.py::_dismiss_blocking_modal.
DATA_COLLECTOR_MARKER = '[data-qa^="additional-data-collector__"]'
DATA_COLLECTOR_CLOSE = '[data-qa="additional-data-collector__popup-close"]'

# An employer's own question field, which must never receive a cover letter.
# Three shapes seen in captures: a field inside a task-body block, a field
# inside one of hh's vacancy-response-question blocks, and the bare
# name="task_<id>_text" textarea that a questionnaire renders per question.
_QUESTION_FIELD_JS = """el => {
    if (el.closest('[data-qa="task-body"]')) return true;
    if (el.closest('[data-qa^="vacancy-response-question"]')) return true;
    return /^task_\\d+_text$/.test(el.getAttribute('name') || '');
}"""


def is_employer_question_field(element) -> bool:
    """True if this input belongs to an employer's questionnaire.

    Fails CLOSED: when the check itself blows up the answer is "yes, it is a
    question field", so the caller declines to type into it. The cost of a false
    yes is one skipped application; the cost of a false no is a cover letter
    submitted into an employer's screening question, under the user's name.
    """
    try:
        return bool(element.evaluate(_QUESTION_FIELD_JS))
    except Exception as e:
        print(f"   ⚠️ Couldn't tell whether a field belongs to an employer questionnaire ({e}) "
              f"— refusing to type into it")
        return True


def is_data_collector(dialog) -> bool:
    """True if this dialog is one of hh's own profile-enrichment surveys."""
    try:
        return dialog.query_selector(DATA_COLLECTOR_MARKER) is not None
    except Exception:
        return False


def is_in_data_collector(element) -> bool:
    """True if this element belongs to one of hh's profile surveys.

    The element-level counterpart of is_data_collector, for the button hunts:
    a survey's "Сохранить и продолжить" is a perfect match for every
    navigation keyword the code knows, and clicking it saves profile data
    instead of advancing an application. Fails closed — an unanswerable check
    means "leave it alone".
    """
    try:
        return bool(element.evaluate(
            "el => !!el.closest('[data-qa^=\"additional-data-collector__\"]')"
            " || !!(el.getAttribute('data-qa')||'').startsWith('additional-data-collector__')"
        ))
    except Exception:
        return True


def find_topmost_dialog(scope):
    """The dialog the user is actually looking at, or None.

    hh stacks dialogs — the 2026-08-11 run met three in a row — and the one the
    user can click is the last one opened. Document order is the proxy for that:
    hh appends each new dialog to the end of the body, so the LAST match wins,
    not the first, which is what every single-match probe here used to take.

    Matching is done in ONE query rather than per selector so the results come
    back in true document order; iterating the cascade would order them by
    selector priority instead, and "last" would then mean "matched the
    lowest-priority selector", which is a different and useless question. A
    consequence worth naming: when a dialog root and an inner content wrapper
    both match, this returns the inner one — harmless, since callers only read
    text and buttons out of it, and both contain the same ones.
    """
    try:
        candidates = [el for el in scope.query_selector_all(", ".join(MODAL_SELECTORS))
                      if _safe_visible(el)]
    except Exception:
        return None
    return candidates[-1] if candidates else None


def _safe_visible(element) -> bool:
    try:
        return element.is_visible()
    except Exception:
        return False


def iter_visible(scope, selectors: Union[str, Iterable[str]]) -> list:
    """Every visible match across the cascade, in cascade then document order."""
    found = []
    for selector in _as_list(selectors):
        try:
            for element in scope.query_selector_all(selector):
                try:
                    if element.is_visible():
                        found.append(element)
                except Exception:
                    continue
        except Exception:
            continue
    return found


def count_visible(scope, selectors: Union[str, Iterable[str]]) -> int:
    """How many visible matches — for detection probes that only need a boolean
    but whose author should be able to see the duplicate count in a debug run."""
    total = 0
    for selector in _as_list(selectors):
        try:
            for element in scope.query_selector_all(selector):
                try:
                    if element.is_visible():
                        total += 1
                except Exception:
                    continue
        except Exception:
            continue
    return total


def find_chat_link(scope):
    """The 'go to the employer chat' link, hh's `vacancy-response-link-view-topic`.

    One function instead of the five hand-rolled single-match copies that used to
    live in adapter.py (×2), detector.py, hh_modal.py and chat.py — the selector
    is duplicated exactly twice per post-apply page, so every one of those copies
    was a coin flip on which of the two it got.
    """
    from config import SELECTORS
    return find_visible(scope, SELECTORS['chat_link'])
