"""run() executes far enough to set up, on stubs, with no browser.

Written because it was missing. On 2026-09-10 thirty lines went into this
two-hundred-line method and the only check they got was that the file parses —
one of them read a local variable nine lines above its assignment. Not a syntax
error, invisible to ast.parse and py_compile, and it raised the first time the
method actually ran: two seconds after login, before a single vacancy opened,
taking a live session down.

Every other test in this suite covers a function that can be called with
arguments. run() needs a browser, so nothing called it, so the one method that
orchestrates the whole run was the one nobody executed.

What this does NOT do is drive a run. It stubs the browser into finding no
vacancies, which makes run() take its first exit — and that exit is past the
setup block, which is all this needs to be worth having. What it pins is that
the method reaches a decision at all, and that what it registered on the way is
taken down again on the way out.
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
    from utils import call_ledger, navigation


class _Logger:
    """Only what run() reaches before its first exit."""

    def __init__(self):
        self.daily = []

    def load_applied_log(self):
        return []

    def log_daily(self, message):
        self.daily.append(message)

    def is_processed(self, url, log):
        return None

    def log_result(self, log, **kwargs):
        log.append(kwargs)


def _adapter(tmp):
    with patch("core.llm_agent.OpenAI"):
        a = HHAdapter(data_dir=Path(tmp))
    # No browser is started; get_vacancies is the only thing reached.
    a.get_vacancies = lambda target_url=None: []
    return a


registered = []

with tempfile.TemporaryDirectory() as tmp:
    adapter = _adapter(tmp)
    with patch.object(navigation, "set_jam_observer",
                      side_effect=lambda fn: registered.append(fn)):
        with patch("adapters.hh.adapter.set_jam_observer",
                   side_effect=lambda fn: registered.append(fn)):
            out = adapter.run(_Logger(), debug=True)

check("run() reaches a decision instead of raising on the way there", out == [])
check("the jam observer is registered during the run", registered and callable(registered[0]))
check("and cleared on the way out — including on an early exit",
      registered and registered[-1] is None)

# The debug branch is the one that crashed: it reads session_dir_base, which is
# assigned inside `if debug:`. A non-debug run must reach the same exit.
with tempfile.TemporaryDirectory() as tmp:
    adapter = _adapter(tmp)
    out = adapter.run(_Logger(), debug=False)
check("a non-debug run reaches the same decision", out == [])
check("and leaves no ledger behind it", call_ledger.get_ledger() is None)

# An early exit still has to leave a summary — a run that found nothing has a
# real answer (no vacancies, no calls), and it is not the same as no answer.
with tempfile.TemporaryDirectory() as tmp:
    adapter = _adapter(tmp)
    adapter.run(_Logger(), debug=False)
    summary = adapter.last_call_summary
check("an early exit still produces a run summary",
      isinstance(summary, dict) and summary.get("vacancies") == 0)
check("with nothing in it, which is the honest answer for a run that found nothing",
      summary.get("calls") == 0 and summary.get("jams") == {} and summary.get("shapes") == {})

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
