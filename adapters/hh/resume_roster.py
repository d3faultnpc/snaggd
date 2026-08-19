"""Which resumes hh is offering for this vacancy, and which one is selected.

The problem this exists for: applying on hh routinely shows a resume chooser —
inside the response modal, and (found 2026-08-18) as a plain section of the
application page, with no form, no dialog and no modal wrapper around it. The
list REORDERS between openings, so choosing by position is not merely fragile,
it is wrong. And nothing in the collapsed chooser identifies a resume: the card
carries a title and a photo, no id, and the surrounding form holds only `_xsrf`.

Reading the expanded list was the obvious next step and it is not needed. hh
ships the whole roster in the page's own initial state:

    <template id="HH-Lux-InitialState">
      applicantVacancyResponseStatuses.<vacancyId>.resumes            {id: record}
      applicantVacancyResponseStatuses.<vacancyId>.unusedResumeIds    [id, ...]
      applicantVacancyResponseStatuses.<vacancyId>.usedResumeIds      [id, ...]
      applicantVacancyResponseStatuses.<vacancyId>.hiddenResumeIds    [id, ...]

Verified across 16 captures over two days and every page stage — vacancy page,
after the apply click, each form layer, and post-apply. `vacancyResponsePopup.
vacancy.*` carries the same four keys but only exists while the popup does, so
it is a fallback rather than the address.

Each record carries `id`, `hash` (the `/resume/<hash>` segment), `title`,
`photoUrls`, and `_attributes.hasPublicVisibility` — which is what the page's
own `[data-qa="hidden-resume-warning"]` is about.

Where the paid boundary is, and where it deliberately is not
------------------------------------------------------------
Working with the chooser is for EVERY user, free and anonymous included. hh puts
it in front of anyone whose account holds more than one resume, regardless of
what they pay us — so treating it as a paid capability would mean shipping a
free tier that applies with a resume the person did not choose. That is not a
feature boundary, it is a defect with a price tag on it.

What is paid is holding more than one search profile, and that gate already
exists in the app (ProfileTab's MULTI_PROFILE_TIERS). Each profile names exactly
one resume through its search URL, so a free user has one intended resume and
this module keeps hh on it; a paying user has several profiles and the same
mechanism runs per profile, unchanged.

So there is no tier check here, and there must not be one: this engine is MIT and
has no notion of tiers anywhere — the app is what knows who is paying. A check
here would put a commercial rule in a public repository and, worse, would make
correctness conditional on billing.

Nothing here runs before the apply click
----------------------------------------
The chooser cannot exist until "Откликнуться" has been pressed, so looking for
it earlier costs DOM queries on every vacancy page for something that is never
there — and a client that probes for elements a person could not be looking at
is a client that reads as a script. The only caller is the post-apply modal
path; `tests/test_resume_roster.py` pins that rather than trusting this
paragraph.

What this module does NOT do: change the selection. Clicking a row means
expanding the chooser, and the expanded list has never been captured. Reporting
the mismatch is the honest half; acting on it waits for that capture.
"""
import json
import random
from typing import Optional

# Pauses around the only two clicks this module makes. hh checks for automation,
# and the shape it would see without these is unmistakable: a dropdown that opens
# and is answered in the same tick, then a submit fired instantly after. A person
# opening a list of their own CVs reads it first.
#
# Only on the path that actually touches the chooser. When hh already preselected
# the right resume nothing opens and nothing is clicked, so there is nothing to
# make human-looking and no reason to spend the time.
_PAUSE_MIN_MS, _PAUSE_MAX_MS = 500, 1000


def _human_pause(page) -> None:
    page.wait_for_timeout(random.randint(_PAUSE_MIN_MS, _PAUSE_MAX_MS))

# hh's own initial-state template. One per page, and the JSON inside is the
# whole store — `resumes` is nested, not a top-level key, which is worth stating
# because a top-level membership test says "absent" on a page that has it.
_STATE_TEMPLATE = 'template#HH-Lux-InitialState'

