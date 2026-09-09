"""The instrument counts calls per scenario, and counts the two dimensions apart.

Written after measuring 240 real vacancies and 422 calls (2026-08-18 →
2026-09-09), not before: every behaviour pinned here exists because the
measurement showed a way of counting that would have been wrong.

  · A cached score fired no call. Four of the 240 showed `cover:1` alone, and
    folding a cache hit into "no call needed" turns a healthy run into a
    deviation. It belongs in the shape and in the outcomes, differently.
  · A score call that raised is not the absence of a call. Ten of the 240 spent
    one and got `skipped_llm_unavailable`.
  · A CV parse served on another request lands in whatever vacancy is open,
    because the ledger is process-global. Three of the 240 were mangled that way.
  · One expected shape per scenario would have flagged 8.5% of good chat runs,
    which is the modal hh throws in front of roughly one in twelve.

With the envelopes as declared, 225 of the 231 measured vacancies that fall
under a key match an accepted shape, and all 6 that do not are the same v3
scorer fallback paying for a vacancy twice.
"""
import sys
from pathlib import Path
from unittest.mock import patch

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

from utils.call_ledger import CallLedger, OUTCOMES, shape_of

with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from adapters.hh.call_envelopes import CALL_ENVELOPES, NOT_A_VACANCY_CALL

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


def ledger():
    return CallLedger(CALL_ENVELOPES, NOT_A_VACANCY_CALL)


def one(led, scenario, form_type, *calls):
    """One vacancy. Each call is a type, or (type, 'cached'/'fail')."""
    led.begin_vacancy()
    for c in calls:
        if isinstance(c, tuple) and c[1] == "cached":
            led.note_cache_hit(c[0])
        elif isinstance(c, tuple) and c[1] == "fail":
            led.note_attempt(c[0])            # attempt, no outcome
        elif isinstance(c, tuple):
            led.note_attempt(c[0]); led.note_outcome(c[0], c[1])
        else:
            led.note_attempt(c); led.note_outcome(c, "ok")
    led.end_vacancy(scenario, form_type)


# ── the shape is order-independent ───────────────────────────────────────────
check("a shape is sorted, so call order is not mistaken for a change",
      shape_of([("cover", "ok"), ("score", "ok")]) == shape_of([("score", "ok"), ("cover", "ok")])
      == "cover:1|score:1")
check("a vacancy that made no call has a shape too", shape_of([]) == "none")

# ── cache: in the shape, apart in the outcomes ───────────────────────────────
led = ledger()
one(led, "chat_cover_sent", "chat_interface", ("score", "cached"), "cover")
s = led.run_summary()
check("a cached call still counts toward the shape the chain needed",
      s["shapes"]["chat_cover_sent|chat_interface"] == {"cover:1|score:1": 1})
check("and is counted apart, as cached", s["outcomes"] == {"ok": 1, "cached": 1})
check("so a cache hit is not a deviation", s["breaches"] == [])

# ── a failure is an attempt nobody answered ──────────────────────────────────
led = ledger()
one(led, "skip", None, ("score", "fail"))
s = led.run_summary()
check("an attempt with no outcome is counted as failed", s["outcomes"] == {"failed": 1})
check("and the call it spent is still in the shape",
      s["shapes"]["skip|-"] == {"score:1": 1})

# ── truncation is its own outcome ────────────────────────────────────────────
led = ledger()
one(led, "skip", None, ("score", "truncated"))
check("a truncated answer is neither a success nor a failure",
      led.run_summary()["outcomes"] == {"truncated": 1})
check("every outcome the ledger can record is declared",
      set(OUTCOMES) == {"ok", "cached", "truncated", "failed"})

# ── what does not belong to the chain is not counted ─────────────────────────
led = ledger()
one(led, "chat_cover_sent", "chat_interface", "score", "resume_parse", "cover")
s = led.run_summary()
check("a CV parse that merely happened during a vacancy is left out of the shape",
      s["shapes"]["chat_cover_sent|chat_interface"] == {"cover:1|score:1": 1})
check("and out of the call count", s["calls"] == 2)

# ── the envelope is a set, and it is not a rubber stamp ──────────────────────
led = ledger()
one(led, "chat_cover_sent", "chat_interface", "score", "cover")
one(led, "chat_cover_sent", "chat_interface", "score", "modal_action", "cover")
check("both shapes hh actually produces for a chat application are accepted",
      led.run_summary()["breaches"] == [])

led = ledger()
one(led, "chat_cover_sent", "chat_interface", "score", "score", "cover")
b = led.run_summary()["breaches"]
check("a vacancy scored twice is a deviation, not a variant",
      len(b) == 1 and b[0]["got"] == "cover:1|score:2" and b[0]["n"] == 1)
check("the deviation says what was expected instead",
      "cover:1|score:1" in b[0]["want"])

led = ledger()
for _ in range(3):
    one(led, "chat_cover_sent", "chat_interface", "score", "score", "cover")
check("identical deviations are one line with a count",
      led.run_summary()["breaches"] == [{"scenario": "chat_cover_sent|chat_interface",
                                         "got": "cover:1|score:2",
                                         "want": "cover:1|score:1 or cover:1|modal_action:1|score:1",
                                         "n": 3}])

# ── the key needs both halves ────────────────────────────────────────────────
led = ledger()
one(led, "chat_cover_sent", "employer_questions", "score", "fill_form", "cover")
check("the same scenario over a questionnaire is three calls and still clean",
      led.run_summary()["breaches"] == [])
