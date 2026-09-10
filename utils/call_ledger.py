"""What the chain of LLM calls did, per scenario, for one run.

Counts CALLS, not tokens. Tokens are a derivative — they move with the length of
a posting and with the model, not with the chain changing shape. Each scenario
has a known composition and a narrow range, so a deviation from that range is
the signal, and it is visible before the quality of an application drops.

Measured before this was written, over 240 vacancies and 422 calls of real runs
(2026-08-18 → 2026-09-09). The four scenarios that carry 80% of vacancies each
resolve to one shape 90% of the time — a run of `score:1|cover:1` for a chat
application, one call for a score-skip. See adapters/hh/call_envelopes.py for
the table and the provenance.

TWO DIMENSIONS, AND KEEPING THEM APART IS THE POINT
---------------------------------------------------
The SHAPE is which calls the chain needed. The OUTCOME is how each was served:
answered, served from cache, cut off at max_tokens, or attempted and failed.

They were one thing in the first sketch, and the measurement said no. Four of
the 240 vacancies showed `cover:1` with no score at all — the score had come
from llm_cache.json, so no call fired. Folding that into the shape makes a
healthy run look like a broken one, and an instrument that cries wolf on its
own cache is worse than none. A cached call counts toward the shape (the chain
needed it) and counts as `cached` in the outcomes (nobody paid for it).

The same measurement is why an attempt and a success are not the same event: ten
vacancies logged a score call that failed outright (`skipped_llm_unavailable`),
and a ledger that only heard about successes would have recorded no call at all
for a chain that spent one.

WHAT THIS IS NOT
----------------
Not a diagnosis. It says the chain changed, never what changed — that still
takes snapshots. And not a tripwire: a run outside its envelope has usually
still delivered, just more expensively, and the envelope tells us which entry
ate the money.

AMBIENT, AND THAT IS A KNOWN COST
---------------------------------
The vacancy boundary is known to the adapter; the calls are made four modules
away. Nothing is threaded between them, so the ledger is process-global, set
once per session — the same lifecycle and the same limit as
llm_agent.set_session_reporter(), and the same trap this project has paid for
before (a process-global default in a multi-profile process). It is safe here on
the same terms the reporter is: one apply session runs per process at a time.
Anything that changes must set the ledger, not read the global from a frame that
does not own it.
"""
from collections import Counter
from typing import Optional

# Every call the chain can make gets one of these; nothing else may appear in a
# shape. Not enforced against llm_agent's call types by import (utils/ must not
# depend on core/), but pinned by a test that reads both.
OUTCOMES = ("ok", "cached", "truncated", "failed")


def shape_of(calls) -> str:
    """A run of calls rendered as one comparable string: `cover:1|score:1`.

    Sorted by call type, so two vacancies that made the same calls in a
    different order have the same shape — order is a property of the form hh
    happened to show, not of the chain being healthy.
    """
    counts = Counter(call_type for call_type, _ in calls)
    return "|".join(f"{t}:{counts[t]}" for t in sorted(counts)) or "none"