# Returns the raw STRING, not the parsed object. The store is ~400 KB of JSON
# with a deep graph, and handing Playwright a parsed object makes it walk and
# serialise every node across the CDP bridge. One string crosses once; json.loads
# on this side is a few milliseconds.
_STATE_JS = """() => {
    const t = document.querySelector('template#HH-Lux-InitialState');
    if (!t) return null;
    // `.content` is a DocumentFragment; textContent gives the raw JSON either way,
    // and innerHTML would hand back HTML-escaped entities to re-unescape.
    return (t.content ? t.content.textContent : t.textContent) || null;
}"""


def read_page_state(page) -> Optional[dict]:
    """The page's initial-state store, or None when it is absent or unparseable."""
    try:
        raw = page.evaluate(_STATE_JS)
        return json.loads(raw) if raw else None
    except Exception as e:
        print(f"   Couldn't read hh's page state ({e})")
        return None


def _roster_node(state: dict, vacancy_id: Optional[str]) -> Optional[dict]:
    """The node holding the four resume keys, by address rather than by search.

    Vacancy-keyed first: it is present on every captured page stage. The popup's
    copy is identical while it exists, so it is only reached when the first is
    not there — which happens on a page whose vacancy id we could not read.
    """
    statuses = (state or {}).get("applicantVacancyResponseStatuses") or {}
    if vacancy_id and str(vacancy_id) in statuses:
        return statuses[str(vacancy_id)]
    # Exactly one vacancy on the page is the ordinary case; more than one means
    # we cannot tell which is being applied to, and guessing is how the wrong
    # resume gets picked in the first place.
    if not vacancy_id and len(statuses) == 1:
        return next(iter(statuses.values()))
    popup = ((state or {}).get("vacancyResponsePopup") or {}).get("vacancy") or {}
    return popup if "resumes" in popup else None


def _title_of(record: dict) -> str:
    """`title` is a list of fragments in the store; the visible name is their text."""
    title = record.get("title")
    if isinstance(title, str):
        return title.strip()
    if isinstance(title, list):
        parts = []
        for item in title:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("string") or item.get("text") or ""))
        return "".join(parts).strip()
    return ""


def _access_of(record: dict) -> str:
    """`accessType` arrives fragment-wrapped, same shape as `title`."""
    v = record.get("accessType")
    if isinstance(v, str):
        return v
    if isinstance(v, list) and v:
        first = v[0]
        if isinstance(first, dict):
            return str(first.get("string") or "")
        return str(first)
    return ""


def resume_roster(state: dict, vacancy_id: Optional[str] = None) -> list:
    """Every resume hh offers for this vacancy, in the store's own order.

    Order is reported as it comes and must not be relied on — the rendered
    chooser reorders between openings, which is the whole reason this module
    reads ids instead of rows.

    Each entry: {id, hash, title, used, hidden, visible}.
      used    — already applied to this vacancy with it
      hidden  — hh lists it as hidden for this vacancy
      visible — the resume's own publish visibility; False is what the page's
                "change your resume's visibility to apply" warning is about
    """
    node = _roster_node(state or {}, vacancy_id)
    if not node:
        return []
    records = node.get("resumes") or {}
    used = set(map(str, node.get("usedResumeIds") or []))
    hidden = set(map(str, node.get("hiddenResumeIds") or []))
    out = []
    for rid, rec in records.items():
        if not isinstance(rec, dict):
            continue
        attrs = rec.get("_attributes") or {}
        out.append({
            "id": str(rec.get("id") or rid),
            "hash": rec.get("hash") or "",
            "title": _title_of(rec),
            "used": str(rid) in used,
            "hidden": str(rid) in hidden,
            "visible": bool(attrs.get("hasPublicVisibility")),
            # hh's own visibility vocabulary, the five-way setting on the
            # resume's own page: `direct` is "reachable only by direct link",
            # `whitelist` is "visible to selected employers". Confirmed against
            # that page 2026-08-18 for both values. `hasPublicVisibility` is the
            # boolean derived from it, and it is what the page's
            # hidden-resume-warning is about — not a separate fact.
            "access": _access_of(rec),
        })
    return out


# The chooser, in both hosts it has been seen in. Not a modal address and not a
# form address: on 2026-08-18 it was captured as a plain page section with
# neither around it. What identifies it is the cell that carries a resume title.
CHOOSER_CELL = '[data-qa="cell"]'
CHOOSER_TITLE = '[data-qa="resume-title"]'
HIDDEN_RESUME_WARNING = '[data-qa="hidden-resume-warning"]'

