"""
Unit tests for LLMAgent._sanitize_score_result() score clamping.
No LLM calls — tests the sanitization logic directly.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import patch, MagicMock

# Patch env + OpenAI so LLMAgent.__init__ doesn't fail without a real key
with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from core.llm_agent import LLMAgent
        agent = LLMAgent.__new__(LLMAgent)


def sanitize(score):
    return agent._sanitize_score_result({"score": score, "signals": [], "matched_skills": [], "gaps": []})["score"]


cases = [
    (100,  100, "exact max"),
    (0,    0,   "exact min"),
    (75,   75,  "normal"),
    (454,  100, "hallucination above max"),
    (159,  100, "above max (modifier overflow)"),
    (125,  100, "above max"),
    (-5,   0,   "below min"),
    (101,  100, "one above max"),
    ("80", 80,  "string int"),
]

passed = 0
for raw, expected, label in cases:
    result = sanitize(raw)
    status = "✅" if result == expected else "❌"
    print(f"  {status} {label}: {raw!r} → {result} (expected {expected})")
    if result == expected:
        passed += 1

print(f"\n{passed}/{len(cases)} passed")
assert passed == len(cases), f"FAILED: {len(cases) - passed} test(s) failed"
print("All tests passed.")


# ── Template-echo containment (2026-07-12) ──────────────────────────────────────
# match_scoring.md's JSON example occasionally gets echoed back verbatim instead of
# real analysis (seen live). _sanitize_score_result() must treat this as untrusted
# and reset the whole response, not just the affected field.

from core.llm_agent import _SCORE_PLACEHOLDER_TEXT

RESET_DEFAULTS = {"score": 50, "matched_skills": [], "gaps": [], "signals": [],
                  "stop_match": None, "vacancy_role_type": None}

echo_cases = [
    ("signals contains placeholder",
     {"score": 78, "signals": [_SCORE_PLACEHOLDER_TEXT["signals"]], "matched_skills": ["real skill"],
      "gaps": [], "vacancy_role_type": "builder"}),
    ("matched_skills contains placeholder",
     {"score": 65, "signals": ["real tag"], "matched_skills": [_SCORE_PLACEHOLDER_TEXT["matched_skills"]],
      "gaps": [], "vacancy_role_type": "builder"}),
    ("gaps contains placeholder",
     {"score": 50, "signals": [], "matched_skills": [], "gaps": [_SCORE_PLACEHOLDER_TEXT["gaps"]],
      "vacancy_role_type": None}),
    ("vacancy_role_type equals placeholder",
     {"score": 60, "signals": ["real tag"], "matched_skills": [], "gaps": [],
      "vacancy_role_type": _SCORE_PLACEHOLDER_TEXT["vacancy_role_type"]}),
    ("new bracket-style placeholder, unfilled (current prompt wording)",
     {"score": 55, "signals": ["<REAL_TAG_1>", "<REAL_TAG_2>"], "matched_skills": [],
      "gaps": [], "vacancy_role_type": "builder"}),
    ("partial fill — one real value, one unfilled bracket token in the same list",
     {"score": 70, "signals": ["real tag"], "matched_skills": ["actual skill", "<REAL_SKILL_2>"],
      "gaps": [], "vacancy_role_type": "builder"}),
    ("bracket token embedded inside an otherwise-real sentence",
     {"score": 60, "signals": ["real tag"], "matched_skills": [], "gaps": [],
      "vacancy_role_type": "hybrid of <REAL_CONTRIBUTION_STYLE> and IC work"}),
]

echo_passed = 0
for label, raw_result in echo_cases:
    result = agent._sanitize_score_result(dict(raw_result))
    ok = result == RESET_DEFAULTS
    print(f"  {'✅' if ok else '❌'} {label}: {'reset to safe defaults' if ok else result}")
    if ok:
        echo_passed += 1

# Negative control: realistic well-formed output must NOT be mistaken for template-echo.
clean_result = {"score": 78, "signals": ["B2C mobile app", "user growth"],
                 "matched_skills": ["product discovery"], "gaps": ["direct fintech experience"],
                 "vacancy_role_type": "builder"}
clean_out = agent._sanitize_score_result(dict(clean_result))
clean_ok = clean_out["score"] == 78 and clean_out["signals"] == ["B2C mobile app", "user growth"]
print(f"  {'✅' if clean_ok else '❌'} well-formed output passes through unchanged (no false positive)")
if clean_ok:
    echo_passed += 1

total_echo_cases = len(echo_cases) + 1
print(f"\n{echo_passed}/{total_echo_cases} passed")
assert echo_passed == total_echo_cases, f"FAILED: {total_echo_cases - echo_passed} test(s) failed"
print("All template-echo containment tests passed.")
