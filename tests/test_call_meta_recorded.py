"""
What a call cost, and how to find it again.

Every scoring call already produced a provider generation id and a pair of token
counts, and every one of them was dropped with the response object. The
consequence was concrete: no past call could be looked up for its real tokens or
cost, so a question as ordinary as "did splitting the prompt make it cheaper"
had no local answer and no remote one either — the provider's API returns
metadata only by id, and there were no ids.

The second thing here is subtler and is why a cache hit gets its own check. The
records travel on a module-level global, and a value left over from the previous
vacancy is worse than a missing one: it attributes a real call to a vacancy that
never made one. A cache hit has to say null and mean it.

Run:  venv/bin/python3 tests/test_call_meta_recorded.py
"""
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("LLM_API_KEY", "test-key")

from core.llm_agent import (  # noqa: E402
    LAST_CALL, LLMAgent, _note_call, call_meta_of, last_call_snapshot,
)

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


def _resp(*, rid="gen-1", prompt=11, completion=22, usage=True):
    """Shaped like the OpenAI-compatible response the gateway actually receives."""
    choice = type("_C", (), {"finish_reason": "stop",
                             "message": type("_M", (), {"content": "{}"})()})()
    fields = {"id": rid, "choices": [choice]}
    if usage:
        fields["usage"] = type("_U", (), {"prompt_tokens": prompt,
                                          "completion_tokens": completion})()
    return type("_R", (), fields)()


# ── Pulling the meta off a response ──────────────────────────────────────────
print("\nReading a response for what it cost:")

m = call_meta_of(_resp())
check("the generation id is read", m["generation_id"] == "gen-1")
check("both token counts are read", (m["tokens_prompt"], m["tokens_completion"]) == (11, 22))

m = call_meta_of(_resp(usage=False))
check("a provider that omits usage costs an observation, not a run",
      m == {"generation_id": "gen-1", "tokens_prompt": None, "tokens_completion": None})

check("no response at all is three Nones, not an exception",
      call_meta_of(None)["generation_id"] is None)


# ── The record, and the fact that it is a copy ───────────────────────────────
print("\nThe snapshot is a copy taken in the caller's own frame:")

LAST_CALL.clear()
_note_call(model="m", max_tokens=10, finish_reason="stop",
           **call_meta_of(_resp(rid="gen-A", prompt=5, completion=6)))
snap = last_call_snapshot()
check("the snapshot carries the id", snap["generation_id"] == "gen-A")
check("and the token counts", (snap["tokens_prompt"], snap["tokens_completion"]) == (5, 6))

_note_call(model="m", max_tokens=10, finish_reason="stop",
           **call_meta_of(_resp(rid="gen-B", prompt=7, completion=8)))
check("a later call does not reach back into a snapshot already taken",
      snap["generation_id"] == "gen-A")
check("while the global itself has moved on", LAST_CALL["generation_id"] == "gen-B")


# ── Old callers keep working ─────────────────────────────────────────────────
print("\nEvery existing caller keeps working:")

LAST_CALL.clear()
_note_call(model="m", max_tokens=10, finish_reason="stop", call_type="score")
check("the new fields are optional — a call that names none still records",
      LAST_CALL["model"] == "m" and LAST_CALL["finish_reason"] == "stop")
check("and they read as None, which is what 'the caller did not say' means",
      LAST_CALL["generation_id"] is None and LAST_CALL["tokens_prompt"] is None)


# ── score_vacancy hands the meta on beside the answer ────────────────────────
print("\nA scored vacancy carries what the call did:")


class _Client:
    def __init__(self, resp):
        self._resp = resp
        self.chat = type("_Chat", (), {"completions": self})()

    def create(self, **_kw):
        return self._resp


def _agent_returning(payload, rid):
    agent = LLMAgent(data_dir=Path("/tmp"))
    choice = type("_C", (), {"finish_reason": "stop",
                             "message": type("_M", (), {"content": payload})()})()
    resp = type("_R", (), {"id": rid, "choices": [choice],
                           "usage": type("_U", (), {"prompt_tokens": 100,
                                                    "completion_tokens": 30})()})()
    agent._client_cache = _Client(resp)
    agent._client_cache_key = agent.api_key
    return agent


scored = _agent_returning(
    '{"score": 71, "matched_skills": ["a"], "gaps": [], "signals": ["s"],'
    ' "stop_match": null, "role_type_match": true}', "gen-score"
).score_vacancy("a vacancy")

check("the meta rides beside the answer", scored["call_meta"]["generation_id"] == "gen-score")
check("with the tokens the call actually used",
      scored["call_meta"]["tokens_prompt"] == 100)
check("and the answer itself is untouched by carrying it", scored["score"] == 71)
check("role_type_match survives to the caller — it used to be computed and dropped",
      scored.get("role_type_match") is True)


# ── A cache hit has no call behind it, and must say so ───────────────────────
print("\nA cached score attributes nothing to itself:")

import tempfile  # noqa: E402

from llm_cover import LLMCover  # noqa: E402

_tmp = Path(tempfile.mkdtemp())
(_tmp / "candidate.md").write_text("# candidate\n", encoding="utf-8")

cover = LLMCover(data_dir=_tmp)
cover._agent = _agent_returning(
    '{"score": 64, "matched_skills": ["a"], "gaps": [], "signals": ["s"], "stop_match": null}',
    "gen-cached",
)

check("a live score records the call that produced it",
      cover.score("a vacancy body") and cover.last_call_meta
      and cover.last_call_meta["generation_id"] == "gen-cached")

# Same text, so the second score() is served from cache. The agent is swapped for
# one that would raise if touched: proving the cache answered, not the model.
class _Exploding:
    # Carries the model names because the cache key is built from them — the
    # stub has to hash identically to the agent it replaces, or the "cache hit"
    # this test claims to exercise would be a second live call.
    def __init__(self, like):
        self.model = like.model
        self.cover_model = like.cover_model

    def score_vacancy(self, _text):
        raise AssertionError("the cache should have answered this")


cover._agent = _Exploding(cover._agent)
served = cover.score("a vacancy body")
check("the second look is served from cache", served and cover.last_score == 64)
check("and it carries no call meta — there was no call",
      cover.last_call_meta is None)
check("nor the previous vacancy's role-type verdict",
      cover.last_role_type_match is None)


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
