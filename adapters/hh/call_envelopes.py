"""How many LLM calls each hh scenario costs, measured rather than assumed.

Taken from 240 real vacancies across twelve run-days, 2026-08-18 → 2026-09-09,
by reading the run logs: the per-call console line carries the prompt's opening,
which names the call type, and the vacancy and scenario markers bracket it.

The script that did that reading was not kept — it lived in a session
scratchpad and was gone by 2026-09-12, and the logs of that window have since
rotated out — so the counts below are a dated measurement, not a reproducible
one. That is acceptable for the same reason the table is declared rather than
learned: it only has to be right enough to be wrong loudly, and since
2026-09-10 every run writes its own shapes to public.runs in the app's
Supabase, which is the measurement going forward. Re-derive from there, never
from this docstring.

WHY DECLARED AND NOT LEARNED
----------------------------
A learned envelope only notices a drift once enough runs have moved an average,
and by then a fallback has silently stopped firing on every vacancy in between.
A declared one is wrong the first time reality disagrees, which is the entire
reason for having it before any of the fallback work starts.

WHY A SET OF SHAPES
-------------------
Measured, not guessed. hh puts a blocking modal in front of about one chat
application in twelve, and the extra `modal_action` call that dismisses it is a
healthy chain. One shape per scenario would have flagged 8.5% of good runs.

WHAT THIS TABLE DELIBERATELY DOES NOT ACCEPT
--------------------------------------------
`score:2`. It appeared on 6 of the 240 and it is not benign: the v3 scorer
returns coverage=-1 on every axis, the code discards the result and falls back
to the axes scorer, and the vacancy is paid for twice. It was already known and
already undiagnosed; leaving it outside the envelope is what makes it countable
per run instead of by reading logs by hand.

Coverage of the table as declared: 231 of the 240 vacancies fall under a key
here, 225 of those match an accepted shape (97.4%), and all 6 that do not are
that same score:2 fallback. No other deviation was seen in the window.
"""

# Not part of any vacancy chain. The CV parse is served on its own request from
# the onboarding flow, and the ledger is process-global, so it lands in whatever
# vacancy happens to be open — it did in 3 of the 240, turning a healthy shape
# into a deviation each time.
NOT_A_VACANCY_CALL = ("resume_parse",)

# scenario|form_type → the shapes that pairing is allowed to produce.
# The key needs both halves: `chat_cover_sent` is two calls over a chat form and
# three over an employer questionnaire, and the scenario alone cannot tell them
# apart.
CALL_ENVELOPES = {
    # A letter delivered through chatik. n=82.
    "chat_cover_sent|chat_interface": (
        "cover:1|score:1",                  # 71
        "cover:1|modal_action:1|score:1",   # 7 — hh had a modal in the way
    ),
    # The same, behind an employer's questionnaire: one batched fill_form. n=26.
    "chat_cover_sent|employer_questions": (
        "cover:1|fill_form:1|score:1",                  # 25
        "cover:1|fill_form:1|modal_action:1|score:1",   # 1
    ),
    # A letter delivered in hh's own response modal, where it is mandatory. n=30.
    "hh_modal_with_cover|hh_modal_step1": (
        "cover:1|score:1",                  # 28
    ),
    # Scored and not applied to — below the bar, blocked, or filtered. n=71,
    # every single one a single score call. The cheapest scenario there is, and
    # the one where an extra call would be most obviously wrong.
    "skip|-": ("score:1",),
    "dry_run|-": ("score:1",),
    "error|-": ("score:1",),
    # Applied, no letter — the outcome this whole sprint exists to stop being
    # silent about. The shape is the same as a successful chain minus the cover
    # call, which is exactly what makes it visible here. n=12.
    "chat_no_cover|chat_interface": ("score:1",),
    "chat_no_cover|employer_questions": ("fill_form:1|score:1",),
    # Form rejected by hh after we filled it. n=3.
    "questions_validation_error|employer_questions": ("fill_form:1|score:1",),
    "questions_validation_error|hh_modal_step1": ("modal_action:1|score:1",),
}
