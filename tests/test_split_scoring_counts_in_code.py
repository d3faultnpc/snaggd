"""
The score is computed, not asked for.

A single call did all of it — find the blocked category, match the skills, judge
the domain, judge the role, apply the modifiers, do the arithmetic — and returned
one number. On 2026-08-19 an attempt to lift just the arithmetic out took zero
scores from 3/6 to 0/6 and dropped stop-category blocking from 4/4 to 2/4 in the
same edit, and the second half went unnoticed. One call answering for everything
means every change moves everything.

Split, the model answers two questions it can actually answer — what does this
posting ask for, and does this profile establish each of those — and the number
comes out of code that can be read.

The arithmetic checks below matter more than they look. Coverage is a ratio, so
the posting's own demand sits in the denominator; and a requirement the profile
never speaks to leaves the denominator rather than scoring zero, because silence
is not failure.

Run:  venv/bin/python3 tests/test_split_scoring_counts_in_code.py
"""
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
os.environ.setdefault("LLM_API_KEY", "test-key")

from core.llm_agent import LLMAgent  # noqa: E402

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


_agent = LLMAgent(data_dir=Path("/tmp"))


def _score(reqs, verdicts):
    return _agent._score_from_verdicts(
        [{"text": f"r{i}", "importance": imp} for i, imp in enumerate(reqs)],
        [{"i": i, "verdict": v} for i, v in enumerate(verdicts)])


# ── The arithmetic ───────────────────────────────────────────────────────────
print("\nCoverage is a ratio of what was asked:")
check("everything met is full marks", _score(["must", "must"], ["met", "met"]) == 100)
check("nothing met is zero", _score(["must", "must"], ["absent", "absent"]) == 0)
check("half met is half", _score(["must", "must"], ["met", "absent"]) == 50)
check("partial is worth half of met", _score(["must"], ["partial"]) == 50)

check("a requirement the posting calls mandatory weighs double one it calls a plus",
      _score(["must", "nice"], ["met", "absent"]) == 67
      and _score(["must", "nice"], ["absent", "met"]) == 33)
check("unspecified weighs as a plus, not as mandatory — a posting that does not "
      "say how much something matters has not said it is required",
      _score(["must", "unspecified"], ["met", "absent"]) == 67)

print("\nThe demand belongs in the denominator:")
check("a junior posting with three asks, two met, beats a senior one with twelve "
      "asks and four met",
      _score(["must"] * 3, ["met", "met", "absent"])
      > _score(["must"] * 12, ["met"] * 4 + ["absent"] * 8))

print("\nSilence is not failure:")
check("a requirement the profile never speaks to leaves the denominator",
      _score(["must", "must"], ["met", "silent"]) == 100)
check("which is not the same as it being absent — that one does count",
      _score(["must", "must"], ["met", "absent"]) == 50)
check("a thin profile against a demanding posting is judged on what it did speak to",
      _score(["must"] * 5, ["met", "silent", "silent", "silent", "silent"]) == 100)
check("and a verdict we never asked for is treated as silence, not as a pass",
      _score(["must", "must"], ["met", "probably"]) == 100)

print("\nNothing judgeable is None, never a number:")
check("all silent → None, because there is no honest score for it",
      _score(["must", "nice"], ["silent", "silent"]) is None)
check("no verdicts at all → None", _score(["must"], []) is None)
check("no requirements at all → None", _score([], []) is None)


# ── The quote is enforced, not requested ─────────────────────────────────────
print("\nA requirement has to be in the posting:")


class _Reply:
    def __init__(self, payload):
        self.payload = payload
        self.chat = type("_C", (), {"completions": self})()

    def create(self, **_kw):
        choice = type("_Ch", (), {"finish_reason": "stop",
                                  "message": type("_M", (), {"content": self.payload})()})()
        return type("_R", (), {"id": "gen-x", "choices": [choice],
                               "usage": type("_U", (), {"prompt_tokens": 1,
                                                        "completion_tokens": 1})()})()


_agent._client_cache = _Reply(
    '{"requirements": ['
    '{"text": "real one", "quote": "we need someone who ships", "importance": "must"},'
    '{"text": "invented one", "quote": "a phrase that is not in the posting", "importance": "must"}'
    '], "signals": ["a"], "vacancy_role_type": "builder"}')
_agent._client_cache_key = _agent.api_key

_out = _agent._requirements_of("The posting says we need someone who ships things.")
check("a requirement whose quote is in the posting is kept",
      [r["text"] for r in _out["requirements"]] == ["real one"])
check("and one whose quote is not there is dropped — the quote is the whole basis "
      "for calling it a requirement, and this rule can be enforced rather than asked",
      "invented one" not in str(_out["requirements"]))


# ── The switch ───────────────────────────────────────────────────────────────
print("\nOff by default:")
_src = (_REPO_ROOT / "core" / "llm_agent.py").read_text(encoding="utf-8")
check("the split path runs only when explicitly switched on",
      'os.getenv("SNAGGD_SPLIT_SCORING"' in _src)
check("and a split that produces nothing judgeable falls back to the single call "
      "rather than inventing a number",
      "falling back to one call" in _src)
check("stage one is given no candidate profile at all",
      "You read job postings and list what they ask for." in _src)


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
