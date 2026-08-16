"""
A hard block is the strongest thing done to a vacancy: no application is sent at
all, and the person never learns the opportunity existed. So a block has to be
DECLARED (the category is one the candidate themselves listed) and EVIDENCED
(the answer says what it rests on).

Both rules come from reviewing every block one real profile had made. Of 29,
five were plainly wrong and one was not a category at all — a UI object's repr
had leaked into the reply, and a non-empty string is truthy, so a bank got
blocked by `Panel(layout='column', ...)`. The wrong ones shared a shape: an
adjacent domain read as the domain itself — a games studio, a payments company,
a payroll project, and an app whose company name merely resembled a betting
brand.

Three correct blocks, though, rested on nothing in the posting at all: the
employer was known to operate in the category and never said so. That is why
the rule is "say what it rests on", not "quote the posting or drop it".

Run:  venv/bin/python3 tests/test_stop_match_is_declared_and_evidenced.py
"""
import os
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("LLM_API_KEY", "test-key")

from core.llm_agent import LLMAgent  # noqa: E402

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


def _agent_declaring(*categories):
    """An agent whose profile declares exactly these stop categories."""
    tmp = Path(tempfile.mkdtemp())
    if categories:
        (tmp / "job_preferences.md").write_text(
            "stop_categories:\n" + "".join(f"  - {c}\n" for c in categories), encoding="utf-8")
    return LLMAgent(data_dir=tmp)


def _verdict(agent, **fields):
    reply = {"score": 70, "matched_skills": [], "gaps": [], "signals": [], **fields}
    with redirect_stdout(StringIO()):
        return agent._sanitize_score_result(reply)


# ── The vocabulary is the candidate's own list ───────────────────────────────
print("\nOnly a declared category can block:")
_agent = _agent_declaring("gambling", "mlm")

_r = _verdict(_agent, stop_match="gambling", stop_basis="text",
              stop_evidence="the posting says 'iGaming platform'")
check("a declared category with evidence blocks", _r["stop_match"] == "gambling")

_r = _verdict(_agent, stop_match="crypto", stop_basis="text", stop_evidence="mentions tokens")
check("a category the candidate never declared does not block", _r["stop_match"] is None)

_r = _verdict(_agent, stop_match="Panel(layout='column', items=[], background='transparent')",
              stop_basis="text", stop_evidence="whatever")
check("a leaked object repr is not a category and blocks nothing",
      _r["stop_match"] is None)

_r = _verdict(_agent_declaring(), stop_match="gambling", stop_basis="text",
              stop_evidence="says casino")
check("a profile that declares nothing blocks nothing", _r["stop_match"] is None)

_r = _verdict(_agent, stop_match="  GAMBLING  ", stop_basis="text", stop_evidence="says casino")
check("case and stray spacing do not decide whether a rule applies",
      _r["stop_match"] == "gambling")


# ── A block must say what it rests on ────────────────────────────────────────
print("\nA block must say what it rests on:")
_r = _verdict(_agent, stop_match="gambling")
check("no basis at all → not a block", _r["stop_match"] is None)

_r = _verdict(_agent, stop_match="gambling", stop_basis="text", stop_evidence="   ")
check("empty evidence → not a block", _r["stop_match"] is None)

_r = _verdict(_agent, stop_match="gambling", stop_basis="vibes", stop_evidence="feels like it")
check("a basis outside the two allowed kinds → not a block", _r["stop_match"] is None)

_r = _verdict(_agent, stop_match="gambling", stop_basis="company_knowledge",
              stop_evidence="the employer runs an online casino")
check("a block on company knowledge is allowed — three real correct ones were",
      _r["stop_match"] == "gambling" and _r["stop_basis"] == "company_knowledge")
check("and the fact behind it is kept, since the posting cannot confirm it later",
      _r["stop_evidence"] == "the employer runs an online casino")


# ── The prompt no longer teaches the mistake ─────────────────────────────────
print("\nThe prompt no longer teaches recognition by name:")
_prompt = (_REPO_ROOT / "prompts" / "match_scoring.md").read_text(encoding="utf-8")
check("no example telling the model to block a company by its name alone",
      "with no explicit keyword" not in _prompt)
check("name resemblance is named as NOT evidence",
      "resembles a known brand" in _prompt or "what its name sounds like" in _prompt)
check("adjacency is named as NOT evidence", "Adjacency belongs in signals" in _prompt)
check("a vendor is not its client's business", "CLIENT" in _prompt)
check("uncertainty is told to produce null, not a block",
      "If unsure, do not block" in _prompt)
check("contradicting your own signals is told to stop the block",
      "contradict the category you" in _prompt)
check("the answer shape asks for the basis and the evidence",
      '"stop_basis": null' in _prompt and '"stop_evidence": null' in _prompt)


# ── The record keeps the basis, or the review cannot happen twice ────────────
print("\nThe record keeps it:")
_adapter = (_REPO_ROOT / "adapters" / "hh" / "adapter.py").read_text(encoding="utf-8")
_details = _adapter.split("score_details = {", 1)[-1].split("}", 1)[0]
check("stop_basis and stop_evidence are part of the logged record",
      "'stop_basis'" in _details and "'stop_evidence'" in _details)
check("a block resting only on company knowledge is marked where a person sees it",
      "company_knowledge" in _adapter and "unconfirmed by the posting" in _adapter)

print()
_total, _passed = len(results), sum(results)
print(f"{_passed}/{_total} passed")
sys.exit(0 if _passed == _total else 1)
