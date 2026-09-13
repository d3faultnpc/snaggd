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
        except Exception as e:
            # Said out loud, not swallowed. A selector that RAISES is a broken
            # selector — a malformed cascade entry, or a scope that went away —
            # and it is not the same thing as an address that matched nothing.
            # Silently continuing made the two indistinguishable in the one
            # helper every caller shares, which is the same disease the
            # detector's own probes had (2026-09-09 audit). Control flow is
            # deliberately unchanged: the cascade still moves on, because the
            # next address may well answer.
            print(f"   ⚠️ selector {selector!r} could not be evaluated ({e}) — "
                  "moving on down the cascade")
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
# The way off hh's own territory. The exact address is what has actually worked
# (three clean closes on record); the second entry is any close-shaped control
# inside the survey, which survives hh renaming just the popup-close leaf.
#
# Said plainly: this cascade fixes NONE of the failures measured so far. Both of
# those found the button and could not click it — a stale handle once, an
# overlay swallowing the click once — and a second address helps with neither.
# It is here because a single-address entry is a delayed incident (the
# 2026-09-09 audit counted 24 of them, and the seven that have cascades got
# them only after hh broke us), not because it answers anything observed.
#
# No wording tier, deliberately. A Russian aria-label here would be the exact
# hardcode this sprint exists to stop adding, on the one territory where a
# navigator is about to be the proper backstop.
# Controls that SAVE. Taken from the survey's own localisation dictionary,
# captured from a real page on 2026-08-27 — `editor.save` and `examples.append`
# are the two that write to the person's resume — plus the exact label that got
# pressed on 2026-08-11 and wrote work format, city and desired salary into a
# real profile, three surveys in a row, silently.
#
# The last line of defence and nothing more: the navigator is asked which
# control DISMISSES the survey, the answer is checked to be inside the survey,
# and only then does this veto get a look. Wording, on purpose — a save button
# is identified by what it says, and the alternative is finding out structurally
# after the click.
SAVE_SHAPED = (
    "сохранить",
    "добавить в мои достижения",
    "продолжить",
)

DATA_COLLECTOR_CLOSE = [
    '[data-qa="additional-data-collector__popup-close"]',
    '[data-qa^="additional-data-collector__"] [data-qa*="close"]',
]

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


# hh's own response popup — the modal the Apply click opens, with the resume
# card, the collapsed "Добавить сопроводительное", and "Откликнуться". It is the
# application itself, and it has never been a blocker; but in its collapsed
# state it has no visible textarea, so the detector could not name it, and the
# dismisser then asked the model which of ITS buttons to press (2026-09-11,
# vacancy #2: the model refused, detection fell through to the page beneath
# the overlay, and the run filled a questionnaire nobody could click). Known by
# its submit's address, which is the one thing every state of this modal has.
RESPONSE_POPUP_MARKER = '[data-qa="vacancy-response-submit-popup"]'


def is_response_popup(dialog) -> bool:
    """True if this dialog is hh's own response popup, in any of its states."""
    try:
        return dialog.query_selector(RESPONSE_POPUP_MARKER) is not None
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


# Last opened, outermost. Both halves are load-bearing:
#
#   LAST, because hh stacks dialogs — the 2026-08-11 run met three in a row —
#   and the one the user can act on is the most recent, which hh appends to the
#   end of the body.
#
#   OUTERMOST, because a dialog's own parts also match this selector list, and a
#   part is not the dialog. Real markup, captured live (2026-08-12):
#
#       [role="dialog"]
#       ├── magritte-modal-content-wrapper   ← title, textarea, "Сгенерировать"
#       ├── divider
#       └── footer → vacancy-response-submit-popup   "Откликнуться"
#
#   The footer is a SIBLING of the wrapper. Taking the last match in plain
#   document order picks the wrapper, and the submit button then sits outside
#   the search scope entirely: the run filled in a cover letter it could no
#   longer send and reported "Navigation buttons not found in HH modal".
#
# So: discard any match nested inside another match, then take the last of what
# remains. Done in one page evaluation because the containment test needs all
# the candidates at once.
_TOPMOST_DIALOG_JS = """(selector) => {
    const all = Array.from(document.querySelectorAll(selector));
    const roots = all.filter(el => !all.some(other => other !== el && other.contains(el)));
    return roots.length ? roots[roots.length - 1] : null;
}"""


def find_topmost_dialog(scope):
    """The dialog the user is actually looking at, or None — root element, not a
    part of one. See _TOPMOST_DIALOG_JS above for why both of those matter."""
    try:
        handle = scope.evaluate_handle(_TOPMOST_DIALOG_JS, ", ".join(MODAL_SELECTORS))
    except Exception:
        return None
    element = handle.as_element()
    if element is None or not _safe_visible(element):
        return None
    return element


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


# Controls a navigator may be shown. Lives here because two callers need it and
# because "what can this scope address" is a DOM question, not an adapter one.
PRESSABLE = 'button, [role="button"], a[href]'
TYPEABLE = 'textarea, input[type="text"], input:not([type]), [contenteditable="true"]'


def addressable_controls(*surfaces, selector: str = PRESSABLE):
    """Everything of `selector` on the surfaces given, as (descriptions, elements).

    Index-aligned and deliberately split: the first list is what a model is
    shown, the second is what the caller clicks. The model never receives an
    element and never names one — it picks a number out of what it was offered,
    which is what makes its answer unable to reach a control nobody offered.

    Several surfaces because a layer on top is usually the problem: hh's confirm
    over its own profile survey is a different dialog with its own dismiss
    control, and no address belonging to the surface underneath can reach it.

    Duplicates by (label, address) are dropped. hh renders the same address more
    than once on a page — the reason dom.py exists at all — and a menu listing
    the same button three times asks the model to choose between identical
    options.
    """
    seen, descriptions, elements = set(), [], []
    for surface in [s for s in surfaces if s is not None]:
        for el in iter_visible(surface, selector):
            try:
                label = (el.inner_text() or "").strip()
                data_qa = el.get_attribute("data-qa")
                if not label:
                    # Icon-only controls carry no text, and a close X usually is
                    # one — so without this the single most useful option is the
                    # least legible one on the menu.
                    label = (el.get_attribute("aria-label")
                             or el.get_attribute("placeholder") or data_qa or "")
                key = (label, data_qa)
                if key in seen:
                    continue
                seen.add(key)
            except Exception:
                continue
            descriptions.append({"index": len(descriptions),
                                 "label": label[:80], "data_qa": data_qa})
            elements.append(el)
    return descriptions, elements
