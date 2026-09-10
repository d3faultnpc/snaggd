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

ONE NODE SO FAR, AND ON PURPOSE
-------------------------------
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
}

_PURPOSE = {
    "data_collector_close": (
        "pick the control that closes this without saving anything to the CV"
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
