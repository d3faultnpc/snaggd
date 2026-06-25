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
