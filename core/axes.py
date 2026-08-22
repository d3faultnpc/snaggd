"""The axes a vacancy is judged on, and the arithmetic that turns labels into a score.

Why axes at all (2026-08-22):

Scoring used to be one call answering seven questions and returning one number. That
coupling was measured, not theorised: lifting the arithmetic out of the prompt took
zero-scores from 3/6 to 0/6 AND dropped stop-category blocking from 4/4 to 2/4 in the
same edit. Touching anything in that call touched everything.

The first attempt at a fix split the call in two — one pass to list the vacancy's
requirements, a second to judge them. It failed for a reason worth keeping written
down: a requirement list built without the candidate present has nothing to rank
against, so the model enumerated and paraphrased instead. 11 of 12 answers hit the
output ceiling, 12 of 12 requirements came back "unspecified", and the output grew
5.9x — we were paying to have the vacancy read back to us in its own words.

So: one call, and a FIXED set of axes. Fixed is the load-bearing word. The ceiling
failure above is only possible when the model decides how many things to emit; with a
closed axis set the answer's length is known before it is generated.

Why these five, and not the profile's own sections:

The frame's sections were declared to answer "who may read this", not "what can be
judged on this" — and a vacancy's demands do not decompose along the candidate's
section list ("team lead experience, 5+ years" belongs to which section?). Making the
model choose reintroduces exactly the undetermined decision this module exists to
remove. These five are stated from the vacancy's side and survive from a dentist to an
astronaut, which is the actual requirement: the app's users are not all product
managers, and the previous rubric was written against one hand-written profile.

Nothing here calls a model. Labels come in, a number goes out, and every step is
inspectable — which is the point. The model observes; the code decides.
"""

from dataclasses import dataclass, field


# Ordered: this is also the order axes are presented and reported in.
#
# Each axis names what a vacancy can demand, and points at the evidence in the
# profile that answers it. The mapping is one-way on purpose: an axis may read
# several sections, but no section decides an axis by itself.
AXES: dict[str, tuple[str, ...]] = {
    # `skills` and `languages` sit together because the split between them is not
    # reliably made by anything upstream, and their weight is the same anyway. A
    # language IS a skill a posting asks for by name; a translator's English and a
    # developer's SQL are the same kind of claim about the same kind of demand.
    "skills": ("skills", "languages"),
    # Kept separate from skills only because the profile keeps them separate. The
    # split is unstable in real data — one live profile has 12 skills and 4 tools
    # correctly apart, another has 34 skills and 0 tools with MS Outlook, Lotus
    # Notes and Adobe Photoshop among the "skills" — so both pockets are shown and
    # both weigh the same. We do not make people follow our format.
    "tools": ("tools",),
    # Volunteering belongs here and not in its own axis: for a junior, a student, or
    # someone returning to work it is not a footnote, it is the only evidence of
    # having done the thing. None of the 13 live profiles has any — every one of
    # them is a mid-career person with employment history, which is a property of
    # our sample, not of the world.
    "experience": ("employment", "project", "volunteering"),
    "education": ("education", "publication"),
    # Its own axis, and the reason is arithmetic rather than subject matter — see
    # NON_COMPENSABLE below.
    "credential": ("credential", "award"),
}


# The vocabulary of a verdict. Anything outside it is not a label.
#
# A closed set of named grades rather than a free number: an absolute 0-100 scale
# drifts with wording and cannot be compared between professions (65 means something
# different for a dentist and an analyst, and the threshold is one number for both),
# while a fixed set of anchored grades is relative to the candidate by construction.
LABELS: tuple[str, ...] = ("ideal", "strong", "neutral", "weak", "miss")

# What each grade is worth, before weighting. NEUTRAL IS ABSENT FROM THIS TABLE ON
# PURPOSE — see _in_play below.
LABEL_VALUES: dict[str, float] = {
    "ideal": 1.0,
    "strong": 0.75,
    "weak": 0.25,
    "miss": 0.0,
}

# Per-axis weight. All equal today, and that is a starting position, not a finding:
# calibration needs a frozen axis set and a model whose own spread is smaller than
# the effect being measured. Neither holds yet — the scorer's standard tier runs on
# a model that returned scores 85 points apart for the same input at temperature 0.
# This table is where calibration will land when it can happen; it has one home so
# that it cannot quietly grow several.
AXIS_WEIGHTS: dict[str, float] = {axis: 1.0 for axis in AXES}

# An axis whose `miss` cannot be bought back by strength elsewhere.
#
# Only credential, and the reason is not that it matters more. Every other axis is
# compensable in the ordinary way — thin tooling is answered by deep experience. A
# credential is a gate: a driver without licence category B cannot take the job at
# any amount of experience, and neither can a nurse without registration. Treating
# that as "a few points off" states something false about the world.
#
# This is reported, not enforced. `non_compensable` comes back beside the score and
# the decision layer acts on it — the same separation stop_match already has, and
# the same reason: a rule that silently zeroed a score would be indistinguishable
# from the arithmetic bug that drove real matches to zero in the first place.
NON_COMPENSABLE: frozenset = frozenset({"credential"})


