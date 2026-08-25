"""
A hard block is the strongest thing done to a vacancy: no application is sent at
all, and the person never learns the opportunity existed. So a block has to be
DECLARED (the category is one the candidate themselves listed) and EVIDENCED
(the answer says what it rests on).

Both rules come from reviewing every block one real profile had made. Of 29,
five were plainly wrong and one was not a category at all — a UI object's repr
had leaked into the reply, and a non-empty string is truthy, so a bank got
blocked by `Panel(layout='column', ...)`. The wrong ones shared a shape: a
neighbouring field read as the field itself — products sharing users or mechanics
with a blocked category, a vendor selling tooling into one, and an employer whose
name merely resembled a brand in one.

The fixtures below use stand-in category names on purpose. A category is one
person's own decision; baking a real one into a shipped test would quietly turn
one user's preference into the product's example of what people refuse.

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
        # candidate.md, because that is where a semantic category lives now. It used
        # to be declarable in job_preferences.md as well, and the two were merged by
        # union — so removing one from candidate.md did not remove it. Dropped
        # 2026-08-21; the fixture follows the one home.
        (tmp / "candidate.md").write_text(
            "# t\n\n## Career Profile\nstop_categories: " + ", ".join(categories) + "\n",
            encoding="utf-8")
    return LLMAgent(data_dir=tmp)


def _verdict(agent, **fields):
    reply = {"score": 70, "matched_skills": [], "gaps": [], "signals": [], **fields}
    with redirect_stdout(StringIO()):
        return agent._sanitize_score_result(reply)


# ── The vocabulary is the candidate's own list ───────────────────────────────
print("\nOnly a declared category can block:")
_agent = _agent_declaring("example_category", "second_category")

_r = _verdict(_agent, stop_match="example_category", stop_basis="text",
              stop_evidence="the posting says 'the category, named outright'")
check("a declared category with evidence blocks", _r["stop_match"] == "example_category")

_r = _verdict(_agent, stop_match="a_category_never_declared", stop_basis="text", stop_evidence="the posting mentions it")
check("a category the candidate never declared does not block", _r["stop_match"] is None)

_r = _verdict(_agent, stop_match="Panel(layout='column', items=[], background='transparent')",
              stop_basis="text", stop_evidence="whatever")
check("a leaked object repr is not a category and blocks nothing",
      _r["stop_match"] is None)

_r = _verdict(_agent_declaring(), stop_match="example_category", stop_basis="text",
              stop_evidence="the posting says so")
check("a profile that declares nothing blocks nothing", _r["stop_match"] is None)

_r = _verdict(_agent, stop_match="  EXAMPLE_CATEGORY  ", stop_basis="text", stop_evidence="the posting says so")
check("case and stray spacing do not decide whether a rule applies",
      _r["stop_match"] == "example_category")


# ── A block must say what it rests on ────────────────────────────────────────
print("\nA block must say what it rests on:")
_r = _verdict(_agent, stop_match="example_category")
check("no basis at all → not a block", _r["stop_match"] is None)

_r = _verdict(_agent, stop_match="example_category", stop_basis="text", stop_evidence="   ")
check("empty evidence → not a block", _r["stop_match"] is None)

_r = _verdict(_agent, stop_match="example_category", stop_basis="vibes", stop_evidence="feels like it")
check("a basis outside the two allowed kinds → not a block", _r["stop_match"] is None)

# Company knowledge stays allowed — three real correct ones were — but from
# 2026-08-21 it has to be corroborated by the model's OWN signals. The prompt has
# always said "if the signals you are producing contradict the category you are
# about to block on, do not block", and nothing enforced it. Every false block
# measured over 2026-08-18..20 named a category appearing nowhere in its own
# signals: an employer blocked for its parent group, a bank blocked through a
# chain of ownership, a vendor blocked for what its CUSTOMERS do.
_r = _verdict(_agent, stop_match="example_category", stop_basis="company_knowledge",
              stop_evidence="the employer's business is that category",
              signals=["example_category_primary", "b2c"])
check("company knowledge corroborated by its own signals is still a block",
      _r["stop_match"] == "example_category" and _r["stop_basis"] == "company_knowledge")
check("and the fact behind it is kept, since the posting cannot confirm it later",
      _r["stop_evidence"] == "the employer's business is that category")

_r = _verdict(_agent, stop_match="example_category", stop_basis="company_knowledge",
              stop_evidence="the parent group is known for it",
              signals=["ai_platform", "enterprise_b2b", "top_employer"])
check("company knowledge its own signals do not support → not a block",
      _r["stop_match"] is None)
check("and the refusal is written down, or nobody could ever review it",
      (_r.get("stop_suppressed") or {}).get("category") == "example_category")

# "<category>_adjacent" is the prompt's own marker for NOT blocking, so it cannot
# be what corroborates a block. This is the vendor-selling-into-the-category case.
_r = _verdict(_agent, stop_match="example_category", stop_basis="company_knowledge",
              stop_evidence="its customers are in that category",
              signals=["b2b_saas", "example_category_adjacent"])
check("an _adjacent signal corroborates nothing — it means the opposite",
      _r["stop_match"] is None)

# Text is exempt on purpose: a quote from the posting supports itself, and all
# three text-based blocks in the same measurement were correct.
_r = _verdict(_agent, stop_match="example_category", stop_basis="text",
              stop_evidence="the posting says so in these words",
              signals=["unrelated_tag"])
check("a quoted posting still blocks without help from the signals",
      _r["stop_match"] == "example_category")

# One real false block arrived with nothing in it at all: no skills, no gaps, no
# signals, just a category and a story about the employer. Scoped to company
# knowledge — a text block with a quote supports itself, and refusing that for a
# formatting deficiency would send an application to an employer the person
# explicitly excluded.
_r = _verdict(_agent, stop_match="example_category", stop_basis="company_knowledge",
              stop_evidence="the employer is known for it", signals=[],
              matched_skills=[], gaps=[])
check("company knowledge with no analysis behind it does not get to block",
      _r["stop_match"] is None)
check("and the reason is recorded, not just the refusal",
      (_r.get("stop_suppressed") or {}).get("why") == "the answer carries no analysis at all")

_r = _verdict(_agent, stop_match="example_category", stop_basis="text",
              stop_evidence="the posting says so in these words", signals=[],
              matched_skills=[], gaps=[])
check("but a quoted posting still blocks even when the rest of the answer is thin",
      _r["stop_match"] == "example_category")


# ── A name on the list is not a category, and is checked differently ─────────
# Names started reaching this tier on 2026-08-25, when one wizard field began
# feeding both stop tiers — a person refusing work does not sort their refusals by
# matching mechanism, so the wizard stopped asking them to.
#
# Everything above was built and measured for CATEGORIES, and a name breaks one of
# its premises: "a quote from the posting supports itself" holds for a kind of
# business (the quote has to describe the business) and fails for a name (a posting
# is full of other companies' names). Measured live twice on five synthetic
# postings; the run that produced these rules blocked a grocery chain called
# «Монетка» for a stop entry «Монеткин», quoting its own name as the evidence.
print("\nAn employer block is checked against the name it claims:")

_named = _agent_declaring("example_company", "example_category")

_r = _verdict(_named, stop_match="example_company", stop_kind="employer",
              stop_basis="text", stop_evidence="Компания «Example_Company» приглашает")
check("a quote that contains the name blocks, case and guillemets ignored",
      _r["stop_match"] == "example_company")

_r = _verdict(_named, stop_match="example_company", stop_kind="employer",
              stop_basis="text", stop_evidence="«Example_Cmpny» приглашает")
check("a quote naming a DIFFERENT, similar company does not block",
      _r["stop_match"] is None)
check("and the refusal says which rule refused it",
      "quote the name it blocks on" in ((_r.get("stop_suppressed") or {}).get("why") or ""))

# The half that must NOT change. A category is matched by meaning, so wording that
# never contains the word is exactly how the semantic tier earns its keep — demanding
# containment there would break it.
_r = _verdict(_named, stop_match="example_category", stop_kind="category",
              stop_basis="text", stop_evidence="a phrase that never says the word")
check("a category still blocks on wording that does not contain it",
      _r["stop_match"] == "example_category")

# An answer that does not say which judgement it made cannot be checked, so it is
# treated as the unguarded case it currently is — recorded here as the known gap
# rather than asserted as correct.
_r = _verdict(_named, stop_match="example_company",
              stop_basis="text", stop_evidence="«Example_Cmpny» приглашает")
check("an answer omitting stop_kind falls through to the category path (known gap)",
      _r["stop_match"] == "example_company")


# ── The prompt no longer teaches the mistake ─────────────────────────────────
print("\nThe prompt no longer teaches recognition by name:")
_prompt = (_REPO_ROOT / "prompts" / "match_scoring.md").read_text(encoding="utf-8")
check("no example telling the model to block a company by its name alone",
      "with no explicit keyword" not in _prompt)
check("and no real category is baked in as the product's own example",
      not any(w in _prompt.lower() for w in ("gambling", "casino", "betting", "igaming", "mlm")))
check("name resemblance is named as NOT evidence",
      "resembles a known brand" in _prompt or "what its name sounds like" in _prompt)
check("a neighbouring field is named as NOT evidence",
      "Neighbouring belongs\nin signals" in _prompt or "Neighbouring belongs" in _prompt)
check("a vendor is not its client's business", "CLIENT" in _prompt)
check("uncertainty is told to produce null, not a block",
      "If unsure, do not block" in _prompt)
check("contradicting your own signals is told to stop the block",
      "contradict the category you" in _prompt)
check("the answer shape asks for the basis and the evidence",
      '"stop_basis": null' in _prompt and '"stop_evidence": null' in _prompt)
check("and asks which of the two judgements was made",
      '"stop_kind": null' in _prompt)
check("the prompt tells the model a client is not the employer",
      "AMONG its clients" in _prompt)


# ── The record keeps the basis, or the review cannot happen twice ────────────
print("\nThe record keeps it:")
_adapter = (_REPO_ROOT / "adapters" / "hh" / "adapter.py").read_text(encoding="utf-8")
_details = _adapter.split("score_details = {", 1)[-1].split("}", 1)[0]
check("stop_basis and stop_evidence are part of the logged record",
      "'stop_basis'" in _details and "'stop_evidence'" in _details)
# The mark is written where the decision is made, which since 2026-08-21 is
# core/selector.py rather than inside the loop. Same words, same place in the
# record a person reads; only the file that composes them changed.
_selector = (_REPO_ROOT / "core" / "selector.py").read_text(encoding="utf-8")
check("a block resting only on company knowledge is marked where a person sees it",
      "company_knowledge" in _selector and "unconfirmed by the posting" in _selector)
check("and the loop says the verdict's own words rather than composing its own",
      "verdict.gui_line" in _adapter and "verdict.reason" in _adapter)

print()
_total, _passed = len(results), sum(results)
print(f"{_passed}/{_total} passed")
sys.exit(0 if _passed == _total else 1)
