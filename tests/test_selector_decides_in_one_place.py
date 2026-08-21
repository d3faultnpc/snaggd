"""
Whether to apply is decided in one step, and the step is the one that ran before.

The decision used to be three `if`s woven through the apply loop with narration
and early returns between them. Moving it out changes nothing about what a run
does — that is the point of this file. Every string here is the string a person
already reads in History, and the order of the gates is the order they have
always run in.

The order is not incidental. A blocked vacancy is reported as blocked even in a
dry run: the block is a fact about the vacancy, the dry run is a fact about how
the session was launched. The threshold comes last because it is the weakest of
the three — it says a match was thin, not that anything was wrong.

Run:  venv/bin/python3 tests/test_selector_decides_in_one_place.py
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from core.selector import Verdict, threshold_selector  # noqa: E402

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


def _decide(**over):
    args = dict(match_score=80, min_score=65, stop_match=None, stop_basis=None,
                dry_run=False, matched_skills=["a"])
    args.update(over)
    return threshold_selector(**args)


# ── The threshold, and its edge ──────────────────────────────────────────────
print("\nThe threshold:")
_v = _decide(match_score=62)
check("below the threshold is a skip", not _v.apply and _v.status == "skipped_score")
check("and the reason is the sentence History already shows",
      _v.reason == "Score 62 below threshold 65")
check("and so is the line the person sees while it runs",
      _v.gui_line == "[SKIP] match 62% below your threshold")
check("the threshold is the product's own line, not the model's", _v.actor == "scan")

check("exactly on the threshold applies — the comparison is <, not <=",
      _decide(match_score=65).apply)
check("above it applies", _decide(match_score=80).apply)

check("no score at all is not a low score: a failure to measure is not a "
      "measurement", _decide(match_score=None).apply)


# ── Order ────────────────────────────────────────────────────────────────────
print("\nThe order of the gates carries meaning:")
_v = _decide(stop_match="a_category", dry_run=True, match_score=10)
check("a block outranks a dry run — the block is a fact about the vacancy",
      _v.status == "semantic_blocked")

_v = _decide(dry_run=True, match_score=10)
check("a dry run outranks the threshold — a thin match in a dry run is still "
      "reported as a dry run", _v.status == "dry_run")
check("and it says what it would have scored",
      _v.reason == "Dry-run — score: 10")


# ── A block says what it rests on ────────────────────────────────────────────
print("\nA block says what it rests on:")
_v = _decide(stop_match="a_category", stop_basis="company_knowledge")
check("company knowledge is marked unconfirmed wherever it is shown",
      "· unconfirmed by the posting" in _v.reason
      and "· unconfirmed by the posting" in _v.gui_line)
check("and the log line names the basis too",
      "(company knowledge)" in _v.log_line)

_v = _decide(stop_match="a_category", stop_basis="text")
check("a block quoting the posting carries no such mark",
      "unconfirmed" not in _v.reason and "unconfirmed" not in _v.log_line)
check("both kinds are the model's own line", _v.actor == "llm")


# ── The step is a step ───────────────────────────────────────────────────────
print("\nIt is swappable, which is the whole reason it exists:")
check("deciding needs no browser, no network and no disk — it is called with "
      "values and returns one", isinstance(_decide(), Verdict))
check("an applying verdict carries nothing to say, because there is nothing to "
      "report yet", _decide().log_line == "" and _decide().status == "")


# ── The loop still says it, and says the verdict's own words ─────────────────
print("\nThe loop narrates the verdict rather than its own version of it:")
_adapter = (_REPO_ROOT / "adapters" / "hh" / "adapter.py").read_text(encoding="utf-8")
check("the loop calls the step", "threshold_selector(" in _adapter)
for _gone in ("[SKIP] match", "[BLCK] not a fit", "Dry run — would score"):
    check(f"and no longer builds {_gone!r} itself", _gone not in _adapter)
check("it passes the verdict's own actor, not a guess about it",
      "actor=verdict.actor" in _adapter)


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
