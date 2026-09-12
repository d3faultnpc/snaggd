"""The form a vacancy is filed under is the first one the detector named.

Not "the one at layer 0". The resume chooser can consume layer 0 whole — it
submits hh's modal and the loop `continue`s past detection — and until
2026-09-12 that left first_form_type at its placeholder for the vacancy. Both
the record and the call ledger file the vacancy under it, so across the two
debug runs of 2026-09-10 and 2026-09-11 four of 34 records were keyed
`chat_cover_sent|unknown` (and one `skip|unknown`): forms the detector had
named one layer later, judged against no envelope at all.

Driven, not grepped: _process_vacancy_loop with the chooser, the dismisser, the
detector and the handler stubbed on the instance. No browser, no page.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

os.environ.setdefault("LLM_API_KEY", "test")

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


with patch("core.llm_agent.OpenAI"):
    from adapters.hh.adapter import HHAdapter
    from adapters.hh.handlers.base import FormInfo, FormType, ProcessResult


class _Handler:
    def __init__(self, status, scenario):
        self.status, self.scenario = status, scenario

    def process(self, page, **kwargs):
        return ProcessResult(success=True, status=self.status, reason="stub",
                             scenario=self.scenario, is_terminal=True, goal_reached=True)

    def verify_submission(self, page):
        return True


def _loop(chooser_answers, detected, handler):
    """Run the loop with these chooser answers per layer and these detections."""
    with tempfile.TemporaryDirectory() as tmp:
        with patch("core.llm_agent.OpenAI"):
            a = HHAdapter(data_dir=Path(tmp))
        answers = list(chooser_answers)
        forms = list(detected)
        a._handle_resume_chooser = lambda page, vid: answers.pop(0) if answers else None
        a._dismiss_blocking_modal = lambda page, **kw: False
        a.detector.detect = lambda page: FormInfo(form_type=forms.pop(0), input_count=0)
        a.handlers.get_handler = lambda ft: handler
        return a._process_vacancy_loop(page=object(), vacancy_text="", vacancy_id="1",
                                       index=1, debug=False, session_dir=None)


chat = _Handler("applied_via_chat", "chat_cover_sent")

# The case that produced the unknowns: the chooser submits hh's modal at layer 0,
# the first detection happens at layer 1.
result, first = _loop(["submitted", None], [FormType.CHAT_INTERFACE], chat)
check("a chooser that consumes layer 0 does not leave the form type at its placeholder",
      first == "chat_interface")
check("and the loop still ends where the handler ended it", result.status == "applied_via_chat")

# The ordinary case: first detection at layer 0 is still the one that counts.
result, first = _loop([None, None], [FormType.EMPLOYER_QUESTIONS, FormType.CHAT_INTERFACE],
                      _Handler("applied_via_chat", "chat_cover_sent"))
check("with no chooser, the first detection is layer 0's", first == "employer_questions")

# The chooser refuses before anything is detected: there is no form to name.
result, first = _loop(["blocked"], [], chat)
check("a vacancy that never reached detection carries None, not the string 'unknown'",
      first is None)
check("which the ledger files as `skip|-`, the key every other undetected skip uses",
      f"{result.scenario}|{first or '-'}" == "skip|-")

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
