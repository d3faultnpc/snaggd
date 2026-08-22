"""The number in the model's reply is not the score. No LLM calls.

This file used to test a clamp. The clamp existed because the prompt asked the model
for a number and then had it apply a stack of modifiers to it, so replies arrived at
454 and at -5, and one live reply was "紙 67". None of that can happen now: the model
grades axes, the arithmetic runs in core.axes, and a value that cannot leave [0, 100]
does not need containing.

What replaces it is the opposite property. A model that sends a number anyway — out of
habit, or because it has seen a thousand scoring prompts — must not be able to set the
score by doing so.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import patch

with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from core.llm_agent import LLMAgent
        agent = LLMAgent.__new__(LLMAgent)

failures = []


def check(label, condition):
    print(("  ✅ " if condition else "  ❌ ") + label)
    if not condition:
        failures.append(label)


def sanitize(**fields):
    base = {"axes": {}, "matched_skills": [], "signals": [], "stop_match": None}
    base.update(fields)
    return agent._sanitize_score_result(base)


IDEAL = {"grade": "ideal", "anchor": "does exactly this"}
MISS = {"grade": "miss", "anchor": "absent from record"}
NEUTRAL = {"grade": "neutral", "anchor": "not asked"}

print("\nA number in the reply is ignored")
for sent in (454, -5, 71, "紙 67", None, "high"):
    r = sanitize(score=sent, axes={"skills": IDEAL})
    check(f"reply said {sent!r} → score is ours ({r['score']})", r["score"] == 100)

print("\nThe score comes from the grades")
check("one ideal, one missed → half",
      sanitize(axes={"skills": IDEAL, "tools": MISS})["score"] == 50)
check("an axis the posting never raised leaves the denominator",
      sanitize(axes={"skills": IDEAL, "tools": NEUTRAL})["score"] == 100)
check("no axis graded at all → no score, and no invented 50",
      sanitize(axes={})["score"] is None)
check("axes that are not a dict do not crash the call",
      sanitize(axes="everything matched")["score"] is None)

print("\nThe record can be argued with")
r = sanitize(axes={"skills": IDEAL, "tools": MISS, "credential": MISS})
check("it carries the grades the number was built from",
      r["axes"]["tools"] == {"grade": "miss", "anchor": "absent from record"})
check("and does NOT hard-gate on a credential the document never claimed to list — "
      "this bare instance carries no profile, so the gate has nothing to stand on",
      r["non_compensable"] == [])
check("and stamps which shape it is in, so aggregates can tell eras apart",
      r["scoring_format"] == "axes-v1")

print("\nAn invented grade is dropped and reported, never averaged")
r = sanitize(axes={"skills": IDEAL, "tools": {"grade": "amazing", "anchor": "x"}})
check("the score ignores it", r["score"] == 100)
check("it is not in the axes record", "tools" not in r["axes"])
check("but it is reported", r.get("axes_unknown") == {"tools": "amazing"})

print("\nAn unfilled placeholder makes the whole answer untrusted")
r = sanitize(axes={"skills": {"grade": "<GRADE>", "anchor": "<THREE_TO_FIVE_WORDS>"}})
check("a token nested inside axes is still caught — the old guard only looked at "
      "top-level keys and would have missed it", r["score"] is None)
check("and nothing survives from that answer", r["axes"] == {} and r["matched_skills"] == [])

print("\nrole_fit is normalised, or it is nothing")
check("a declared value survives with its anchor",
      sanitize(axes={"skills": IDEAL},
               role_fit={"value": "different", "anchor": "barista counter role"}
               )["role_fit"] == {"value": "different", "anchor": "barista counter role"})
check("an invented value is not a value",
      sanitize(axes={"skills": IDEAL}, role_fit={"value": "kind of"})["role_fit"] is None)
check("a missing one is None, not a guess",
      sanitize(axes={"skills": IDEAL})["role_fit"] is None)
check("and it does not touch the score — a different question, answered separately",
      sanitize(axes={"skills": IDEAL},
               role_fit={"value": "different", "anchor": "x"})["score"] == 100)

print("\ngaps is gone")
check("a model still sending it does not get it into the record",
      "gaps" not in sanitize(gaps=["something"], axes={"skills": IDEAL}))

print()
print(f"{'❌ ' + str(len(failures)) + ' failed' if failures else '✅ all passed'}")
sys.exit(1 if failures else 0)
