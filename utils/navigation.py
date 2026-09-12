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
    # hh's own profile surveys, which are not part of an application at all and
    # can write to the person's real resume. The one node where giving up is not
    # a lost application but an edit nobody asked for, and the one whose
    # vocabulary is closed — 47 keys across 4 families, captured 2026-08-27 —
    # which makes it the only node a navigator can be accepted on offline.
    "data_collector_close": "which control dismisses one of hh's own profile surveys without saving it",
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


# The navigator, if anything plugged one in. Nothing here decides whether it
# runs: it runs when it exists, and the engine ships no implementation of its
# own registration. That is the same shape as set_relay_fallback,
# set_session_reporter, set_ledger and set_jam_observer — four hooks in this
# codebase that are off by default because nothing is plugged into them, not
# because a flag says so.
#
# It matters which side registers this one. The ledger is registered by the
# adapter, because observation is harmless and should always run. The navigator
# is registered by the commercial app, because it changes what the claw does and
# therefore needs a switch — and the switch is that app's business, not the
# engine's. A fork gets a working navigator and decides for itself.
_NAVIGATOR = None
# How many times the claw has asked for directions this run, and how many times
# it may. The count is here rather than on the ledger because the loop has to
# read it between vacancies to decide whether to carry on, and reading the
# ledger from a frame that does not own the run is the ambient-state mistake
# this project keeps paying for.
_ASKED = 0
_BUDGET = 0


def set_navigator(fn, budget: int = 0) -> None:
    """Once per session, by whoever decides the claw may ask for directions.

    fn(node, detail, candidates) -> index into candidates, or None.

    An index rather than an element: the model chooses among things the caller
    already found and can already address, so its answer cannot name anything
    the caller did not offer. That is the same contract ask_modal_action has
    used since it was written, and the reason it is the one LLM call on this
    path that has never clicked something unexpected.

    `budget` is how many questions this run may ask. NOT a throttle on a run
    that is merely having a bad day: a site that renamed one address makes every
    vacancy ask once, and the whole point of asking is to carry that run to the
    end rather than abandon a person's applications. It is set high enough for
    that on purpose, and catches the other thing — several nodes broken at once,
    each vacancy asking three or four times, a bill that compounds while nobody
    is watching. 0 means unlimited, which is what a run with no navigator has.
    """
    global _NAVIGATOR, _ASKED, _BUDGET
    _NAVIGATOR = fn
    _ASKED = 0
    _BUDGET = max(0, int(budget))


def jam(node: str, detail: str = "", *, scope=None, candidates=None) -> Optional[object]:
    """The claw could not decide at `node`. Records it and returns None.

    Returning None rather than raising is deliberate and is what keeps this
    change free of behaviour: every call site already handles the None or False
    it was returning before, and adding this line changes nothing about which
    branch runs.

    `scope` is the Page or Frame the decision was being made in. Nothing here
    reads it; the debug observer does, to capture what was on screen.

    `candidates` is what the caller could address at the moment it stopped —
    supplied by the caller rather than gathered here, because the caller is the
    one that knows its own DOM. With none supplied there is nothing to choose
    between and the navigator is not asked, whatever is registered.

    Returns the navigator's choice — an index into `candidates` — or None. Every
    call site handles None already, because None is what this returned before
    anything could answer.
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

    if _NAVIGATOR is None or not candidates:
        return None
    global _ASKED
    if _BUDGET and _ASKED >= _BUDGET:
        # Said every time rather than once: the loop stops on its own terms and
        # this line is what tells the person which node spent the run.
        print(f"   navigator budget spent ({_ASKED}/{_BUDGET}) — not asking about {node}")
        return None
    _ASKED += 1
    try:
        picked = _NAVIGATOR(node, detail, candidates)
    except Exception as e:
        # A navigator is a fallback for a claw that is already stuck. Failing to
        # get directions leaves it exactly as stuck as it was, which is the
        # outcome the call site is written for.
        print(f"   navigator failed at {node} ({e}) — leaving the jam as it is")
        return None
    if picked is None:
        # Said, because it is the answer this call exists to allow: "none of
        # these" is a motivated refusal, and a log that shows the question and
        # no answer reads the same as a navigator that fell over.
        print(f"   navigator: none of the {len(candidates)} offered at {node} is the one")
        return None
    if (not isinstance(picked, int) or isinstance(picked, bool)
            or not (0 <= picked < len(candidates))):
        if picked is not None:
            print(f"   navigator answered {picked!r} at {node}, which is not one of "
                  f"the {len(candidates)} offered — ignored")
        return None
    print(f"   navigator picked #{picked} of {len(candidates)} at {node}")
    return picked


def navigator_spend() -> tuple:
    """(asked, budget) for this run. Read by the loop between vacancies, which
    is the only frame entitled to decide that a run has cost enough."""
    return _ASKED, _BUDGET