# The expanded list, captured live 2026-08-18. Three things about it decide how
# selection is written:
#
#   1. It is NOT inside the modal. It renders as a portal — `[data-qa="drop-base"]`
#      with its own z-index (2250) above the modal's footer (2200) — positioned
#      over and past the modal's edges. So it must be looked for on the PAGE, not
#      within the dialog, and the dialog's own outerHTML does not contain it.
#   2. Every option carries the resume hash on itself, three times over:
#      `data-magritte-select-option`, a `data-qa` suffixed with it, and a radio
#      `value`. Matching a rendered title is therefore unnecessary — and title
#      matching was the weak part, since hh does not forbid two resumes sharing one.
#   3. `aria-selected="true"` marks the current one, so what is chosen can be read
#      without inferring it from the collapsed card.
CHOOSER_LISTBOX = '[role="listbox"]'
CHOOSER_DROP_BASE = '[data-qa="drop-base"]'
CHOOSER_OPTION = '[role="option"][data-magritte-select-option]'

# The SECOND presentation of the same chooser, captured live 2026-08-19 on the
# hh-modal path. hh renders the expanded list as its own [role="dialog"] portal
# ABOVE the modal (z 2250 over 2200) — and inside it there is no magritte select
# at all: each row is a plain radio carrying the hash in `value`, beside a
# decorative radio that is what the eye actually sees.
#
# Which presentation appears is not ours to predict, so neither is assumed. The
# run that crashed on 2026-08-18 did so because option_for knew only the first
# one, found nothing, and reported "expanded list has no row" on a list that was
# right there.
#
# The real input is visually hidden behind the decorative one. Clicking it is
# exactly the `ElementHandle.check: Timeout` this file already met elsewhere, so
# what gets handed back to click is the enclosing cell — the thing a person hits.
CHOOSER_RADIO = 'input[name="resumeId"]'
CHOOSER_RADIO_ROW = 'el => el.closest(\'[data-qa="cell"]\') || el.closest("label") || el.parentElement'


def option_for(scope, resume_hash: str):
    """The expanded list's row for this resume, addressed by its own hash."""
    if not resume_hash:
        return None
    try:
        row = scope.query_selector(f'[data-magritte-select-option="{resume_hash}"]')
        if row is not None:
            return row
        radio = scope.query_selector(f'{CHOOSER_RADIO}[value="{resume_hash}"]')
        if radio is None:
            return None
        clickable = radio.evaluate_handle(CHOOSER_RADIO_ROW).as_element()
        return clickable or radio
    except Exception:
        return None


def selected_option_hash(scope) -> Optional[str]:
    """Hash of the row hh currently has selected, read from the list itself."""
    try:
        for opt in scope.query_selector_all(CHOOSER_OPTION):
            if (opt.get_attribute("aria-selected") or "").lower() == "true":
                return opt.get_attribute("data-magritte-select-option")
        # The radio presentation says it plainly, and says it better: `checked`
        # is a real property of a real form control, not an ARIA claim about one.
        radio = scope.query_selector(f'{CHOOSER_RADIO}:checked')
        if radio is not None:
            return radio.get_attribute("value")
    except Exception:
        pass
    return None


def selected_resume_title(scope) -> Optional[str]:
    """The title shown on the collapsed chooser, or None when there is no chooser.

    `scope` is a Page or a Frame — the chooser has been seen at page level and
    inside the response modal, and this must not care which.
    """
    try:
        for cell in scope.query_selector_all(CHOOSER_CELL):
            title_el = cell.query_selector(CHOOSER_TITLE)
            if title_el is None:
                continue
            text = (title_el.inner_text() or "").strip()
            if text:
                return text
    except Exception as e:
        print(f"   Couldn't read the resume chooser ({e})")
    return None


def match_selected(roster: list, selected_title: Optional[str]) -> Optional[dict]:
    """The roster entry whose title the chooser is showing.

    Title is the only thing the collapsed card and the store share, and two
    resumes may carry the same one — so an ambiguous match is reported as no
    match rather than as the first hit. Being unable to tell which resume is
    selected is a fact worth surfacing; picking one is not.
    """
    if not selected_title:
        return None
    hits = [r for r in roster if r["title"] and r["title"] == selected_title.strip()]
    return hits[0] if len(hits) == 1 else None


