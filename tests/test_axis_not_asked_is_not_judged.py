"""An axis the posting never raised must not be graded.

The prompt has said so since the axes landed ("neutral — THIS POSTING DOES NOT ASK
ABOUT THIS AXIS", plus a paragraph explaining it). It was still broken in production:
measured 2026-08-24, an axis came back `education: miss` on a posting whose
text contains no word about education, and the anchor gave it away by naming his own
record (a line from their own record) rather than anything the posting asked.
That one axis dropped him from 33 to 25 and put him level with a recruiter on a job
he fits.

Fourth rule in this codebase written in a prompt and broken in production. So it has
a mechanism now, and the mechanism has this test.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.axes import grade_after_asked, score_from_axes  # noqa: E402

failures = []


def check(label, condition):
    print(("  ✅ " if condition else "  ❌ ") + label)
    if not condition:
        failures.append(label)


def run():
    print("\n`asked: false` overrides whatever was graded")
    for graded in ("miss", "weak", "strong", "ideal"):
        grade, coerced_from = grade_after_asked(graded, False)
        check(f"`{graded}` on an axis nobody raised becomes neutral", grade == "neutral")
        check(f"and `{graded}` stays in the record as what it replaced",
              coerced_from == graded)

    print("\nEverything else is left exactly as it was")
    check("an asked axis keeps its grade", grade_after_asked("miss", True) == ("miss", None))
    check("a missing `asked` is not a claim the posting was silent",
          grade_after_asked("miss", None) == ("miss", None))
    check("nor is an unparseable one", grade_after_asked("weak", "") == ("weak", None))
    check("a grade nobody declared is left for unknown_labels, where an inventing "
          "model is counted", grade_after_asked(None, False) == (None, None))

    print("\nThe measured case, in numbers")
    before = score_from_axes({"skills": "weak", "tools": "miss",
                              "experience": "strong", "education": "miss"})
    check("graded on an axis the posting never raised: 25", before.score == 25)

    graded = {}
    for axis, (grade, asked) in (("skills", ("weak", True)), ("tools", ("miss", True)),
                                 ("experience", ("strong", True)),
                                 ("education", ("miss", False))):
        graded[axis], _ = grade_after_asked(grade, asked)
    after = score_from_axes(graded)
    check("not graded: 33", after.score == 33)
    check("the axis is reported as not asked", "education" in after.neutral)
    check("and it is out of the denominator, not a zero in it",
          "education" not in after.in_play)

    print()
    print(f"{'❌ ' + str(len(failures)) + ' failed' if failures else '✅ all passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
