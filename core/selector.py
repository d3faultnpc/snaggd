"""Whether to apply to a vacancy, decided in one place instead of three.

The decision used to live inside the apply loop as three `if`s interleaved with
narration and early returns. Nothing was wrong with it — this module is the same
logic, moved — but while it lived there it could not be replaced, compared or
tested without a browser. A run selects by an absolute threshold today; whether
it should instead take the best K of a window is an open product question, and
an open question needs something to swap.

The step is deliberately not pure of its own words. It returns the lines the
loop should say, rather than saying them, because the loop is what knows which
vacancy is on screen — and because a replacement policy should not have to
reimplement narration to be tried.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Verdict:
    """Apply, or don't and here is what to record and say.

    `status`, `reason` and `scenario` are what reaches applied_log.json and,
    through it, the History screen. They are part of the contract, not an
    implementation detail: a person reads them months later to understand why a
    vacancy they can still see was passed over.
    """
    apply: bool
    status: str = ""
    reason: str = ""
    scenario: str = ""
    log_line: str = ""
    gui_line: str = ""
    # Who the loop should attribute the line to. The two gates the model is
    # responsible for are its own; the threshold is the product's, and carries
    # the reporter's ordinary default.
    actor: str = "scan"
    extra: dict = field(default_factory=dict)


def threshold_selector(*, match_score, min_score, stop_match, stop_basis,
                       dry_run, matched_skills=()) -> Verdict:
    """Today's behaviour, unchanged, in the order the gates have always run.

    The order carries meaning and is not incidental. A blocked vacancy is
    reported as blocked even in a dry run: the block is a fact about the vacancy,
    while the dry run is a fact about how this session was launched. And the
    threshold is last because it is the weakest of the three — it says a match
    was thin, not that anything was wrong.
    """
    if stop_match:
        # A block resting on company knowledge is marked wherever it is shown.
        # The posting does not support it, so it is the one kind a person can
        # only check by looking the employer up.
        by_knowledge = stop_basis == "company_knowledge"
        mark = " · unconfirmed by the posting" if by_knowledge else ""
        return Verdict(
            apply=False, status="semantic_blocked", scenario="skip",
            reason=f"LLM detected blocked category: '{stop_match}'{mark}",
            log_line=(f"   🚫 semantic_blocked: LLM detected '{stop_match}'"
                      f"{' (company knowledge)' if by_knowledge else ''}"),
            gui_line=f"[BLCK] not a fit ({stop_match}){mark}", actor="llm",
        )

    if dry_run:
        return Verdict(
            apply=False, status="dry_run", scenario="dry_run",
            reason=f"Dry-run — score: {match_score}",
            log_line=f"   🔍 Dry-run: score={match_score}, skills={list(matched_skills)}",
            gui_line=f"Dry run — would score {match_score}%, not applying", actor="llm",
        )

    # `is not None` guards the case where scoring produced nothing at all: a
    # missing score is not a low one, and treating it as below the threshold
    # would report a failure to measure as a measurement.
    if match_score is not None and match_score < min_score:
        return Verdict(
            apply=False, status="skipped_score", scenario="skip",
            reason=f"Score {match_score} below threshold {min_score}",
            log_line=f"   ⏭ Score {match_score} < min {min_score} — skipping",
            gui_line=f"[SKIP] match {match_score}% below your threshold",
        )

    return Verdict(apply=True)
