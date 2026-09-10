"""Directions for the claw, on the surfaces where canon ran out.

The claw follows a route sheet and cannot interpret. When a decision node runs
out of addresses it jams, and a jam is where this gets asked. Nothing here
decides what to do — every node states its own goal, and the model is asked only
WHERE the control that serves it is. "Which control dismisses this without
saving" has a right answer on the page; "what should I do here" does not, and
asking the second is how one of hh's profile surveys got its Save button pressed
on 2026-08-11, writing work format, city and desired salary into a real profile.

Registered by whoever decides the claw may ask — which in this project is the
commercial app, behind its own feature register, never this module and never the
adapter. An engine with nothing plugged in behaves exactly as it did before this
file existed, and a fork can plug in its own.

TWO KINDS OF QUESTION
---------------------
Where the vocabulary is CLOSED, the model is told what it is looking at and
asked only where the control is. hh's own profile surveys are that: 47 keys
across four families, captured from a real page, and every family known in
advance. There is a right answer and the context names it.

Where it is OPEN — the vacancy loop, whatever hh shipped this week — the model
is told what the claw was trying to do and shown what it can address, and
"none of these" is an allowed answer. That last part is the difference between
a motivated refusal and a guess: a claw that reports it could not find the way
is telling the truth, and one that clicks the closest-looking thing on a real
employer's form is not.

Both are the same question shape. Neither asks what to do.

WHICH NODES, AND WHY THESE
--------------------------
data_collector_close is the only node whose vocabulary is CLOSED: 47 keys across
four families, captured from a real page on 2026-08-27. That makes it the only
one where the model can be told what it is looking at rather than asked to work
it out, and the only one that can be accepted by a fixture instead of by a live
run against a real employer's form.

It is also where being wrong is worst. Everywhere else in this loop a bad answer
costs an application; here it edits the person's actual resume.

A node with no entry here is not asked about. That is what makes this file safe
to grow one node at a time.
"""

# What the claw already knows about each surface. Supplied to the model so it
# resolves an address rather than guessing at a situation — the whole difference
# between a locator and a decider.
_CONTEXT = {
    "data_collector_close": (
        "This dialog is one of hh.ru's own profile-enrichment surveys "
        "(additionalDataCollector). It is NOT part of the job application: hh "
        "shows it over the application because the person is already there. "
        "There are four kinds — preferred work format, preferred work area, a "
        "salary-mismatch editor, and achievements — and every one of them writes "
        "to the person's real CV when saved.\n"
        "The survey has no skip button; it never had one. The only ways out are "
        "its own close control (an X, often icon-only and unlabelled) and, on the "
        "achievements kind, a Close on its preview.\n"
        "hh sometimes raises a second dialog over the survey — a confirm asking "
        "whether to save changes. When that is present, the way out is that "
        "dialog's own dismissal, not the survey's X underneath it.\n"
        "Anything that saves, adds or continues writes to the CV and is the "
        "wrong answer here, however obvious it looks."
    ),

    # ── open vocabulary: the vacancy loop ───────────────────────────────────
    # Both of these are where letters have actually been lost. On 2026-08-29 hh
    # renamed the chatik message field and ten applications went out with no
    # letter in one evening; and as of 2026-09-09, 32 applications on the live
    # profile had no letter at all — one in sixteen of everything it called
    # applied. They are also both inside the chatik iframe, which the
    # page-level snapshotter cannot see into — so when they break, the claw is
    # blind in exactly the place it needs to look.
    "add_cover_button": (
        "This is hh.ru's chat with an employer, opened right after an "
        "application was submitted. The application is already in. What is left "
        "is attaching a cover letter, and hh offers that as a control in the "
        "chat — historically labelled 'Добавить сопроводительное', though the "
        "label and the markup are hh's to change.\n"
        "The claw looked for that control at every address it knows and found "
        "none of them.\n"
        "Some conversations genuinely do not offer one. Saying so is a correct "
        "answer and a useful one; picking something that merely looks plausible "
        "sends the wrong thing to a real employer under the person's name."
    ),
    "cover_input": (
        "This is hh.ru's chat with an employer. The control that reveals the "
        "cover-letter field has already been pressed and the field it should "
        "have revealed was not found at any address the claw knows.\n"
        "What is wanted is the field the letter gets typed into. On hh this has "
        "historically been the chat's own message box rather than a separate "
        "one, so a message field is a plausible answer and not a mistake.\n"
        "If nothing here takes typed text, saying so is the right answer: the "
        "application is already submitted either way, and a letter typed into "
        "the wrong box is worse than a letter not typed."
    ),
}

_PURPOSE = {
    "data_collector_close": (
        "pick the control that closes this without saving anything to the CV"
    ),
    "add_cover_button": (
        "pick the control that opens the cover-letter field, or none if this "
        "conversation does not offer one"
    ),
    "cover_input": (
        "pick the field a cover letter should be typed into, or none if there "
        "is no such field here"
    ),
}


def make_navigator(agent):
    """A navigator bound to one LLM agent, for set_navigator().

    Returns None for any node it has no context for, which leaves that node
    exactly as jammed as it was — the state every call site already handles.
    """

    def navigate(node, detail, candidates):
        context = _CONTEXT.get(node)
        if context is None:
            return None
        return agent.locate_control(_PURPOSE[node], context, candidates)

    return navigate


def knows(node: str) -> bool:
    """Whether a node has directions available at all. For tests and for the
    caller that would rather not gather candidates for nothing."""
    return node in _CONTEXT
