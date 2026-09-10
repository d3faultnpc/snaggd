"""Where the claw stops being able to decide, and says so.

The engine drives a job site the way a blind claw follows a route sheet: it
knows addresses, it presses what the sheet names, and it cannot interpret.
Interpretation is the model's job — but until now the claw could only ask at
exactly one node, dismissing an unrecognised modal. Everywhere else a canonical
lookup that missed returned None or False, the caller improvised, and the run
either gave up or carried on into the wrong branch. Neither outcome had a name,
and neither was counted.

A JAM IS A NODE, NOT A SELECTOR
-------------------------------
Measured 2026-09-09 before this was written: outside dom.py the adapter reads
the DOM 88 times across ten files, and 31 addresses are named in config. Making
every one of those a jam would be wrong twice over — most are reads (a vacancy
title, a rating, a description), and a jam on a read is not a decision anyone
can help with.

The decisions are the twelve functions listed in NODES. Each answers "what do I
do next", each already exists, and each has exactly one place where it gives up.
That place calls jam(). The claw's behaviour is unchanged by doing so: jam()
decides nothing and returns nothing.

WHAT THIS IS FOR
----------------
Three things, in this order:

  1. Counting. Which nodes actually jam, and how often, across real users —
     which is what says where a navigator is worth having first, instead of
     guessing from the last incident anyone remembers.
  2. One place to plug in. When the navigator arrives it replaces this
     function's body: collect what the claw can address in `scope`, ask which
     one advances the goal, validate the answer in code, hand it back. Twelve
     call sites, one implementation.
  3. One place for an answer to become canon. A locator the navigator found and
     code validated belongs in that node's own cascade, permanently — so the
     next run does not pay for the same question.

WHY THE VOCABULARY IS CLOSED
----------------------------
A node inventing its own name lands in a counter nobody reads, which is the
failure the feature register exists to prevent one layer up. Same stance here,
and a test enforces it against this file.
"""
from typing import Optional

from utils import call_ledger

# Every decision node in the vacancy loop. The name is what gets counted, so it
# describes the DECISION, not the function that happens to implement it — a
# rename of the function must not silently start a new counter.
NODES = {
    # ── what am I looking at ────────────────────────────────────────────────
    "form_type": "which of the known form shapes this page is",
    # ── what do I press ─────────────────────────────────────────────────────
    "apply_button": "the control that starts an application",
    "action_button": "the control that advances this form",
    "nav_button": "the control that advances hh's own response modal",
    "submit_button": "the control that submits an answered questionnaire",
    # ── where does the letter go ────────────────────────────────────────────
    "cover_field": "the field a cover letter is typed into",
    "add_cover_button": "the control that reveals the cover-letter field in chat",
    "cover_input": "the cover-letter field inside the chat frame",
    # Deliberately no node for sending a typed letter: that path ends in a
    # keypress fallback that always reports success, so it has no branch where
    # the claw admits it could not decide. Declaring one would be a counter that
    # can never move, which is the same failure as a feature flag nothing asks
    # about.
    # ── the layer in the way ────────────────────────────────────────────────
    "blocking_modal": "which control dismisses a modal that is not part of the application",
    # ── reading an employer's own question ──────────────────────────────────
    "option_match": "which of an employer's options the answer corresponds to",
    # ── did it work ─────────────────────────────────────────────────────────
    "submission_verified": "whether the application actually went through",
}


# Set for a debug run only, by whoever owns the loop. A jam is the one moment
# worth a full capture — it is the moment the claw could not name what it was
# looking at — and until now it produced a single line of stdout, mixed in with
# two dozen other kinds of warning. The mechanism that answers "what was on
# screen" already exists and was simply never pointed here.
#
# Never set outside debug, so a customer's run cannot be slowed down by it and
# cannot write a page carrying their own profile fields to disk.
_OBSERVER = None


def set_jam_observer(fn) -> None:
    """Once per session, by whoever owns the loop. None clears it."""
    global _OBSERVER
    _OBSERVER = fn


def jam(node: str, detail: str = "", *, scope=None) -> Optional[object]:
    """The claw could not decide at `node`. Records it and returns None.

    Returning None rather than raising is deliberate and is what keeps this
    change free of behaviour: every call site already handles the None or False
    it was returning before, and adding this line changes nothing about which
    branch runs.

    `scope` is accepted and unused. It is the Page or Frame the decision was
    being made in, and it is threaded now so that step three has the thing it
    needs — what the claw could address at the moment it stopped — without
    revisiting twelve call sites to add an argument.
    """
    if node not in NODES:
        # Loud, because a counter under a name nobody declared is a jam that
        # will never be found again. Not fatal: a mistyped name must not cost a
        # run, only its own observability.
        print(f"   ⚠️  jam at undeclared node {node!r} — declare it in utils/navigation.NODES")
    call_ledger.note_jam(node)
    print(f"   jam at {node}: {detail or NODES.get(node, 'no decision could be made')}")
    if _OBSERVER is not None:
        try:
            _OBSERVER(node, detail, scope)
        except Exception as e:
            # An observer is a diagnostic. It may cost an observation, never a
            # run — the same stance call_meta_of() takes on a missing usage
            # block, and the same one billing takes on an unreachable quota.
            print(f"   ⚠️  jam observer failed ({e}) — the jam itself is unaffected")
    return None