led = ledger()
one(led, "chat_cover_sent", "chat_interface", "score", "fill_form", "cover")
check("and the same three calls over a chat form are not",
      len(led.run_summary()["breaches"]) == 1)

# ── a scenario nobody declared is recorded, never judged ─────────────────────
led = ledger()
one(led, "some_scenario_from_a_future_handler", "chat_interface", "score")
s = led.run_summary()
check("an undeclared scenario is still counted",
      s["shapes"]["some_scenario_from_a_future_handler|chat_interface"] == {"score:1": 1})
check("but never reported as a deviation — there is nothing to deviate from",
      s["breaches"] == [])

# ── the row that leaves the machine carries nothing about anybody ────────────
led = ledger()
one(led, "chat_cover_sent", "chat_interface", "score", "cover")
flat = repr(led.run_summary())
check("the run row is counts and scenario names, nothing else",
      set(led.run_summary()) == {"vacancies", "calls", "shapes", "outcomes", "breaches"})
for forbidden in ("http", "hh.ru", "vacancy_id", "cover_text", "company"):
    check(f"the run row carries no {forbidden}", forbidden not in flat)

# ── the declared table itself ────────────────────────────────────────────────
# The whole repo, not just core/llm_agent.py: `resume_parse` is tagged at its own
# call site in api.py, through GatewayClient, and a table checked against the
# agent alone would call the engine's own vocabulary a typo. Same sweep
# test_llm_call_observability.py uses for the same reason.
import re
DECLARED_CALL_TYPES = set()
for _py in _ENGINE.rglob("*.py"):
    if any(part == "venv" or part.startswith(".") for part in _py.parts) or _py.parts[-2] == "tests":
        continue
    DECLARED_CALL_TYPES |= set(re.findall(r'call_type=["\']([a-z_]+)["\']',
                                          _py.read_text(encoding="utf-8")))
used = set()
for shapes in CALL_ENVELOPES.values():
    for shape in shapes:
        for part in shape.split("|"):
            used.add(part.split(":")[0])
check(f"every call type in the envelope table is one the agent actually makes "
      f"(unknown: {sorted(used - DECLARED_CALL_TYPES)})", used <= DECLARED_CALL_TYPES)
check("what the ledger is told to ignore is also a real call type",
      set(NOT_A_VACANCY_CALL) <= DECLARED_CALL_TYPES)
check("every envelope key carries both halves",
      all("|" in k for k in CALL_ENVELOPES))
check("no envelope is empty — a key with no accepted shape would reject everything",
      all(len(v) > 0 for v in CALL_ENVELOPES.values()))

# ── the wiring: the agent actually reports, and reports the right thing ──────
# The ledger could be perfect and hear nothing. Fired against a stub client, so
# this is the real _chat_completion path with no network — the same harness
# test_llm_call_observability.py uses for the temperature table.
import io
import os
from contextlib import redirect_stdout
from utils import call_ledger as _ledger_mod

# LLMAgent checks for this in __init__, i.e. at call time — the import-time
# patch.dict above does not reach these constructions.
os.environ.setdefault("LLM_API_KEY", "test")

with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        from core.llm_agent import LLMAgent


class _StubClient:
    """Answers like OpenAI's SDK, with the finish_reason this case wants."""

    def __init__(self, finish_reason="stop"):
        self._finish = finish_reason

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, *, model, messages, max_tokens, temperature=None):
        _msg = type("_M", (), {"content": "{}"})
        _choice = type("_C", (), {"message": _msg(), "finish_reason": self._finish})
        return type("_R", (), {"choices": [_choice()], "id": None, "usage": None})()


led = ledger()
led.begin_vacancy()
_ledger_mod.set_ledger(led)
try:
    for ct in ("score", "cover"):
        agent = LLMAgent(data_dir=_ENGINE)
        agent._client_cache, agent._client_cache_key = _StubClient(), agent.api_key
        with redirect_stdout(io.StringIO()):
            agent._chat_completion(model="m", messages=[{"role": "user", "content": "hi"}],
                                   max_tokens=10, call_type=ct)
finally:
    _ledger_mod.set_ledger(None)
led.end_vacancy("chat_cover_sent", "chat_interface")
s = led.run_summary()
check("a real call through the agent reaches the ledger",
      s["shapes"] == {"chat_cover_sent|chat_interface": {"cover:1|score:1": 1}})
check("and is counted as answered", s["outcomes"] == {"ok": 2})

led = ledger()
led.begin_vacancy()
_ledger_mod.set_ledger(led)
try:
    agent = LLMAgent(data_dir=_ENGINE)
    agent._client_cache, agent._client_cache_key = _StubClient("length"), agent.api_key
    with redirect_stdout(io.StringIO()):
        agent._chat_completion(model="m", messages=[{"role": "user", "content": "hi"}],
                               max_tokens=10, call_type="score")
finally:
    _ledger_mod.set_ledger(None)
led.end_vacancy("skip", None)
check("a reply cut off at max_tokens arrives as truncated, not as a success",
      led.run_summary()["outcomes"] == {"truncated": 1})

# No ledger set is the normal case for a CLI run and for a fresh clone.
_ledger_mod.set_ledger(None)
agent = LLMAgent(data_dir=_ENGINE)
agent._client_cache, agent._client_cache_key = _StubClient(), agent.api_key
try:
    with redirect_stdout(io.StringIO()):
        agent._chat_completion(model="m", messages=[{"role": "user", "content": "hi"}],
                               max_tokens=10, call_type="score")
    check("with no ledger set, a call still just works", True)
except Exception as exc:
    check(f"with no ledger set, a call still just works (raised {exc})", False)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
