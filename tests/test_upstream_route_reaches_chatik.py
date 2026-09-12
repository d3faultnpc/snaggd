"""The route that delivered the letter reaches the chatik layer by name.

A questionnaire page can carry hh's own letter field; the letter typed there is
delivered on submit, and hh then offers no "add a cover" control in the chat.
Until 2026-09-12 the chat layer knew only "was it the modal" (a boolean), so a
questionnaire delivery reached it as "nothing delivered", it jammed on the
absent control, asked the navigator, and filed the vacancy as no-cover —
2026-09-12 vacancy #4, the first navigator call this project ever made, spent
on a control that had no reason to exist.

Driven through _process_vacancy_loop with the handlers stubbed: layer 0 claims
the route, layer 1 must be told which one.
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


class _Questionnaire:
    """Layer 0: submits, claims the questionnaire route, hands on to chatik."""
    def process(self, page, **kwargs):
        return ProcessResult(success=True, status="applied", reason="stub",
                             scenario="questions_submitted",
                             details={"cover_delivered": "questionnaire",
                                      "cover_length": 5, "cover_text": "hello"},
                             is_terminal=False, goal_reached=True)

    def verify_submission(self, page):
        return True


class _Chat:
    """Layer 1: records what it was told and finishes."""
    seen = {}

    def process(self, page, **kwargs):
        _Chat.seen = dict(kwargs)
        return ProcessResult(success=True, status="applied_via_questionnaire", reason="stub",
                             scenario="questionnaire_cover_sent", is_terminal=True,
                             goal_reached=True)

    def verify_submission(self, page):
        return True


with tempfile.TemporaryDirectory() as tmp:
    with patch("core.llm_agent.OpenAI"):
        a = HHAdapter(data_dir=Path(tmp))
    forms = [FormType.EMPLOYER_QUESTIONS, FormType.CHAT_INTERFACE]
    handlers = {FormType.EMPLOYER_QUESTIONS: _Questionnaire(), FormType.CHAT_INTERFACE: _Chat()}
    a._handle_resume_chooser = lambda page, vid: None
    a._dismiss_blocking_modal = lambda page, **kw: False
    a.detector.detect = lambda page: FormInfo(form_type=forms.pop(0), input_count=0)
    a.handlers.get_handler = lambda ft: handlers[ft]
    result, first = a._process_vacancy_loop(page=object(), vacancy_text="", vacancy_id="1",
                                            index=1, debug=False, session_dir=None)

check("the chat layer is told the route by name",
      _Chat.seen.get("cover_delivered_upstream") == "questionnaire")
check("and the older boolean says this was not the modal",
      _Chat.seen.get("cover_sent_via_modal") is False)
check("the record carries the delivery from the layer that made it",
      (result.details or {}).get("cover_delivered") == "questionnaire"
      and (result.details or {}).get("cover_text") == "hello")
check("filed under the questionnaire's own form", first == "employer_questions")

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
