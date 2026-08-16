"""
Every LLM call goes through one gateway, and that gateway records what the call
actually did.

Two failures used to compound in silence. A reply cut off at max_tokens comes
back SHORT, not malformed — the tail is simply missing. json_repair then turns
that stump into a well-formed object, so by the time any caller sees it, a
complete answer and a beheaded one are indistinguishable. A CV parse runs at
max_tokens=2500 and is exactly where a rich resume loses its last cases.

The third check here is the reason the first two are worth anything: if a
caller can build its own client, the gateway sees nothing and these records are
a comfortable lie. The CV parse was that caller until 2026-08-16.

Run:  venv/bin/python3 tests/test_llm_call_observability.py
"""
import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("LLM_API_KEY", "test-key")

from core.llm_agent import (  # noqa: E402
    _CALL_TEMPERATURE, _PROSE_TEMPERATURE, _STRUCTURED_TEMPERATURE,
    GatewayClient, LAST_CALL, LLMAgent, _note_call, _note_json_repair,
    _temperature_for,
)

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


# ── A truncated reply is announced, a complete one is not ────────────────────
print("\nTruncation is recorded and said out loud:")
LAST_CALL.clear()
_out = io.StringIO()
with redirect_stdout(_out):
    _note_call(model="m", max_tokens=2500, finish_reason="length", call_type="resume_parse")
check("finish_reason is kept, not dropped with the response object",
      LAST_CALL.get("finish_reason") == "length")
check("the ceiling that cut it off is named", "2500" in _out.getvalue())
check("and the message says the tail is LOST, not that the reply is broken",
      "cut off" in _out.getvalue() and "not malformed" in _out.getvalue())

LAST_CALL.clear()
_out = io.StringIO()
with redirect_stdout(_out):
    _note_call(model="m", max_tokens=400, finish_reason="stop", call_type="score")
check("a complete reply says nothing", _out.getvalue() == "")
check("a fresh call resets the repair flag", LAST_CALL.get("json_repaired") is False)


# ── A rescued reply is marked as rescued ─────────────────────────────────────
print("\nA JSON repair is recorded:")
_out = io.StringIO()
with redirect_stdout(_out):
    _note_json_repair("resume_parse")
check("the flag flips", LAST_CALL.get("json_repaired") is True)
check("and it says which call was rescued", "resume_parse" in _out.getvalue())

agent = LLMAgent(data_dir=_REPO_ROOT)
LAST_CALL.clear()
LAST_CALL["json_repaired"] = False
_out = io.StringIO()
with redirect_stdout(_out):
    _parsed = agent._parse_json('{"score": 80, "matched_skills": ["a", "b"', fallback={"score": None})
check("_parse_json marks the repair it just performed", LAST_CALL.get("json_repaired") is True)
check("and the repair does produce a usable object — which is why it hid",
      isinstance(_parsed, dict) and _parsed.get("score") == 80)


# ── The gateway is the only route out ────────────────────────────────────────
print("\nOne route out:")


class _RecordingAgent:
    def __init__(self):
        self.seen = None

    def _chat_completion(self, *, model, messages, max_tokens, call_type=None):
        self.seen = {"model": model, "max_tokens": max_tokens, "call_type": call_type}
        return '{"ok": true}'


_agent = _RecordingAgent()
_resp = GatewayClient(_agent, call_type="resume_parse").chat.completions.create(
    model="parser/model", messages=[{"role": "user", "content": "hi"}], max_tokens=2500,
)
check("GatewayClient hands the call to the gateway, parameters intact",
      _agent.seen == {"model": "parser/model", "max_tokens": 2500, "call_type": "resume_parse"})
check("and answers in the shape a raw client's caller already expects",
      json.loads(_resp.choices[0].message.content) == {"ok": True})

_own_clients = []
for _py in _REPO_ROOT.rglob("*.py"):
    # Source only: no venv, no tests, and nothing under a dot-directory —
    # .claude/worktrees holds whole checkouts of this repo, and scanning those
    # would report another checkout's code as a violation in this one.
    if any(p == "venv" or p.startswith(".") for p in _py.parts) or _py.parts[-2] == "tests":
        continue
    if _py == _REPO_ROOT / "core" / "llm_agent.py":
        continue  # the gateway itself — the one place a real client is built
    if "OpenAI(" in _py.read_text(encoding="utf-8"):
        _own_clients.append(str(_py.relative_to(_REPO_ROOT)))