def intended_hash(data_dir, resumes_path=None) -> Optional[str]:
    """Which resume this profile applies with, read out of its own search URL.

    The hash is IN the URL — `…/search/vacancy?resume=<38 chars>&…` — and that URL
    is what the profile actually runs. So the answer is one regex on
    `search_urls.txt`, and it is the same 38-char string as `hash` in the page
    roster (verified 2026-08-18).

    It was not always read this way, and the detour cost a live run: intent used
    to be derived by matching the whole URL string against `hh_resumes.json` to
    reach that file's `uuid`. One of the entries there carries an EMPTY
    `search_url`, so a profile pointing at that resume matched nothing, intent
    came back None, and the chooser modal fell through to the model. Going
    through a second file to find something already present in the first is the
    kind of indirection that only fails once it matters.

    `resumes_path` is kept for the fallback below and for callers that still pass
    it. None when the profile's URLs carry no resume, or carry two different ones
    — an ambiguous answer would pick a resume on the person's behalf.
    """
    import re
    from pathlib import Path
    try:
        urls = Path(data_dir, "search_urls.txt").read_text(encoding="utf-8")
    except Exception:
        return None
    found = set(re.findall(r"[?&]resume=([0-9a-f]{32,40})", urls))
    if len(found) == 1:
        return found.pop()
    if found:
        return None  # two different resumes among the profile's searches

    # No hash in any URL — fall back to the roster file's own join. A profile
    # whose search URLs predate the `resume=` parameter can still be resolved.
    if not resumes_path:
        return None
    try:
        entries = json.loads(Path(resumes_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    hits = [e for e in entries
            if (e.get("search_url") or "").strip()
            and (e["search_url"] or "").strip() in urls
            and e.get("uuid")]
    return hits[0]["uuid"] if len(hits) == 1 else None


def _list_is_open(page) -> bool:
    """True while the expanded chooser is on screen, in either presentation.

    It is a portal, drawn OUTSIDE the modal and above it, so it is looked for on
    the page rather than in the dialog — the same reason `option_for` takes the
    page. Both addresses are checked because hh ships two presentations of one
    component and which one appears is not ours to predict.
    """
    for sel in (CHOOSER_LISTBOX, CHOOSER_DROP_BASE):
        try:
            el = page.query_selector(sel)
            if el is not None and el.is_visible():
                return True
        except Exception:
            continue
    return False


def _close_list(page) -> bool:
    """Shut the expanded chooser, and say whether it is shut.

    Escape rather than a click elsewhere: a click needs a safe place to land,
    and on a modal there isn't one — the backdrop closes the whole modal.
    """
    if not _list_is_open(page):
        return True
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass
    return not _list_is_open(page)


def ensure_selected(scope, roster: list, want_hash: Optional[str], page=None) -> dict:
    """Make the chooser show the intended resume, without asking a model.

    The chooser is not a puzzle: the roster says which resumes exist, the
    collapsed card says which one is showing, and both carry a title. So the
    ordinary case — hh already preselected the right one — costs nothing at all:
    no click, no LLM call, and no unrecognised-modal branch. That case is worth
    naming because it is most of them, and because the generic dismisser used to
    spend a model call on it.

    hh's preselection is NOT stable: the same profile has been observed getting
    a different resume between openings, which is also why the rendered order
    cannot be trusted. So when it is wrong, the row is found by the title the
    roster gives for `want_hash` — never by position.

    Returns {"status": ..., "id": ..., "title": ...} with status one of:
      already   — the chooser was on the intended resume; nothing was touched
      changed   — expanded and clicked the row for the intended resume
      unknown   — no chooser on this page (the ordinary case for most pages)
      cannot    — a chooser is here and this could not resolve it safely

    `cannot` is deliberately not "click something and hope". Every way of
    resolving it wrong ends the same: an application sent under someone's name
    with a resume they did not choose.
    """
    page = page or scope
    shown = selected_resume_title(scope)
    if shown is None:
        return {"status": "unknown"}

    current = match_selected(roster, shown)
    already_showing = bool(want_hash and current and current["hash"] == want_hash)
    # NOT a shortcut. The card showing the right resume is not the same as the
    # right resume being submitted: nothing in the page carries the choice — no
    # radio, no hidden field, only `_xsrf` — so hh holds it in its own state and
    # fills it in on submit. That state comes from a SELECTION EVENT. A card we
    # never touched has produced no event, and hh submits its own default, which
    # is not always what the card displays.
    #
    # Observed 2026-08-18 on an hr-questions page: the card read the intended
    # resume, this function reported "already", nothing was clicked, and the
    # application went out with the other one. So the selection is always made
    # explicitly, and "already" survives only as narration.

    wanted = next((r for r in roster if r["hash"] == want_hash), None) if want_hash else None
    if wanted is None:
        # Either the profile names no resume, or the one it names is not on
        # offer for this vacancy. Both are facts to report, not to work around.
        return {"status": "cannot", "id": (current or {}).get("id"),
                "title": shown, "reason": "no intended resume on offer"}

    if wanted.get("hidden"):
        # hh lists it as hidden FOR THIS VACANCY, which is what the page's own
        # "change your resume's visibility to apply" warning is about. Naming
        # the cause is the whole value here: the alternative is a refusal the
        # person cannot act on.
        return {"status": "cannot", "id": wanted["id"], "title": wanted["title"],
                "reason": "the intended resume is hidden for this vacancy — "
                          "change its visibility to apply with it"}

    # Expand, then pick by hash. The list is a portal outside the modal, so it is
    # searched from `page` rather than from the dialog — see CHOOSER_LISTBOX.
    try:
        # The card is a TOGGLE, so it may only be pressed while the list is shut.
        # Pressing it with the list open does not re-open it — it closes it, and
        # the open list's own overlay sits over the card, so the press cannot
        # land at all: Playwright retried for 30 seconds, dragging the row from
        # side to side, and the vacancy died on the timeout. Read straight off
        # the failing run's snapshots (2026-08-19): the list was shut in
        # `02_after_apply_click` and open in `error`, and what intercepted the
        # click was `[data-qa="drop-base"]`, by name, in Playwright's own log.
        #
        # An open list is not a failure — it is the state the next step wants —
        # so this expands only when there is nothing to read yet.
        if _list_is_open(page):
            row = option_for(page, want_hash)
        else:
            card = None
            for cell in scope.query_selector_all(CHOOSER_CELL):
                if cell.query_selector(CHOOSER_TITLE) is not None:
                    card = cell.evaluate_handle(
                        'el => el.closest("[role=button]") || el.closest("[tabindex]") || el.parentElement'
                    ).as_element()
                    break
            if card is None:
                return {"status": "cannot", "title": shown, "reason": "chooser has no expander"}
            card.click()
            # Read the list before answering it.
            _human_pause(page)
            row = option_for(page, want_hash)
        if row is None:
            return {"status": "cannot", "title": shown,
                    "reason": "expanded list has no row for the intended resume"}
        row.click()
        # And a beat after choosing, before whatever the caller presses next —
        # on the classic path that is the apply button, one click away.
        _human_pause(page)
        # Verify, because a click that lands and changes nothing is exactly the
        # failure this path exists to avoid. Two readings, in order of strength:
        # the list's own aria-selected while it is still open, and — once it has
        # closed, which is the normal case after a pick — the title now on the
        # collapsed card. Confirmed live 2026-08-18: the list closes on click, so
        # aria-selected comes back None and the card is the only witness left.
        now = selected_option_hash(page)
        if now is not None:
            if now != want_hash:
                return {"status": "cannot", "title": shown, "reason": "selection did not take"}
        else:
            card_now = selected_resume_title(page)
            if card_now is None or card_now.strip() != wanted["title"]:
                return {"status": "cannot", "title": shown,
                        "reason": "selection did not take (card still shows the old resume)"}
        # Leave the chooser SHUT, and the rule for when it isn't is exact,
        # established by hand on a live page 2026-08-19:
        #
        #   picking a DIFFERENT resume  → hh selects it and collapses the list
        #   picking the SELECTED one    → nothing changes, so no event fires,
        #                                 and the list stays open
        #
        # The second case is the ordinary one here, because hh usually has the
        # intended resume preselected already — so the row this function is
        # asked to click is the row that is already chosen, and the click is a
        # no-op that leaves the portal covering the page. The next step is the
        # modal's own submit button, which then sits underneath it: Playwright
        # named the cover by name (`[data-qa="drop-base"] subtree intercepts
        # pointer events`) and spent its whole actionability budget dragging
        # the button about before timing out — the sideways smear seen on
        # screen, and a vacancy lost to it.
        #
        # The click still happens rather than being skipped: what must not be
        # assumed is that a displayed value is the submitted one.
        #
        # So closing is part of choosing, not a courtesy: every caller after
        # this one clicks something, and an open portal covers the page.
        _close_list(page)
        return {"status": "already" if already_showing else "changed",
                "id": wanted["id"], "title": wanted["title"]}
    except Exception as e:
        return {"status": "cannot", "title": shown, "reason": f"{type(e).__name__}: {e}"}


def chooser_texts(scope) -> list:
    """Every line the chooser contributes to a modal's text.

    Used to take it back out again. When a modal has to reach a model at all —
    it also holds a cover-letter field, or the chooser could not be resolved —
    the chooser is still a component we recognise, and handing the model its
    title and its card is handing it a decision that is already made. Worse
    than useless: the resume's own title reads like an instruction about what
    the candidate is, sitting in a prompt that asks which button to press.
    """
    out = []
    try:
        for cell in scope.query_selector_all(CHOOSER_CELL):
            if cell.query_selector(CHOOSER_TITLE) is None:
                continue
            text = (cell.inner_text() or "").strip()
            if text:
                out.extend(line.strip() for line in text.splitlines() if line.strip())
    except Exception:
        pass
    return out


def without_chooser(text: str, chooser_lines: list) -> str:
    """`text` with the chooser's own lines removed, order otherwise untouched."""
    if not chooser_lines:
        return text
    drop = set(chooser_lines)
    return "\n".join(l for l in (text or "").splitlines() if l.strip() not in drop)


def is_chooser_button(element) -> bool:
    """True when this element is the chooser card rather than a real action.

    The card is `role="button"`, so it arrives in any list of a modal's buttons
    looking like a choice the model could make. It is not one — pressing it
    expands a list the model cannot read and cannot act on.
    """
    try:
        return element.query_selector(CHOOSER_TITLE) is not None
    except Exception:
        return False


# Everything a modal can hold that means "this is a form, someone must answer it".
# The chooser's own radios are not in this list: they belong to the chooser and are
# answered by picking a resume, not by a model.
_FORM_CONTENT = (
    'textarea',
    '[data-qa^="vacancy-response-question"]',
    '[data-qa="task-question"]',
    '[data-qa="task-body"]',
)


def modal_is_chooser_only(dialog) -> bool:
    """True when this modal holds the resume chooser and nothing to answer.

    This is the classic apply path, and it is the whole reason the chooser needed
    naming: a modal with only a chooser in it is not an unfamiliar pop-up. It went
    to the model as one — `fill_form` was asked to answer a question that does not
    exist, returned nothing usable, and the run then spent thirty seconds retrying
    a click the modal's own backdrop was intercepting.

    Wrapper first, then contents: the chooser must be here, and none of the things
    that make a modal a form may be.

    `[data-qa="add-cover-letter"]` is deliberately NOT one of those things, and
    the omission is the rule rather than a gap in it. Every loop here is goal
    directed: where the cover can be skipped in the form it is skipped, and the
    loop carries on to the chat, which is where the letter actually goes and
    where the send is verified. So a button OFFERING a letter leaves this a
    chooser; only a field DEMANDING one makes it a form. The hh-modal path is
    the single exception, and it declares itself by shipping the textarea.

    What this must never key on is which select widget hh drew. Captured
    2026-08-19 on two vacancies with the identical `bottom-sheet-content`
    wrapper: one expands into magritte options, the other into named radios,
    and the one with the textarea is NOT the one with magritte. Widget and
    route vary independently, so reading the route off the widget would be
    copying a coincidence between two pages.
    """
    try:
        if dialog.query_selector(CHOOSER_TITLE) is None:
            return False
        for sel in _FORM_CONTENT:
            if dialog.query_selector(sel) is not None:
                return False
        return True
    except Exception:
        return False
