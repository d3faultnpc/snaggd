"""An absent score must arrive at the gate still absent.

The 2026-09-03 incident needed two things to be true at once: the gate read a
missing score as consent (fixed in core/selector.py), and a missing score
actually reached it. The second half was never pinned by anything. It holds
today because llm_cover.score() assigns `score_data.get("score")` with no
`or 0` — a one-token change away from a silent 0 that would sail under every
threshold and be indistinguishable from a genuine mismatch.

This is the seam, not the arithmetic: no model is called, no browser, no disk
beyond a scratch profile. The agent is a stub that returns exactly what the
three real scorers can return when they measure nothing.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.selector import threshold_selector  # noqa: E402
from llm_cover import LLMCover  # noqa: E402

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


class _ScorerThatMeasuredNothing:
    """What all three scorers hand back when no axis was in play: the shape is
    whole, every analysis field is present, and `score` is None."""

    # Read by _hash_text() to key the score cache — the two model names are part
    # of what makes a cached score belong to the answer it came from.
    model = "stub/model"
    cover_model = "stub/cover-model"

    def score_vacancy(self, _text):
        return {"score": None, "axes": {}, "axes_in_play": [], "axes_neutral": [],
                "matched_skills": [], "matched_skills_dropped": 0, "signals": [],
                "role_fit": None, "non_compensable": [], "stop_match": None,
                "stop_basis": None, "stop_evidence": None, "stop_suppressed": None,
                "scoring_format": "axes-v3"}


print("\nAn absent score survives the trip from the scorer to the gate:")

with tempfile.TemporaryDirectory() as tmp:
    profile = Path(tmp)
    (profile / "candidate.md").write_text("# scratch profile\n", encoding="utf-8")

    cover = LLMCover(data_dir=profile)
    cover._agent = _ScorerThatMeasuredNothing()

    ok = cover.score("Employer: Nobody\n\nA posting.")
    check("score() reports success — measuring nothing is not a failure to run", ok)
    check("and last_score is None, not 0 — no `or 0` on the way through",
          cover.last_score is None)

    verdict = threshold_selector(
        match_score=cover.last_score, min_score=55, stop_match=None,
        stop_basis=None, dry_run=False, matched_skills=cover.last_matched_skills)
    check("the gate does not apply on it", not verdict.apply)
    check("and names it as its own outcome, not as a low score",
          verdict.status == "skipped_no_score")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
