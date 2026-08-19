"""hh tells us which resumes it is offering; we stopped needing to read the list.

The chooser reorders between openings and the collapsed card carries no id, so
for a while the plan was to expand it and read the rows. That turned out to be
unnecessary: the roster is in the page's own initial state, keyed by resume id,
on every page stage from the vacancy page onward.

What is pinned here is the shape of that read, and three refusals — an
ambiguous title match, a page with several vacancies, and a store whose title
arrives as fragments rather than a string. Each of them is a place where
guessing produces a plausible answer, and a plausible answer here means
applying with the wrong resume under someone's name.

Fixtures are synthetic on purpose. A real store holds a person's resume titles
and ids; baking one into a shipped test would put it in the public history for
good — the very thing the repository's scrub pass exists to prevent.

Run:  python3 tests/test_resume_roster.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.hh.resume_roster import (  # noqa: E402
    chooser_texts, ensure_selected, intended_hash, is_chooser_button,
    match_selected, modal_is_chooser_only, option_for, resume_roster,
    selected_option_hash, without_chooser,
)


def _store(vacancy_id="100000001", **over):
    """The four keys hh ships per vacancy, in their real nesting."""
    node = {
        "resumes": {
            "111111111": {
                "id": "111111111", "hash": "h" * 38,
                "title": [{"string": "First Role"}],
                "_attributes": {"hasPublicVisibility": True},
            },
            "222222222": {
                "id": "222222222", "hash": "g" * 38,
                "title": [{"string": "Second Role"}],
                "_attributes": {"hasPublicVisibility": False},
            },
        },
        "usedResumeIds": [],
        "unusedResumeIds": ["111111111", "222222222"],
        "hiddenResumeIds": [],
    }
    node.update(over)
    return {"applicantVacancyResponseStatuses": {vacancy_id: node}}


def run():
    failures = []

    def check(label, condition):
        print(f"  {'✅' if condition else '❌'} {label}")
        if not condition:
            failures.append(label)

    # ── The read ──────────────────────────────────────────────────────────
    roster = resume_roster(_store(), "100000001")
    check("both resumes come back, with their ids", {r["id"] for r in roster} ==
          {"111111111", "222222222"})
    check("the hash comes back — it is the /resume/<hash> segment",
          all(len(r["hash"]) == 38 for r in roster))
    check("a title arriving as fragments reads as its text",
          {r["title"] for r in roster} == {"First Role", "Second Role"})
    check("publish visibility is reported per resume — this is what hh's own "
          "'change your visibility to apply' warning is about",
          {r["id"]: r["visible"] for r in roster} ==
          {"111111111": True, "222222222": False})

    used = resume_roster(_store(usedResumeIds=["111111111"]), "100000001")
    check("a resume already used for this vacancy says so",
          [r["used"] for r in used if r["id"] == "111111111"] == [True])
    hidden = resume_roster(_store(hiddenResumeIds=["222222222"]), "100000001")
    check("…and so does a hidden one",
          [r["hidden"] for r in hidden if r["id"] == "222222222"] == [True])

    # ── Addressing ────────────────────────────────────────────────────────
    check("the wrong vacancy id returns nothing rather than another vacancy's list",
          resume_roster(_store(), "999999999") == [])

    two = _store()
    two["applicantVacancyResponseStatuses"]["100000002"] = \
        two["applicantVacancyResponseStatuses"]["100000001"]
    check("with no vacancy id and two vacancies on the page, it declines — "
          "guessing which one is being applied to is how the wrong resume gets picked",
          resume_roster(two, None) == [])
    check("…with exactly one, it does not need to be told which",
          len(resume_roster(_store(), None)) == 2)

    # The popup carries the same four keys, but only while it exists.
    popup = {"vacancyResponsePopup": {"vacancy":
             _store()["applicantVacancyResponseStatuses"]["100000001"]}}
    check("the popup's copy is a fallback, not the address",
          len(resume_roster(popup, None)) == 2)

    check("no state at all is empty, not an exception", resume_roster({}, "1") == [])
    check("…and neither is a state with no roster in it",
          resume_roster({"somethingElse": {}}, "1") == [])

    # ── Matching what the chooser shows ───────────────────────────────────
    check("the chooser's title resolves to exactly one resume",
          (match_selected(roster, "Second Role") or {}).get("id") == "222222222")
    check("surrounding whitespace does not prevent a match",
          (match_selected(roster, "  Second Role  ") or {}).get("id") == "222222222")
    check("a title matching nothing is no match, not the first entry",
          match_selected(roster, "Some Other Role") is None)
    check("no chooser on the page is no match", match_selected(roster, None) is None)

    same = resume_roster(_store(resumes={
        "111111111": {"id": "111111111", "hash": "h" * 38,
                      "title": "Same Name", "_attributes": {}},
        "222222222": {"id": "222222222", "hash": "g" * 38,
                      "title": "Same Name", "_attributes": {}},
    }), "100000001")
    check("two resumes sharing a title resolve to NEITHER — hh does not forbid "
          "the duplicate, and picking one of them is picking at random",
          match_selected(same, "Same Name") is None)

    # ── hh's own visibility vocabulary ────────────────────────────────────
    acc = resume_roster({"applicantVacancyResponseStatuses": {"1": {
        "resumes": {"1": {"id": "1", "hash": "h" * 38, "title": "R",
                          "accessType": [{"string": "direct"}], "_attributes": {}}},
        "usedResumeIds": [], "unusedResumeIds": ["1"], "hiddenResumeIds": []}}}, "1")
    check("accessType comes back unwrapped — `direct` is hh's own name for "
          "'reachable only by a direct link', confirmed on the resume's own page",
          acc[0]["access"] == "direct")

    # ── Choosing, without a model ─────────────────────────────────────────
    class FakeScope:
        """A chooser that reports one title and refuses to expand."""
        def __init__(self, title): self.title, self.clicks = title, 0
        def query_selector_all(self, sel): return [_Cell(self.title)]
        def query_selector(self, sel): return None
        def wait_for_timeout(self, ms): pass

    class _Cell:
        """A chooser card with no expandable ancestor — the card is there, the
        thing that opens the list is not."""
        def __init__(self, title): self.title = title
        def query_selector(self, sel): return _Text(self.title)
        def click(self): pass
        def evaluate_handle(self, js): return _Handle()

    class _Handle:
        def as_element(self): return None

    class _Text:
        def __init__(self, t): self.t = t
        def inner_text(self): return self.t

    # The selection is ALWAYS made, even when the card already reads right.
    # Nothing in the page carries the choice — no radio, no hidden field — so hh
    # holds it in its own state and fills it in on submit, and that state comes
    # from a selection EVENT. Observed live 2026-08-18: card showed the intended
    # resume, nothing was clicked, and the application went out with the other
    # one. So a card that reads correctly is narration, never a shortcut.
    want = roster[1]["hash"]  # Second Role
    r = ensure_selected(FakeScope("Second Role"), roster, want)
    check("an already-correct card still goes through the chooser — it is a "
          "display, not the submitted value",
          r["status"] == "cannot")
    _mod_src = (Path(__file__).parent.parent / "adapters" / "hh" / "resume_roster.py") \
        .read_text(encoding="utf-8")

    # A resume hh lists as hidden FOR THIS VACANCY cannot be applied with, and
    # the page says so itself. Refusing is right; refusing without naming the
    # cause leaves a person with nothing to act on, so the reason carries it.
    hidden_store = _store(hiddenResumeIds=["222222222"])
    hidden_roster = resume_roster(hidden_store, "100000001")
    r_hidden = ensure_selected(FakeScope("Second Role"), hidden_roster,
                               hidden_roster[1]["hash"])
    check("a hidden intended resume is refused, not worked around",
          r_hidden["status"] == "cannot")
    check("…and the refusal names visibility, so it can be acted on",
          "visibility" in (r_hidden.get("reason") or ""))

    _es = _mod_src[_mod_src.index("def ensure_selected"):]
    check("…and the already-correct case carries no early return of its own",
          "already_showing" in _es
          and "return {\"status\": \"already\", " not in _es)

    r = ensure_selected(FakeScope("First Role"), roster, want)
    check("a chooser that will not expand is reported, never guessed at — every "
          "wrong resolution ends as an application under someone's name",
          r["status"] == "cannot" and r["reason"] == "chooser has no expander")

    r = ensure_selected(FakeScope("First Role"), roster, "z" * 38)
    check("an intended resume hh is not offering is reported as such",
          r["status"] == "cannot" and r["reason"] == "no intended resume on offer")

    class NoChooser(FakeScope):
        def query_selector_all(self, sel): return []
    check("a page with no chooser is 'unknown', not a failure",
          ensure_selected(NoChooser(""), roster, want)["status"] == "unknown")

    # ── The expanded list addresses rows by hash, not by title ────────────
    # Captured live 2026-08-18: each row is
    #   <label role="option" data-magritte-select-option="<hash>" aria-selected=…>
    # so the resume is named on the row itself, three times over. Title matching
    # was the weak half of this module — hh does not forbid two resumes sharing a
    # title — and none of it is needed once the row carries the hash.
    class Opt:
        def __init__(self, h, sel): self.h, self.sel = h, sel
        def get_attribute(self, n):
            return self.h if n == "data-magritte-select-option" else ("true" if self.sel else "false")

    class List:
        def __init__(self, opts): self.opts = opts
        def query_selector_all(self, sel): return self.opts
        def query_selector(self, sel):
            want = sel.split('"')[1]
            return next((o for o in self.opts if o.h == want), None)

    lb = List([Opt("a" * 38, False), Opt("b" * 38, True)])
    check("a row is found by the resume's own hash",
          option_for(lb, "a" * 38) is not None)
    check("a hash that is not on offer finds no row",
          option_for(lb, "z" * 38) is None)
    check("no hash asks for nothing rather than for the first row",
          option_for(lb, "") is None and option_for(lb, None) is None)
    check("what hh has selected is read from the list, not inferred from the card",
          selected_option_hash(lb) == "b" * 38)

    # ── Pauses where hh is watching ───────────────────────────────────────
    # A dropdown opened and answered in the same tick, then a submit fired
    # instantly after, is a shape no person produces. Both pauses are randomised
    # rather than fixed, because a constant delay is its own fingerprint.
    import adapters.hh.resume_roster as rr
    check("both pauses fall in the half-second-to-second band",
          rr._PAUSE_MIN_MS == 500 and rr._PAUSE_MAX_MS == 1000)
    seen = set()
    class Clock:
        def wait_for_timeout(self, ms): seen.add(ms)
    for _ in range(40):
        rr._human_pause(Clock())
    check("…and they vary, rather than being one constant hh could measure",
          len(seen) > 1 and all(500 <= ms <= 1000 for ms in seen))

    # Pinned by reading the source: the pauses must sit on the interaction path
    # only. When hh already preselected the right resume nothing is opened and
    # nothing clicked, so a pause there would be time spent looking busy.
    src = (Path(__file__).parent.parent / "adapters" / "hh" / "resume_roster.py") \
        .read_text(encoding="utf-8")
    already_branch = src[src.index('already_showing ='):src.index('wanted = next(')]
    check("the already-correct case is narration only, not an early return",
          "return" not in already_branch)
    calls = [ln for ln in src.splitlines()
             if "_human_pause(page)" in ln and not ln.lstrip().startswith("def ")]
    check("both clicks on the interaction path are followed by one",
          len(calls) == 2)

    # ── What must never reach a model ─────────────────────────────────────
    # A modal still goes to the model when it also holds a cover-letter field.
    # The chooser is resolved by then, so its title and its card are noise — and
    # not harmless noise: a resume title reads like a claim about the candidate
    # inside a prompt asking which button to press, and the card is role=button,
    # so it arrives as a choice the model can pick and cannot act on.
    class Cell:
        def __init__(self, text, is_resume=True):
            self.text, self.is_resume = text, is_resume
        def query_selector(self, sel): return object() if self.is_resume else None
        def inner_text(self): return self.text

    class Modal:
        def __init__(self, cells): self.cells = cells
        def query_selector_all(self, sel): return self.cells

    modal = Modal([Cell("Some Role Title\nMoscow"), Cell("unrelated", is_resume=False)])
    lines = chooser_texts(modal)
    check("the chooser's own lines are identified, and only its own",
          lines == ["Some Role Title", "Moscow"])
    check("…and come back out of the modal text, order otherwise untouched",
          without_chooser("Отклик на вакансию\nSome Role Title\nMoscow\nОткликнуться", lines)
          == "Отклик на вакансию\nОткликнуться")
    check("text with no chooser in it is returned as it was",
          without_chooser("a\nb", []) == "a\nb")
    check("the chooser card is recognised as not-a-button",
          is_chooser_button(Cell("x")) and not is_chooser_button(Cell("x", is_resume=False)))

    # ── Intent comes out of the URL the profile actually runs ─────────────
    # The hash is in `?resume=<hash>` — reading it there is one regex. It used to
    # be found by matching the whole URL against hh_resumes.json to reach that
    # file's `uuid`, and one entry in that file carries an EMPTY search_url: a
    # profile pointing at THAT resume resolved to nothing, and the chooser modal
    # fell through to the model. Cost a live run.
    import tempfile, os as _os
    with tempfile.TemporaryDirectory() as d:
        Path(d, "search_urls.txt").write_text(
            "https://hh.ru/search/vacancy?resume=" + "a" * 38 + "&from=resumelist\n",
            encoding="utf-8")
        check("the intended resume is read straight out of the search URL",
              intended_hash(d, None) == "a" * 38)
        # The empty-search_url entry that broke it, kept as a fixture.
        Path(d, "resumes.json").write_text(json.dumps(
            [{"uuid": "a" * 38, "search_url": ""},
             {"uuid": "b" * 38, "search_url": "https://example.invalid/other"}]),
            encoding="utf-8")
        check("…and an empty search_url in the roster file cannot break it any more",
              intended_hash(d, Path(d, "resumes.json")) == "a" * 38)
        Path(d, "search_urls.txt").write_text(
            "https://hh.ru/search/vacancy?resume=" + "a" * 38 + "\n"
            "https://hh.ru/search/vacancy?resume=" + "b" * 38 + "\n", encoding="utf-8")
        check("two different resumes among a profile's searches is no answer, "
              "not the first one",
              intended_hash(d, None) is None)
        Path(d, "search_urls.txt").write_text("https://hh.ru/search/vacancy?text=pm\n",
                                              encoding="utf-8")
        check("a URL with no resume in it falls back rather than inventing one",
              intended_hash(d, None) is None)

    # ── A modal holding only the chooser is not a form ────────────────────
    # This is the classic apply path, and getting it wrong is what sent fill_form
    # after a question that does not exist.
    class Dlg:
        def __init__(self, has_title=True, extra=()):
            self.has_title, self.extra = has_title, set(extra)
        def query_selector(self, sel):
            if sel == '[data-qa="resume-title"]':
                return object() if self.has_title else None
            return object() if sel in self.extra else None

    check("chooser and nothing else — handled without a model",
          modal_is_chooser_only(Dlg()))
    check("chooser beside a cover field is a form layer, letter and all",
          not modal_is_chooser_only(Dlg(extra=('textarea',))))
    check("chooser beside employer questions is a form layer too",
          not modal_is_chooser_only(Dlg(extra=('[data-qa="task-question"]',))))
    check("no chooser at all is not a chooser-only modal",
          not modal_is_chooser_only(Dlg(has_title=False)))

    # ── Nothing looks for the chooser before the apply click ──────────────
    # It cannot exist until "Откликнуться" is pressed. Probing for it earlier
    # spends a DOM query per vacancy page on something never there, and a client
    # that hunts for elements a person could not be looking at reads as a script.
    # Pinned by reading the source: this is about where a call sits, and there is
    # no object to ask.
    adapter_src = (Path(__file__).parent.parent / "adapters" / "hh" / "adapter.py") \
        .read_text(encoding="utf-8")
    # EVERY place that meets a modal after the apply click must check the chooser
    # first. Guarding only the layer loop left the pre-loop call open, and the
    # chooser modal — which opens the instant Apply is clicked — went to the
    # dismisser, which asked the model, which pressed the primary button. An
    # application went out with a CV the profile does not use. So the count is
    # what is pinned: one guard per dismisser call site, no exceptions.
    dismissals = [ln for ln in adapter_src.splitlines()
                  if "_dismiss_blocking_modal(" in ln and "def " not in ln]
    guards = [ln for ln in adapter_src.splitlines()
              if "_handle_resume_chooser(" in ln and not ln.strip().startswith("def ")]
    check(f"every dismisser call site is guarded by the chooser step "
          f"({len(guards)} guards / {len(dismissals)} dismissals)",
          len(guards) == len(dismissals) and len(guards) >= 3)
    # The decision must be acted on. It used to live inside a method whose return
    # value the loop discards, which is how "stop" became "carry on to the model".
    check("…and every outcome it can return is handled at that call site",
          '"blocked"' in adapter_src and '"submitted"' in adapter_src
          and "skipped_resume_unresolved" in adapter_src)
    # Everything this module exposes must be mentioned only inside that one
    # method — import line included, since an import at module level is how a
    # second caller starts.
    body_start = adapter_src.index("def _handle_resume_chooser")
    body_end = adapter_src.index("def _dismiss_blocking_modal", body_start)
    outside = adapter_src[:body_start] + adapter_src[body_end:]
    # `resume_roster` is also the module's own name, so the call is what is
    # checked — `from adapters.hh.resume_roster import ...` is not a second use.
    for name in ("resume_roster", "selected_resume_title", "read_page_state",
                 "ensure_selected", "intended_hash"):
        check(f"`{name}()` is called nowhere outside that method",
              f"{name}(" not in outside)
    # The three noise-strippers are the exception and belong where the model is
    # spoken to, which is the dismisser itself.
    for name in ("chooser_texts", "without_chooser", "is_chooser_button"):
        check(f"`{name}` is used where the prompt is built, and only there",
              adapter_src.count(name) <= 2
              and name in adapter_src[adapter_src.index("def _dismiss_blocking_modal"):])

    # ── Nothing this module prints carries an emoji ───────────────────────
    # The app renders these lines in its own terminal, and that terminal bans
    # them outright. The engine's older code is full of them; nothing added here
    # may be. Checked mechanically because "I'll remember" already failed once.
    import re as _re
    EMOJI = _re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
    for path in ("adapters/hh/resume_roster.py",):
        body = (Path(__file__).parent.parent / path).read_text(encoding="utf-8")
        check(f"{path} prints no emoji", not EMOJI.search(body))
    adapter_lines = [ln for ln in adapter_src.splitlines()
                     if "Resume" in ln and "_say" in ln]
    check("and neither do the lines this feature adds to the app's terminal",
          adapter_lines and not any(EMOJI.search(ln) for ln in adapter_lines))

    # ── The expander is a toggle, not a "make sure it is open" ───────────
    # Pressing it while the list is already open closes it, and the open list's
    # own overlay covers the card, so the press cannot land: the run of
    # 2026-08-19 spent thirty seconds retrying one click, dragging the row
    # sideways, and lost the vacancy to a timeout. Playwright named the
    # interceptor itself — `[data-qa="drop-base"]` — and the snapshots either
    # side of the failure showed the list shut before and open after.
    class _OpenList:
        """A page whose chooser list is already expanded."""
        def __init__(self):
            self.card_clicks = 0; self.row_clicks = 0; self.keys = []
            self.keyboard = self
        def press(self, key): self.keys.append(key)
        def query_selector(self, sel):
            if "listbox" in sel or "drop-base" in sel:
                return _Visible()
            if "magritte-select-option" in sel:
                return _Row(self)
            return None
        def query_selector_all(self, sel): return [_ClickCounted(self)]
        def wait_for_timeout(self, ms): pass

    class _Visible:
        def is_visible(self): return True

    class _Row:
        def __init__(self, owner): self.owner = owner
        def click(self): self.owner.row_clicks += 1

    class _ClickCounted:
        def __init__(self, owner): self.owner = owner
        def query_selector(self, sel): return _Text("Second Role")
        def click(self): self.owner.card_clicks += 1
        def evaluate_handle(self, js): return _CardHandle(self.owner)

    class _CardHandle:
        def __init__(self, owner): self.owner = owner
        def as_element(self): return _ClickCounted(self.owner)

    _open = _OpenList()
    ensure_selected(_open, roster, roster[1]["hash"], page=_open)
    check("an already-open list is read, not re-opened by pressing the card",
          _open.card_clicks == 0)
    check("…and the row inside it is still the thing that gets clicked",
          _open.row_clicks == 1)
    # Choosing is not finished while the portal is still covering the page: the
    # next click on this modal is its submit button, and on 2026-08-19 that
    # button sat under an open list and burned the whole actionability budget.
    check("choosing ends with the list shut, not merely chosen",
          "Escape" in _open.keys)

    # ── One decision per modal ────────────────────────────────────────────
    # Every call site stays guarded — dropping one is how an application went
    # out with a resume this profile did not choose. But a repeat visit to a
    # modal already settled must confirm by READING. Clicking again expands a
    # chooser that was already answered, on a card hh has since re-rendered:
    # the click lands on nothing, Playwright drags the row around for its whole
    # actionability budget, and the timeout comes back as "cannot" — a stopped
    # vacancy caused entirely by asking twice. Seen live 2026-08-18.
    _settled = adapter_src[adapter_src.index("_resume_settled = getattr")
                           if "_resume_settled = getattr" in adapter_src
                           else adapter_src.index('settled = getattr(self, "_resume_settled"'):]
    _settled = _settled[:_settled.index("outcome = ensure_selected")]
    check("a settled modal is confirmed by reading, never by selecting again",
          "ensure_selected" not in _settled and 'return "fixed"' in _settled)
    check("…and what it reads is the selection itself, not only the card",
          "selected_option_hash" in _settled)
    check("the settle is remembered against the vacancy it was made for",
          '"vid": vid' in adapter_src and '"hash": want' in adapter_src)

    print()
    if failures:
        print(f"❌ {len(failures)} failed")
        for f in failures:
            print(f"   · {f}")
        return 1
    print("✅ all passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
