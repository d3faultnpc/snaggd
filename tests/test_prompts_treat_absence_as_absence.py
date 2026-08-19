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
# Until 2026-08-18 the top band said a profile stating no domain "can still score
# here" — an exception carved into a rule that should not have needed one. The
# bands no longer mention domain AT ALL: it is applied once, as the modifier
# below. So the property is now structural rather than promised, and this checks
# the structure. Domain in both places is what drove three real matches to 0 —
# the band demoted them for the domain, then the modifier charged for it again.
# The BAND LINES themselves, not the paragraph explaining them — that paragraph
# says "domain" several times on purpose, to say where it does NOT belong.
_BAND_LINES = [ln for ln in _SCORING.splitlines()
               if ln.startswith("- ") and "–" in ln.split(":")[0]]
check("the score bands say nothing about domain — it is counted once, below",
      _BAND_LINES and not any("domain" in ln.lower() for ln in _BAND_LINES))
check("and a match with real skill overlap may not be scored zero",
      "If any core skill\ngenuinely matches, the score is not 0" in _SCORING)
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
