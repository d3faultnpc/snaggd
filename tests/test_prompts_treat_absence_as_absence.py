"""
Absence is not evidence.

The same defect kept appearing in different prompts: a profile that does not
state something was scored, or written about, as though it had stated the
opposite. role_type was fixed once this way, and the fix was never carried to
the two places with the same shape — the domain penalty, which quietly puts a
floor under anyone whose profession has no industry (a barista, a warehouse
worker, a nurse), and the cover letter's demand for metrics from a profile that
may hold none.

The second one was worse than a bad score: "back it with 1-2 concrete metrics"
and "never invent" cannot both be obeyed by a profile without numbers, so the
prompt was asking the model to choose which rule to break, in a letter sent to
an employer under the user's name.

Also pinned here: prompts must not defend against scaffolding the renderer no
longer writes. Those clauses describe a state that cannot occur, and a reader —
human or model — takes them as evidence it can.

Run:  venv/bin/python3 tests/test_prompts_treat_absence_as_absence.py
"""
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_SCORING = (_REPO_ROOT / "prompts" / "match_scoring.md").read_text(encoding="utf-8")
_COVER = (_REPO_ROOT / "prompts" / "cover_letter.md").read_text(encoding="utf-8")
_PARSER = (_REPO_ROOT / "onboarding" / "resume_parser.py").read_text(encoding="utf-8")

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


# ── Scoring: a profile that names no domain is not thereby a mismatch ────────
print("\nScoring treats an unstated domain as unstated:")
check("absence has a grade of its own, so it never has to be inferred",
      "THIS POSTING DOES NOT ASK ABOUT THIS AXIS" in _SCORING)
check("and that grade is spelled out as not-a-middling-verdict — `neutral` is the "
      "one most likely to be used as a polite average",
      "not a polite way to avoid deciding" in _SCORING
      and "removes the axis" in _SCORING)
check("silence in the profile is not evidence of DISTANCE — the rule that survives",
      "not thereby far from this vacancy" in _SCORING)
# What does NOT survive: an earlier version of this prompt said an axis the profile
# carries no section for is `neutral`. It read as fair and was wrong twice — it
# inflated the score by removing a real gap, and the profile is the document an
# employer reads, so what it does not state, nobody knows. Absent is missed.
check("an axis the posting asked about and the profile does not evidence is missed",
      "asked for, and not evidenced anywhere in the profile" in _SCORING)
check("and `neutral` is about the POSTING, never about the profile",
      "`neutral` is about the POSTING, never about the profile" in _SCORING)
check("the anchor has a side — it names what drove the grade, not what softened it",
      "The anchor names what DROVE the grade" in _SCORING)

# The 2026-08-18 fix carved an exception into the top band for a profile stating no
# domain, and the band table was later cleaned so domain appeared once. Both are moot:
# there are no bands and no modifiers left to keep apart. Domain is now one grade on
# one axis, which is the structural version of "counted once" — three real matches
# reached 0 because the band demoted them for the domain and the modifier then charged
# for it again, and neither of those two places exists any more.
check("no score bands survive — the model is not asked for a number at all",
      not [ln for ln in _SCORING.splitlines()
           if ln.startswith("- ") and ":" in ln and "–" in ln.split(":")[0]])
check("and no modifier arithmetic survives either",
      not re.search(r"[-+–]?\s*\d+\s*(to\s*[-+–]?\s*\d+\s*)?points", _SCORING))
check("domain is counted exactly once, on one axis, and the prompt says so",
      "there is no separate domain adjustment anywhere" in _SCORING)
check("a graded axis is not a verdict about the person",
      "Do not grade a whole person" in _SCORING)


# ── The cover letter decides about metrics once, not per sentence ────────────
print("\nThe letter decides about evidence once:")
check("there is a single branch declared before writing",
      "decide this once, before writing anything" in _COVER)
check("the no-metrics branch forbids inventing rather than requiring numbers",
      "Do not invent, imply, or gesture at a number" in _COVER)
check("it says what to use instead, so the branch is usable",
      "scope (how many, how often, for whom)" in _COVER)
check("and it names a profile without numbers ordinary, not deficient",
      "not a deficient one" in _COVER)
check("no later rule demands metrics unconditionally",
      "Back it with 1–2 concrete metrics" not in _COVER)
check("the branch is held for the whole letter",
      "never half in each" in _COVER)


# ── No prompt defends against scaffolding that is no longer written ──────────
print("\nNo defences against placeholders that no longer exist:")
check("the scorer no longer mentions the SKIPPABLE placeholder", "SKIPPABLE" not in _SCORING)
check("the letter no longer mentions EMPTY or HINT markers",
      "EMPTY or HINT" not in _COVER)
# Comment lines are excluded on purpose: the code says why the placeholder went,
# and naming it there is documentation, not a placeholder being written.
_parser_code = "\n".join(l for l in _PARSER.splitlines() if not l.lstrip().startswith("#"))
check("and the renderer no longer writes a MISSING heading into the profile",
      "MISSING — company/role/period" not in _parser_code)


# ── One vocabulary of kinds, stated as closed ────────────────────────────────
print("\nThe kind vocabulary is closed and singular:")
from onboarding.profile_frame import KIND_HEADINGS, RETIRED_KINDS  # noqa: E402

check("the extraction prompt says the list is exhaustive", "EXACTLY ONE OF" in _PARSER)
check("every kind in the frame is offered by the prompt",
      all(k in _PARSER for k in KIND_HEADINGS))
check("the prompt no longer asks for a retired kind",
      not any(f"type='{k}'" in _PARSER for k in RETIRED_KINDS))
check("other requires a label, so it cannot be a quiet resting place",
      "required for type=other" in _PARSER)
check("a label is defined as a name that stands on its own",
      "stands on its own" in _PARSER and "thing a verb acts on" in _PARSER)

_wizard = (_REPO_ROOT / "onboarding" / "wizard.py").read_text(encoding="utf-8")
check("the CLI wizard offers the frame's vocabulary rather than its own copy",
      "set(KIND_HEADINGS)" in _wizard and "normalise_kind(" in _wizard)

print()
_total, _passed = len(results), sum(results)
print(f"{_passed}/{_total} passed")
sys.exit(0 if _passed == _total else 1)