@dataclass
class Verdict:
    """What the arithmetic concluded, and enough of its working to argue with it."""

    score: int | None
    """0-100, or None when no axis was in play — see Verdict.in_play."""

    non_compensable: tuple[str, ...] = ()
    """Axes that came back `miss` and cannot be compensated. Advisory: the decision
    layer decides what to do, this only reports that the arithmetic cannot."""

    in_play: tuple[str, ...] = ()
    """Axes that counted. An axis labelled `neutral` is not here."""

    neutral: tuple[str, ...] = ()
    """Axes the vacancy did not ask about."""

    unknown_labels: dict = field(default_factory=dict)
    """Axis -> the value returned, for values outside LABELS. Discarded, but counted:
    a model that starts inventing grades has to be visible, not silently averaged."""


def normalise_label(raw) -> str | None:
    """A label in the vocabulary, or None.

    None is the interesting answer — it means the model returned something that is
    not a grade, and the caller must not guess which one was meant. Guessing is how
    a scale acquires values nobody declared.
    """
    if raw is None:
        return None
    label = str(raw).strip().lower()
    return label if label in LABELS else None


def score_from_axes(labels: dict) -> Verdict:
    """Axis labels -> a score, with the working kept.

    `neutral` is excluded from the denominator rather than counted as half. This is
    the whole reason the grade exists: it means "this vacancy did not ask", and a
    vacancy that never mentions tooling must not drag a strong candidate toward the
    middle on an axis nobody raised. Excluding it makes the score a statement about
    what this posting actually demanded.

    It also removes the need to ask the model which requirements are mandatory. That
    question was asked once and failed completely — importance in real Russian
    postings is carried by structure ("Требования" vs "Будет плюсом"), not by the
    marker words a prompt can list, and 12 of 12 requirements came back
    "unspecified". Here the grade carries it instead: an axis the posting did not
    raise is `neutral`, so `miss` already means "asked for, and absent". Nothing has
    to be inferred twice.
    """
    in_play: list[str] = []
    neutral: list[str] = []
    unknown: dict = {}
    non_comp: list[str] = []
    total = 0.0
    weight_sum = 0.0

    for axis in AXES:
        if axis not in labels:
            continue
        label = normalise_label(labels[axis])
        if label is None:
            unknown[axis] = labels[axis]
            continue
        if label == "neutral":
            neutral.append(axis)
            continue
        weight = AXIS_WEIGHTS.get(axis, 1.0)
        total += LABEL_VALUES[label] * weight
        weight_sum += weight
        in_play.append(axis)
        if label == "miss" and axis in NON_COMPENSABLE:
            non_comp.append(axis)

    # No axis in play is not a score of zero. It is the absence of a judgement, and
    # saying "0" about it would put a real number on a question nobody answered —
    # which is what a score of 0 used to mean here, and why it was never possible to
    # tell a genuine mismatch from a model that had refused to work.
    score = round(100 * total / weight_sum) if weight_sum else None

    return Verdict(
        score=score,
        non_compensable=tuple(non_comp),
        in_play=tuple(in_play),
        neutral=tuple(neutral),
        unknown_labels=unknown,
    )


def validate_matched_skills(returned, declared) -> tuple[list, int]:
    """Keep only what the candidate actually declared. Returns (kept, dropped_count).

    Measured on a live log of 913 applications: the model emitted 205 distinct
    strings for a profile declaring 30 skills and tools, and only 1637 of 3824
    emissions (42.8%) were exact members of that list. It was not selecting from the
    person's document, it was paraphrasing it — which is why the dashboard's "top
    matches" is a frequency count over near-duplicates, and why its sibling
    aggregation needs a regular expression to guess which two free-text strings mean
    the same thing.

    Asking for verbatim members and checking membership here is the same technique
    that failed when it was pointed at the vacancy — quote the source, verify by
    substring. It failed there because a posting is long and open, so the model
    rewrote it and hit the ceiling. A candidate's own skill list is short and closed,
    and already in the prompt. Same technique, right target.

    The dropped count is not diagnostics-for-later. It is the health metric: the day
    it climbs, the model has gone back to paraphrasing, and the aggregate built on
    this field has quietly stopped meaning what it says.
    """
    allowed = {str(d).strip().lower(): str(d).strip()
               for d in (declared or []) if str(d).strip()}
    kept: list = []
    dropped = 0
    for item in (returned or []):
        key = str(item).strip().lower()
        if key in allowed and allowed[key] not in kept:
            kept.append(allowed[key])
        else:
            dropped += 1
    return kept, dropped