check(f"no other module builds its own OpenAI client (found: {_own_clients or 'none'})",
      not _own_clients)


# ── The record of a blocked vacancy names what blocked it ────────────────────
# Structural, deliberately: building a real process_vacancy() run here would
# cost a browser. The claim is narrow — the field is in the dict that becomes
# the log entry — and the defect it guards against was exactly its absence:
# entries said stop_match: null about vacancies blocked on stop_match, so
# blocks could only be counted by regex over prose.
print("\nA blocked vacancy's record says what blocked it:")
_adapter_src = (_REPO_ROOT / "adapters" / "hh" / "adapter.py").read_text(encoding="utf-8")
_details_block = _adapter_src.split("score_details = {", 1)[-1].split("}", 1)[0]
check("stop_match is part of score_details", "'stop_match': stop_match" in _details_block)
check("and score_details is what a semantic block reports",
      "'status': 'semantic_blocked'" in _adapter_src and "'details': score_details" in _adapter_src)

# ── Temperature is stated per call type, never inherited from the provider ───
# Nothing set it until 2026-08-16, so everything ran at the provider's default
# (1.0 for most models): the same vacancy scored twice came back with different
# numbers and the same CV parsed twice came back with a different structure.
print("\nTemperature:")


class _RecordingClient:
    def __init__(self):
        self.seen = {}

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, *, model, messages, max_tokens, temperature=None):
        self.seen = {"model": model, "max_tokens": max_tokens, "temperature": temperature}
        _msg = type("_M", (), {"content": "{}"})
        _choice = type("_C", (), {"message": _msg(), "finish_reason": "stop"})
        return type("_R", (), {"choices": [_choice()]})()


def _fire(call_type):
    _agent = LLMAgent(data_dir=_REPO_ROOT)
    _rc = _RecordingClient()
    _agent._client_cache, _agent._client_cache_key = _rc, _agent.api_key
    with redirect_stdout(io.StringIO()):
        _agent._chat_completion(model="m", messages=[{"role": "user", "content": "hi"}],
                                max_tokens=10, call_type=call_type)
    return _rc.seen["temperature"]


check("scoring is decided at the likeliest answer, every time",
      _fire("score") == _STRUCTURED_TEMPERATURE == 0.0)
check("a CV parse likewise — a re-parse must not re-roll the structure",
      _fire("resume_parse") == _STRUCTURED_TEMPERATURE)
check("a cover letter is allowed to vary", _fire("cover") == _PROSE_TEMPERATURE)
check("and the two values differ, or the table decides nothing",
      _STRUCTURED_TEMPERATURE != _PROSE_TEMPERATURE)
check("the temperature used is recorded with the call",
      LAST_CALL.get("temperature") == _PROSE_TEMPERATURE)

_out = io.StringIO()
with redirect_stdout(_out):
    _undeclared = _temperature_for("a_call_type_nobody_declared")
check("an undeclared call type is said out loud, never silently defaulted",
      _undeclared == _STRUCTURED_TEMPERATURE and "no temperature declared" in _out.getvalue())

# Closed vocabulary: a call site inventing its own call_type would land in the
# warning branch above at runtime and nowhere else. Catch it here instead.
_declared = set(_CALL_TEMPERATURE)
_used = set()
for _py in _REPO_ROOT.rglob("*.py"):
    if any(p == "venv" or p.startswith(".") for p in _py.parts) or _py.parts[-2] == "tests":
        continue
    _used |= set(re.findall(r'call_type=["\']([a-z_]+)["\']', _py.read_text(encoding="utf-8")))
check(f"every call_type in use has a declared temperature (undeclared: "
      f"{sorted(_used - _declared) or 'none'})", not _used - _declared)


print()
_total, _passed = len(results), sum(results)
print(f"{_passed}/{_total} passed")
sys.exit(0 if _passed == _total else 1)
