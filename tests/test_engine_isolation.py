"""
Regression guard: core/llm_agent.py and api.py's surface stays bounded to
exactly what a single-provider, direct-call LLM gateway and REST wrapper
need - nothing more. Structural allowlist checks (what's ALLOWED to exist)
rather than a denylist (naming what must be absent) - a denylist would need
to spell out the exact shape of whatever it's guarding against, which is
itself information this repo shouldn't carry. If either file ever gains an
attribute, import, or request field outside its documented surface, these
checks fail and print the offending name(s) - the name only ever appears in
a failure message at run time, never in this file's own source.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_REPO_ROOT = Path(__file__).parent.parent

results = []


def check(label, condition):
    status = "✅" if condition else "❌"
    print(f"  {status} {label}")
    results.append(bool(condition))


def _imported_top_level_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _top_level_defined_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


_STDLIB = {
    "base64", "dataclasses", "json", "os", "re", "sys", "shutil", "tempfile",
    "threading", "time", "uuid", "datetime", "pathlib", "typing", "ast", "unittest",
}
_THIRD_PARTY = {"fastapi", "pydantic", "dotenv", "playwright", "openai", "uvicorn", "httpx", "json_repair"}
_REPO_LOCAL = {p.stem for p in _REPO_ROOT.glob("*.py")} | {"adapters", "onboarding", "utils", "core"}
_ALLOWED_MODULES = _STDLIB | _THIRD_PARTY | _REPO_LOCAL

# ── Structural: core/llm_agent.py imports only from known-legitimate sources ─
_llm_agent_src = (_REPO_ROOT / "core" / "llm_agent.py").read_text(encoding="utf-8")
_unresolved = _imported_top_level_modules(_llm_agent_src) - _ALLOWED_MODULES
check(f"core/llm_agent.py imports only stdlib/third-party/this-repo modules "
      f"(unresolved: {sorted(_unresolved) or 'none'})", not _unresolved)

# ── Structural: core/llm_agent.py defines nothing beyond its documented set ──
_expected_llm_agent_module_names = {
    "_PROMPTS_DIR", "_MAX_VACANCY_CHARS", "_SCORE_PLACEHOLDER_TEXT",
    "_PLACEHOLDER_TOKEN_RE", "_CALL_TYPE_NARRATION", "_CALL_SEQ",
    "_SESSION_REPORTER", "set_session_reporter", "LLMAgent",
    # Per-call observability (2026-08-16): records what a call actually did —
    # whether the reply was cut off at max_tokens, whether json_repair had to
    # rescue it. Local facts about a local call; no transport, tier or billing
    # concept enters through them, which is what this guard is for.
    "_TRUNCATED", "LAST_CALL", "_note_call", "_note_json_repair",
    # Sampling temperature stated per call type (2026-08-16) — a local property
    # of a local call, same as max_tokens beside it.
    "_STRUCTURED_TEMPERATURE", "_PROSE_TEMPERATURE", "_CALL_TEMPERATURE",
    "_temperature_for",
    # GatewayClient: the raw-client interface ResumeParser expects, routed
    # through _chat_completion so the CV parse stops being the one call with a
    # private route. Deliberately transport-agnostic — it knows only that an
    # agent has a gateway.
    "GatewayClient",
}
_extra_module_names = _top_level_defined_names(_llm_agent_src) - _expected_llm_agent_module_names
check(f"core/llm_agent.py defines no module-level names beyond its documented set "
      f"(unexpected: {sorted(_extra_module_names) or 'none'})", not _extra_module_names)

_expected_llm_agent_class_members = {
    "model", "cover_model", "api_key", "client",
    "generate_cover", "score_vacancy", "fill_form", "ask_modal_action", "answer_question",
    "_chat_completion", "_system", "_build_system_prompt", "_build_match_hint",
    "_load_profile", "_load_prompt", "_sanitize_score_result", "_is_template_echo", "_parse_json",
}
from core import llm_agent  # noqa: E402

_actual_class_members = {
    name for name in dir(llm_agent.LLMAgent)
    if not (name.startswith("__") and name.endswith("__"))
}
_extra_class_members = _actual_class_members - _expected_llm_agent_class_members
check(f"LLMAgent has no members beyond its documented surface "
      f"(unexpected: {sorted(_extra_class_members) or 'none'})", not _extra_class_members)

# ── Structural: api.py imports only from known-legitimate sources ───────────
_api_src = (_REPO_ROOT / "api.py").read_text(encoding="utf-8")
_unresolved_api = _imported_top_level_modules(_api_src) - _ALLOWED_MODULES
check(f"api.py imports only stdlib/third-party/this-repo modules "
      f"(unresolved: {sorted(_unresolved_api) or 'none'})", not _unresolved_api)

# ── Functional: LLMAgent still makes a single direct attempt, unchanged ─────
from unittest.mock import patch

with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
    with patch("core.llm_agent.OpenAI"):
        import openai
        import httpx as _httpx

        agent = llm_agent.LLMAgent(data_dir=Path("/tmp"))
        mock_client = agent.client  # patched OpenAI() → MagicMock, cached
        conn_error = openai.APIConnectionError(request=_httpx.Request("POST", "https://x.test"))
        mock_client.chat.completions.create.side_effect = conn_error

        try:
            agent._chat_completion(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=10)
            check("a connection failure propagates — no fallback exists to catch it", False)
        except openai.APIConnectionError:
            check("a connection failure propagates — no fallback exists to catch it", True)

        mock_client.chat.completions.create.side_effect = None
        mock_client.chat.completions.create.reset_mock()
        mock_resp = mock_client.chat.completions.create.return_value
        mock_resp.choices[0].message.content = "direct-ok"
        result = agent._chat_completion(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=10)
        check("a healthy call returns the direct response unchanged", result == "direct-ok")
        check("exactly one attempt made (no retry loop of any kind)",
              mock_client.chat.completions.create.call_count == 1)

# ── Runtime: api.py's own request models carry only their documented fields ─
with patch.dict("os.environ", {"LLM_API_KEY": "test", "API_KEY": "test-api-key"}):
    import api as _api_module

    _expected_fields = {
        "SessionStartRequest": {"profile", "max_vacancies", "dry_run", "debug", "target_url"},
        "ResumeParseRequest": {"filename", "content_b64"},
        "MinMatchPatchRequest": {"min_match"},
        # overwrite (2026-08-12): opt-in acknowledgement that a save erases an
        # existing profile's content. Without it, a save carrying no cases,
        # skills, tools or languages is refused when the profile on disk has
        # them — see onboarding/profile_guard.py for the wipe that motivated it.
        "CandidateSaveRequest": {"profile", "candidate", "overwrite"},
        # The hand-edit path for candidate.md (2026-08-14). Same overwrite rule as
        # above and for the same reason: a cleared textarea is the hand-edit shape
        # of a blank wizard form.
        "CandidateMdSaveRequest": {"candidate_md", "overwrite"},
    }
    for model_name, expected in _expected_fields.items():
        model_cls = getattr(_api_module, model_name, None)
        if model_cls is None:
            check(f"api.py defines {model_name}", False)
            continue
        # One-directional on purpose: a field appearing without being written down here
        # is the thing worth catching. A field disappearing is caught by whatever used it.
        extra = set(model_cls.model_fields) - expected
        check(f"{model_name} has no fields beyond its documented set "
              f"(unexpected: {sorted(extra) or 'none'})", not extra)

    # Removed 2026-08-14: a process-global setter, in a process serving several profiles,
    # with no caller in any frontend bundle. min_score had already been taken out of it
    # after a per-resume threshold turned out to be settable process-wide. GET stays.
    check("PATCH /api/v1/config is gone, not merely unused",
          not any(getattr(r, "path", None) == "/api/v1/config" and "PATCH" in getattr(r, "methods", set())
                  for r in _api_module.app.routes))
    check("GET /api/v1/config is still there — reading the resolved config mutates nothing",
          any(getattr(r, "path", None) == "/api/v1/config" and "GET" in getattr(r, "methods", set())
              for r in _api_module.app.routes))

print()
total = len(results)
passed = sum(results)
print(f"{passed}/{total} passed")
if passed != total:
    sys.exit(1)
