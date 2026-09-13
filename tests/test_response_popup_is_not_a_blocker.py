"""hh's own response popup is the application, not a pop-up to be dismissed.

2026-09-11, vacancy #2 (137207876): the Apply click opened hh's response
modal in its collapsed state — resume card, "Добавить сопроводительное",
"Откликнуться", no visible textarea. The detector cannot name that state
(hh_modal_step1 is "a dialog with a textarea"), so the dismisser took it for
an unrecognised pop-up and asked the model which of the modal's own buttons to
press. The model refused; detection then fell through to the page under the
overlay and the run filled a questionnaire nobody could click. Three calls
spent, the vacancy flagged for retry — to spend them again.

The popup is known by its submit's address, which every state of it carries.
It is never a blocker and never a question for the model. What it may need is
its letter field revealed, which is a click at an address the config has held
since 2026-05 and nothing read: [data-qa="add-cover-letter"].
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
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
    from adapters.hh.dom import RESPONSE_POPUP_MARKER, is_response_popup
    from config import SELECTORS


class _El:
    def __init__(self, visible=True):
        self.visible, self.clicks = visible, 0

    def is_visible(self): return self.visible
    def click(self, timeout=None): self.clicks += 1


class _Dialog:
    """hh's response popup, collapsed: submit + add-cover control, no textarea yet."""
    def __init__(self, expands=True):
        self.submit, self.toggle, self.expands = _El(), _El(), expands
        self.textareas = []
        self.waited = []

    def query_selector(self, sel):
        return self.submit if sel == RESPONSE_POPUP_MARKER else None

    def query_selector_all(self, sel):
        if sel == 'textarea':
            return list(self.textareas)
        if sel == SELECTORS['popup_add_cover']:
            return [self.toggle]
        return []

    def wait_for_selector(self, sel, timeout=None, state=None):
        self.waited.append(sel)
        if self.expands and sel == 'textarea':
            self.textareas.append(_El())
            return self.textareas[-1]
        raise TimeoutError("no field")

    def inner_text(self): return "Отклик на вакансию"


class _Page:
    pass


check("the popup is known by its submit's address", is_response_popup(_Dialog()))


def _adapter(tmp):
    with patch("core.llm_agent.OpenAI"):
        a = HHAdapter(data_dir=Path(tmp))
    # Any question to the model here is the failure this test exists for.
    class _NeverAsk:
        def ask_modal_action(self, *a, **k):
            raise AssertionError("the model was asked about hh's own modal")
    a.llm_cover._agent = _NeverAsk()
    return a


with tempfile.TemporaryDirectory() as tmp:
    a = _adapter(tmp)
    d = _Dialog()
    with patch("adapters.hh.adapter.find_topmost_dialog", return_value=d):
        with redirect_stdout(io.StringIO()):
            handled = a._dismiss_blocking_modal(_Page(), index=2)
    check("the dismisser leaves hh's own modal to the form loop", handled is False)
    check("and reveals the collapsed letter field on the way", d.toggle.clicks == 1 and d.waited == ['textarea'])
    check("so a textarea is now on the dialog for the detector to see", len(d.textareas) == 1)

    # Already expanded: nothing to click.
    d2 = _Dialog(); d2.textareas.append(_El())
    with patch("adapters.hh.adapter.find_topmost_dialog", return_value=d2):
        with redirect_stdout(io.StringIO()):
            a._dismiss_blocking_modal(_Page(), index=3)
    check("an already-open letter field is not clicked at", d2.toggle.clicks == 0)

    # The click reveals nothing: said, not raised, and still not the model's problem.
    d3 = _Dialog(expands=False)
    buf = io.StringIO()
    with patch("adapters.hh.adapter.find_topmost_dialog", return_value=d3):
        with redirect_stdout(buf):
            handled = a._dismiss_blocking_modal(_Page(), index=4)
    check("a control that reveals no field is reported", "no field appeared" in buf.getvalue())
    check("and the modal is still not handed to the model", handled is False)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
