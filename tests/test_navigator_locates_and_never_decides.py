"""The navigator says WHERE a control is. It is never asked what to do.

hh's own profile surveys are the first surface the claw may ask about, and they
were chosen first for two reasons. Their vocabulary is closed — 47 keys across
four families, captured from a real page on 2026-08-27 — so the model can be
told what it is looking at instead of working it out. And being wrong there is
worst: everywhere else in this loop a bad answer costs an application, here it
edits the person's real CV. That already happened once, on 2026-08-11, when an
unrecognised survey was handed to the model as "which button continues" and the
answer was Save.

The keys below are the fixture, committed, so this runs everywhere. When the
original capture happens to be on disk it is checked against them — the other
way round from the roster corpus test, which depends on a gitignored corpus and
has been failing since that corpus lost two of its captures.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from adapters.hh.navigator import make_navigator, knows, _CONTEXT, _PURPOSE
        from adapters.hh.dom import SAVE_SHAPED, DATA_COLLECTOR_CLOSE
        from utils.navigation import NODES, jam, set_navigator
        from utils.call_ledger import CallLedger, set_ledger

# ── the captured vocabulary, as a committed fixture ──────────────────────────
FAMILIES = ("workFormats", "preferredWorkArea", "salaryMismatch", "achievements")
SAVES = ("salaryMismatch.editor.save", "achievements.examples.append")
EXITS = ("achievements.preview.close",)
NO_SKIP = ("skip", "later", "dismiss", "notNow")

_CAPTURE = _ENGINE / ("debug_screenshots/session_20260827_132212/"
                      "08_Product Manager/03_layer0_00_presented.html")
if _CAPTURE.exists():
    import re
    keys = set(re.findall(r"additionalDataCollector\.([A-Za-z.]+)", _CAPTURE.read_text(
        encoding="utf-8", errors="replace")))
    check("the fixture's four families are the ones the real page shipped",
          {k.split(".")[0] for k in keys} == set(FAMILIES))
    check("the fixture's saving controls are the ones the real page shipped",
          all(s in keys for s in SAVES))
    check("and the survey still ships no skip of any kind — the reason closing "
          "was never a choice between two buttons",
          not any(w.lower() in k.lower() for k in keys for w in NO_SKIP))
else:
    print("  SKIP  the 2026-08-27 capture is not on this machine "
          "(debug_screenshots/ is gitignored)")

# ── the question is a location, never a decision ─────────────────────────────
asked = {}


class _Agent:
    def __init__(self, answer):
        self.answer = answer

    def locate_control(self, purpose, context, candidates):
        asked.update(purpose=purpose, context=context, candidates=candidates)
        return self.answer


nav = make_navigator(_Agent(1))
CANDIDATES = [
    {"index": 0, "label": "Сохранить", "data_qa": "additionalDataCollector-save"},
    {"index": 1, "label": "", "data_qa": "additional-data-collector__popup-close"},
]
nav("data_collector_close", "would not close", CANDIDATES)

check("the model is asked to pick a control, not to decide what happens",
      "pick the control" in asked["purpose"] and "clos" in asked["purpose"])
check("and told not to save anything to the CV", "without saving" in asked["purpose"])
ctx = asked["context"].lower()
check("the context says what the surface is, so the model is not guessing at it",
      "not part of the job application" in ctx)
check("it says every one of these writes to a real CV", "writes to the person's real cv" in ctx)
check("it says there is no skip — the fact that made closing the only way out",
      "no skip button" in ctx)
check("and it names the second dialog, which is what both real failures were",
      "second dialog" in ctx and "underneath" in ctx)
check("nothing in the context asks the model what to do",
      "what should" not in ctx and "decide" not in ctx)

# ── which nodes have directions, and which deliberately do not ───────────────
WITH_DIRECTIONS = {"data_collector_close", "add_cover_button", "cover_input"}
check("the closed-vocabulary node has directions", knows("data_collector_close"))
check("so do the two where letters have actually been lost",
      knows("add_cover_button") and knows("cover_input"))
check(f"and no others, so every remaining node is left as jammed as it was "
      f"(has directions: {sorted(n for n in NODES if knows(n))})",
      {n for n in NODES if knows(n)} == WITH_DIRECTIONS)

# The open-vocabulary half. "None of these" has to be an allowed answer, or a
# claw with no way out picks the closest-looking control on a real employer's
# form — which is a guess wearing the clothes of a decision.
for node in ("add_cover_button", "cover_input"):
    purpose = _PURPOSE[node]
    check(f"{node}: refusing is offered as an answer", "or none" in purpose)
    ctx = _CONTEXT[node].lower()
    check(f"{node}: the context says the application is already submitted, "
          f"so the cost of refusing is named", "already" in ctx)
    check(f"{node}: nothing asks the model what to do",
          "what should" not in ctx and "decide" not in ctx)
check("the letter-bearing nodes say what a wrong pick costs",
      "real employer" in _CONTEXT["add_cover_button"]
      and "wrong box" in _CONTEXT["cover_input"])
check("a node with no directions returns nothing rather than improvising",
      make_navigator(_Agent(0))("action_button", "", CANDIDATES) is None)

# ── the answer is an index into what was offered, and is checked ─────────────
def ask(answer, candidates=CANDIDATES):
    led = CallLedger()
    set_ledger(led)
    set_navigator(make_navigator(_Agent(answer)))
    try:
        return jam("data_collector_close", "x", candidates=candidates)
    finally:
        set_navigator(None)
        set_ledger(None)


check("a valid index comes back", ask(1) == 1)
check("an index outside what was offered is discarded", ask(7) is None)
check("a negative index is discarded", ask(-1) is None)
check("a non-integer answer is discarded", ask("popup-close") is None)
check("a boolean is not an index, even though Python says True == 1", ask(True) is None)
check("no answer is no answer", ask(None) is None)
check("with no candidates the navigator is not asked at all", ask(1, []) is None)


def _raises(*a, **k):
    raise RuntimeError("provider down")


set_navigator(_raises)
try:
    out = jam("data_collector_close", "x", candidates=CANDIDATES)
finally:
    set_navigator(None)
check("a navigator that fails leaves the claw exactly as stuck as it was", out is None)

# ── the caller refuses a saving control even when the model picks it ─────────
adapter_src = (_ENGINE / "adapters/hh/adapter.py").read_text(encoding="utf-8")
check("the pick is vetoed against the surveys' own saving labels",
      "any(w in label for w in SAVE_SHAPED)" in adapter_src)
check("and checked to be inside hh's own survey before anything is clicked",
      "is_in_data_collector(chosen)" in adapter_src)
check("the veto is built from what the real page called those controls",
      all(w in " ".join(SAVE_SHAPED) for w in ("сохранить", "добавить в мои достижения")))
check("a refusal is said out loud rather than swallowed",
      "refused the navigator's pick" in adapter_src)
check("and closing is only reported once the survey is actually gone",
      "if find_visible(page, DATA_COLLECTOR_MARKER) is None:" in adapter_src)

# ── the claw still tries its own addresses first ─────────────────────────────
check("canon runs before anyone is asked — the close address is a cascade, not a call",
      isinstance(DATA_COLLECTOR_CLOSE, list) and len(DATA_COLLECTOR_CLOSE) >= 2)
check("the jam is what the navigator hangs off, not a new path",
      'jam("data_collector_close"' in adapter_src)

# ── the budget, which is a ceiling on runaway and not a throttle ─────────────
# A site that renamed one address makes every vacancy ask once, and THAT run
# should finish: abandoning a person's applications to save a few cents is the
# wrong trade. What the budget catches is several nodes broken at once, each
# vacancy asking three or four times, a bill compounding while nobody watches.
from utils.navigation import navigator_spend

set_navigator(make_navigator(_Agent(0)), budget=2)
try:
    spent = [jam("data_collector_close", "x", candidates=CANDIDATES) for _ in range(4)]
    spend = navigator_spend()
finally:
    set_navigator(None)
check("the first questions inside the budget are asked", spent[:2] == [0, 0])
check("and everything past it is not", spent[2:] == [None, None])
check("the count stops at the budget rather than running on", spend == (2, 2))

set_navigator(make_navigator(_Agent(0)))
try:
    unlimited = [jam("data_collector_close", "x", candidates=CANDIDATES) for _ in range(5)]
    spend = navigator_spend()
finally:
    set_navigator(None)
check("no budget means no ceiling — a run with no navigator has neither",
      unlimited == [0] * 5 and spend == (5, 0))

check("the count resets with each registration, so a budget is per run",
      navigator_spend() == (0, 0))

adapter_src2 = (_ENGINE / "adapters/hh/adapter.py").read_text(encoding="utf-8")
check("the loop stops on a spent budget rather than the jam doing it",
      'termination_reason = "navigator_budget_spent"' in adapter_src2)
check("and says why, which is the difference from a run that quietly costs triple",
      "stopping rather than paying to relearn the same thing" in adapter_src2)

# The click belongs to the path that was already going to click it. Doing it in
# the navigator branch too is how a chat gets two messages instead of one.
chat_src = (_ENGINE / "adapters/hh/handlers/chat.py").read_text(encoding="utf-8")
_branch = chat_src[chat_src.index('jam("add_cover_button"'):][:900]
check("the cover control is handed to the existing path, not clicked twice",
      "add_cover = elements[picked]" in _branch and ".click(" not in _branch)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
