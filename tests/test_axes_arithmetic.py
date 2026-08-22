"""The axis arithmetic decides, the model only labels — so this runs with no LLM.

Every property here is one that was paid for. The zeros, the ceiling, the
paraphrasing aggregate: each check below names the failure it exists to prevent.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.axes import (
    AXES, AXIS_WEIGHTS, LABELS, NON_COMPENSABLE,
    normalise_label, score_from_axes, validate_matched_skills,
)

failures = []


def check(label, condition):
    print(("  ✅ " if condition else "  ❌ ") + label)
    if not condition:
        failures.append(label)


def run():
    print("\nLabel vocabulary")
    check("a declared grade normalises to itself",
          normalise_label("Strong") == "strong")
    check("whitespace and case do not make a new grade",
          normalise_label("  IDEAL  ") == "ideal")
    check("a grade nobody declared is not a grade — None, never a guess",
          normalise_label("pretty good") is None)
    check("None stays None", normalise_label(None) is None)
    check("a number is not a grade — the whole point is that the model stopped "
          "returning numbers", normalise_label(70) is None)

    print("\nNeutral is not half — it leaves the denominator")
    v = score_from_axes({"skills": "ideal", "tools": "neutral"})
    check("a vacancy that did not ask about tooling does not drag a perfect "
          "candidate to the middle", v.score == 100)
    check("and the axis is reported as not asked", v.neutral == ("tools",))
    check("only the axes in play counted", v.in_play == ("skills",))

    half = score_from_axes({"skills": "ideal", "tools": "miss"})
    check("whereas an axis that WAS asked and missed does count", half.score == 50)

    print("\nNo axis in play is not a score of zero")
    empty = score_from_axes({"skills": "neutral", "tools": "neutral"})
    check("all-neutral returns None, not 0 — a 0 here is what made a genuine "
          "mismatch indistinguishable from a model that refused to work",
          empty.score is None)
    check("nothing counted", empty.in_play == ())
    check("an empty answer scores nothing at all", score_from_axes({}).score is None)

    print("\nThe ends of the scale")
    perfect = {axis: "ideal" for axis in AXES}
    check("every axis ideal → 100", score_from_axes(perfect).score == 100)
    worst = {axis: "miss" for axis in AXES}
    check("every axis missed → 0", score_from_axes(worst).score == 0)
    check("and 0 arrived with axes in play, so it means something",
          score_from_axes(worst).in_play == tuple(AXES))

    print("\nCredential is non-compensable, and reports rather than enforces")
    strong_but_unlicensed = {a: "ideal" for a in AXES}
    strong_but_unlicensed["credential"] = "miss"
    v = score_from_axes(strong_but_unlicensed)
    check("a missing licence is reported as non-compensable",
          v.non_compensable == ("credential",))
    check("but the score is still computed, not zeroed — zeroing is the disease, "
          f"not the cure (got {v.score})", v.score is not None and v.score > 0)
    check("the decision layer gets a number it can still rank by", v.score == 80)

    weak_tools = {a: "ideal" for a in AXES}
    weak_tools["tools"] = "miss"
    check("a missing TOOL is compensable and is not reported as blocking",
          score_from_axes(weak_tools).non_compensable == ())
    check("credential is the only non-compensable axis",
          NON_COMPENSABLE == frozenset({"credential"}))

    print("\nA credential nobody asked for changes nothing")
    v = score_from_axes({"skills": "ideal", "credential": "neutral"})
    check("neutral credential is not a block", v.non_compensable == ())
    check("and does not touch the score", v.score == 100)

    print("\nInvented grades are discarded AND counted")
    v = score_from_axes({"skills": "ideal", "tools": "excellent"})
    check("the invented grade does not enter the arithmetic", v.score == 100)
    check("it is not silently averaged away — it is reported",
          v.unknown_labels == {"tools": "excellent"})
    check("and its axis is neither in play nor neutral",
          "tools" not in v.in_play and "tools" not in v.neutral)

    print("\nmatched_skills is a selection from the person's document")
    declared = ["SQL", "Metabase", "Postman", "Jira"]
    kept, dropped = validate_matched_skills(["SQL", "Postman"], declared)
    check("exact members survive", kept == ["SQL", "Postman"])
    check("nothing dropped", dropped == 0)

    kept, dropped = validate_matched_skills(["SQL queries", "Postman"], declared)
    check("a paraphrase is not a member — 42.8% of live emissions were "
          "paraphrases, and the aggregate built on them counts near-duplicates",
          kept == ["Postman"])
    check("and the paraphrase is counted, because the day this climbs the "
          "dashboard has quietly stopped meaning what it says", dropped == 1)

    kept, _ = validate_matched_skills(["sql", "  METABASE "], declared)
    check("matching ignores case and padding", len(kept) == 2)
    check("but the candidate's own spelling is what comes back",
          kept == ["SQL", "Metabase"])

    kept, dropped = validate_matched_skills(["SQL", "SQL"], declared)
    check("a repeat is not two matches", kept == ["SQL"])
    check("and the repeat is counted as dropped", dropped == 1)

    check("nothing declared means nothing can match",
          validate_matched_skills(["SQL"], []) == ([], 1))
    check("nothing returned is not an error",
          validate_matched_skills([], declared) == ([], 0))
    check("None on either side is survivable",
          validate_matched_skills(None, None) == ([], 0))

    print("\nThe axes stay tied to the frame")
    from onboarding.profile_frame import KIND_HEADINGS, DECLARED_SECTIONS
    known = set(KIND_HEADINGS) | {s.lower() for s in DECLARED_SECTIONS}
    orphans = [(axis, src) for axis, sources in AXES.items()
               for src in sources if src not in known]
    check(f"every axis reads something the frame actually declares "
          f"(orphans: {orphans or 'none'})", not orphans)
    check("every axis has a weight", set(AXIS_WEIGHTS) == set(AXES))
    check("neutral is a label but has no value — it never enters arithmetic",
          "neutral" in LABELS)

    print()
    print(f"{'❌ ' + str(len(failures)) + ' failed' if failures else '✅ all passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
