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
check("an absent domain applies no modifier",
      "states no domain or industry background at all — apply NO modifier" in _SCORING)
check("and the prompt says silence is not a mismatch",
      "not evidence of\ndistance" in _SCORING or "is not evidence of" in _SCORING)
check("the top band no longer requires a domain the profile may not have",
      "can still score here" in _SCORING)
check("role_type keeps its own absence rule",
      "role_type is absent or empty — apply NO modifier" in _SCORING)


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