class CallLedger:
    """One run. Records nothing that identifies a vacancy, a person or an
    employer — scenario names, call types and counts, and that is all. This is
    deliberate: the run row leaves the machine (see the app's own uploader), and
    what leaves has to be defensible without a second thought."""

    def __init__(self, envelopes: Optional[dict] = None,
                 ignore: Optional[tuple] = ()):
        # scenario key → the shapes that scenario is allowed to produce.
        # Declared, never learned: a learned envelope only catches a drift after
        # enough runs to move an average, while a declared one catches the first
        # occurrence, which is the whole point of having it before the fallbacks
        # are built.
        #
        # A SET, not one shape, because the measurement said the variation is
        # real and benign: hh throws a blocking modal in front of roughly one
        # chat application in twelve, and the extra modal_action call that
        # dismisses it is a healthy chain doing its job. Declaring one shape
        # would have called 8.5% of good runs a deviation, which is how an
        # instrument gets ignored and then switched off.
        self._envelopes = {k: tuple(v) if isinstance(v, (list, tuple)) else (v,)
                           for k, v in (envelopes or {}).items()}
        # Call types that belong to some other activity and merely happened
        # while a vacancy was open. The ledger is process-global (see the module
        # docstring), so a CV parse served on another request lands in whatever
        # vacancy segment is current — it did in 3 of 240 measured vacancies,
        # and each one turned a healthy shape into a deviation. Whoever owns the
        # loop declares what is not theirs; this module has no way to know.
        self._ignore = frozenset(ignore or ())
        self._shapes: dict = {}
        self._outcomes: Counter = Counter()
        self._breaches: list = []
        self._jams: Counter = Counter()
        self._vacancies = 0
        self._calls = 0
        self._current: Optional[list] = None

    # ── the vacancy boundary, marked by whoever owns the loop ────────────────
    def begin_vacancy(self) -> None:
        self._current = []

    def end_vacancy(self, scenario: str, form_type: Optional[str] = None) -> Optional[dict]:
        """Closes the segment and files it under `scenario|form_type`.

        Both halves, because the measurement needs both: `chat_cover_sent` over
        a chat_interface form is two calls and over an employer_questions form is
        three. The scenario alone cannot tell those apart, and calling a healthy
        three-call run a deviation is how an instrument gets switched off.
        """
        if self._current is None:
            return None
        calls, self._current = self._current, None
        self._vacancies += 1
        self._calls += len(calls)

        # An attempt nobody reported an outcome for is a call that raised.
        for entry in calls:
            if entry[1] is None:
                entry[1] = "failed"
            self._outcomes[entry[1]] += 1

        key = f"{scenario}|{form_type or '-'}"
        got = shape_of(calls)
        self._shapes.setdefault(key, Counter())[got] += 1

        accepted = self._envelopes.get(key)
        if accepted and got not in accepted:
            self._breaches.append({"scenario": key, "got": got,
                                   "want": " or ".join(accepted)})

        # Returned so a debug run can show the shape beside the vacancy that
        # produced it. None of the callers on a normal run read this.
        return {"key": key, "shape": got, "expected": not accepted or got in accepted}

    # ── call events, reported by llm_agent and llm_cover ────────────────────
    def note_attempt(self, call_type: str) -> None:
        """A call is about to be made. Outcome unknown until note_outcome, and
        one that never arrives is what makes a failure countable."""
        if self._current is None:
            return
        if call_type in self._ignore:
            return
        self._current.append([call_type or "unknown", None])

    def note_outcome(self, call_type: str, outcome: str) -> None:
        if self._current is None or call_type in self._ignore:
            return
        for entry in reversed(self._current):
            if entry[1] is None:
                entry[1] = outcome if outcome in OUTCOMES else "ok"
                return

    def note_jam(self, node: str) -> None:
        """A decision node could not decide. Counted per run rather than per
        vacancy: which node jams and how often is the question, and a jam is
        rare enough that a per-vacancy breakdown would be mostly zeroes.

        Counted outside a vacancy segment too — a node can jam during a search
        page or a login, and dropping those would flatter the numbers.
        """
        self._jams[node or "unknown"] += 1

    def note_cache_hit(self, call_type: str) -> None:
        """The chain needed this call and nobody paid for it. In the shape,
        because the chain needed it; `cached` in the outcomes, because that is
        the part worth knowing."""
        if self._current is None:
            return
        if call_type in self._ignore:
            return
        self._current.append([call_type or "unknown", "cached"])

    # ── the readout ─────────────────────────────────────────────────────────
    def run_summary(self) -> dict:
        """One row for one run. Small enough to ride inside a call that already
        happens once per run, which is why it is an aggregate and not a row per
        vacancy: per-vacancy telemetry would raise this app's Edge Function
        traffic by about half for nothing the aggregate does not answer."""
        breaches = Counter((b["scenario"], b["got"], b["want"]) for b in self._breaches)
        return {
            "vacancies": self._vacancies,
            "calls": self._calls,
            "shapes": {k: dict(v) for k, v in self._shapes.items()},
            "outcomes": {k: self._outcomes[k] for k in OUTCOMES if self._outcomes[k]},
            "breaches": [{"scenario": s, "got": g, "want": w, "n": n}
                         for (s, g, w), n in breaches.most_common()],
            # Which decision nodes could not decide, and how often. Empty is the
            # healthy answer and the common one; a name appearing here is where
            # the navigator will be worth plugging in first.
            "jams": dict(self._jams.most_common()),
        }


# ── The process-global, and the thin functions that tolerate its absence ─────
# Absence is the normal case, not a fault: the CLI sets no ledger, and a fresh
# clone of this engine has nothing that would.
_LEDGER: Optional[CallLedger] = None


def set_ledger(ledger: Optional[CallLedger]) -> None:
    """Once per session, by whoever owns the session. None clears it."""
    global _LEDGER
    _LEDGER = ledger


def get_ledger() -> Optional[CallLedger]:
    return _LEDGER


def note_attempt(call_type: str) -> None:
    if _LEDGER is not None:
        _LEDGER.note_attempt(call_type)


def note_outcome(call_type: str, outcome: str) -> None:
    if _LEDGER is not None:
        _LEDGER.note_outcome(call_type, outcome)


def note_cache_hit(call_type: str) -> None:
    if _LEDGER is not None:
        _LEDGER.note_cache_hit(call_type)


def note_jam(node: str) -> None:
    if _LEDGER is not None:
        _LEDGER.note_jam(node)
