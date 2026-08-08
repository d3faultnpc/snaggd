"""
LLM agent — single client for all AI calls (cover letter, scoring, HR questions).
Uses OpenRouter as gateway; model comes from the LLM_MODEL/COVER_MODEL env vars,
API key from LLM_API_KEY. Every call routes through _chat_completion() — a
single, direct attempt against OpenRouter; a connection-level failure just
raises.
"""

import json
import os
import re
from pathlib import Path

import httpx
from openai import OpenAI

from config import CONFIG

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_MAX_VACANCY_CHARS = CONFIG.llm_max_input_chars

# match_scoring.md's own JSON-example placeholder text. Seen live 2026-07-12: on an
# ambiguous/long vacancy description, the model returned this verbatim instead of real
# analysis. Used by _sanitize_score_result() to detect and contain template-echo responses.
_SCORE_PLACEHOLDER_TEXT = {
    "matched_skills": "skill present in both profile and vacancy",
    "gaps": "requirement in vacancy missing from profile",
    "signals": "3–5 short tags characterizing this vacancy's domain, context, and product type",
    "vacancy_role_type": "contribution style of this vacancy (use the same vocabulary as the candidate's role_type when possible)",
}

# match_scoring.md's CURRENT JSON example uses <REAL_SKILL_1>-style bracket tokens instead of
# the sentence placeholders above. Shape-based, not tied to specific wording — catches an
# unfilled placeholder regardless of how the prompt's example text is phrased. Unanchored
# (search, not match on the whole string): the current example has 2 tokens per list field
# (<REAL_SKILL_1>, <REAL_SKILL_2>), so a partial fill could leave one token embedded inside
# an otherwise-real string rather than being the entire field value.
_PLACEHOLDER_TOKEN_RE = re.compile(r"<[A-Z0-9_]+>")

try:
    from json_repair import repair_json as _repair_json
    _HAS_JSON_REPAIR = True
except ImportError:
    _HAS_JSON_REPAIR = False

# GUI client narration per call type (session 58) — humanized,
# actor="llm". "score" is deliberately absent: adapter.py already narrates
# scoring with richer context (vacancy_id/company/position); a second,
# unlabeled line here would just duplicate it in the GUI. CLI console output
# (the diagnostic "🧮 LLM call #N..." print in _chat_completion) is
# unaffected either way — this dict only shapes what the reporter receives.
_CALL_TYPE_NARRATION = {
    "cover": "Writing your cover letter…",
    "fill_form": "Reading an unfamiliar question, working out the answer…",
    "modal_action": "Reading an unfamiliar pop-up…",
    "answer_question": "Answering the employer's question…",
}

# Diagnostic call counter (session 55) — see _chat_completion(). Process-wide,
# not per-vacancy: good enough to eyeball "how many calls just fired" in the
# console/reporter stream during a live single-vacancy or small run; not
# meant as a permanent metric.
_CALL_SEQ = 0
# Session reporter mirror for the counter above (session 56) — the "reporter
# stream" the comment above assumed already existed never actually did;
# _chat_completion() only ever printed, invisible once the app is launched
# from a bundled .app with no attached console. LLMAgent is an import-time
# singleton in handlers/chat.py and handlers/test_form.py, so only a global
# read at call time (not a constructor arg) can reach it. None (CLI/guest) =
# print-only, unchanged.
_SESSION_REPORTER = None


def set_session_reporter(reporter) -> None:
    """Sets (or with None clears) the process-wide EventReporter that mirrors
    _chat_completion()'s diagnostic call counter into an attached GUI client.
    Called once per API session by api.py's worker; CLI/guest runs never
    call this, so their behavior stays print-only, unchanged."""
    global _SESSION_REPORTER
    _SESSION_REPORTER = reporter


class LLMAgent:
    def __init__(self, data_dir: Path = None):
        env_api_key = os.getenv("LLM_API_KEY")
        if not env_api_key:
            raise RuntimeError("LLM_API_KEY not set — add it to .env")

        self._data_dir = data_dir or CONFIG.data_dir
        self._env_api_key = env_api_key
        self._env_model = os.getenv("LLM_MODEL", "deepseek/deepseek-v3.2")
        self._env_cover_model = os.getenv("COVER_MODEL", self._env_model)
        self._system_prompt: str | None = None
        # Client is built lazily (see `client` property below) and cached
        # against the key it was built for.
        self._client_cache: OpenAI | None = None
        self._client_cache_key: str | None = None

    # Properties rather than attributes frozen in __init__ purely so this
    # matches the reporter global's own call-time-read pattern above —
    # model/cover_model/api_key are fixed for this instance's lifetime today,
    # but LLMAgent is still an import-time singleton in handlers/chat.py and
    # handlers/test_form.py, so reading at call time costs nothing and keeps
    # the shape consistent. llm_cover.py's cache keys read these per call, so
    # cached scores stay partitioned by actual model.
    @property
    def model(self) -> str:
        return self._env_model

    @property
    def cover_model(self) -> str:
        return self._env_cover_model

    @property
    def api_key(self) -> str:
        return self._env_api_key

    @property
    def client(self) -> OpenAI:
        """Lazy OpenAI client, built once and cached. Rebuilding only when
        the effective key actually changes (not on every call) avoids
        opening a fresh httpx.Client per LLM call."""
        key = self.api_key
        if self._client_cache is None or self._client_cache_key != key:
            # PROXY_URL (.env.example, documented since before session 55, never
            # actually read by any code until now) — optional local proxy for RU
            # users who can't reach openrouter.ai directly. httpx[socks] added as
            # a dependency alongside this so the documented socks5:// example
            # value actually works, not just http(s):// ones.
            proxy_url = os.getenv("PROXY_URL")
            http_client = httpx.Client(proxy=proxy_url) if proxy_url else None
            self._client_cache = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=key,
                http_client=http_client,
                # SDK default is read=600s (10 min) — found live (session 55):
                # a VPN dropping mid-request leaves an already-established TCP
                # connection with no path, and the client just sits inside
                # read() waiting up to 10 minutes for bytes that will never
                # arrive. Nothing about restoring the VPN un-sticks that
                # specific hung read — the process is genuinely blocked, Stop
                # can't reach it, and no exception has fired yet for anything
                # further up the call stack to react to. 30s read is generous
                # for our largest call (800 max_tokens) on a healthy
                # connection, short enough that a dead one fails fast instead
                # of hanging past any reasonable human patience.
                timeout=httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=10.0),
                # SDK default is max_retries=2 (up to 3 attempts) — found live
                # in testing this same fix: against a hung connection that
                # compounded the 30s read timeout to ~70s before finally
                # raising, because each retry re-hit the identical dead path.
                # 0 lets a connection failure surface immediately instead of
                # the SDK silently re-trying a path that isn't coming back on
                # its own within a few seconds — this module makes one direct
                # attempt and raises on failure; it has no retry/fallback
                # strategy of its own.
                max_retries=0,
            )
            self._client_cache_key = key
        return self._client_cache

    # ── Single gateway for every LLM call ────────────────────────────────────

    def _chat_completion(self, *, model: str, messages: list, max_tokens: int,
                          call_type: str = None) -> str:
        """Routes every LLM call for this process — one direct attempt
        against OpenRouter; a connection-level failure just raises.

        call_type: which public method is calling (see _CALL_TYPE_NARRATION)
        — used only to pick the GUI client narration line when a reporter is
        attached. vacancy_id is deliberately left unset on that event: this
        pipeline processes one vacancy at a time, and adapter.py's own events
        already established which one is currently active — the GUI attaches
        a vacancy_id-less llm event to whichever vacancy that is instead of
        threading vacancy identity through every LLM call site.
        """
        global _CALL_SEQ
        _CALL_SEQ += 1
        # Diagnostic instrumentation (session 55) — every LLM call funnels
        # through this one method, so this is the single point that can
        # answer "how many calls actually fired and what was each one" —
        # needed after a live incident (2 different cover-letter texts sent
        # to one chatik, 3 calls visible on OpenRouter's own dashboard) that
        # the code's own applied_log bookkeeping couldn't explain (logged
        # exactly one clean successful send, no error, no HR-bot activity).
        # Prints the last message's own opening text — since each call site
        # (score/cover/modal-action/HR-answer) uses a distinctly-worded
        # prompt, this alone identifies which call this is without needing
        # a separate call-site parameter threaded through five methods.
        _last_content = messages[-1].get("content", "") if messages else ""
        if isinstance(_last_content, list):
            # Multimodal content (image_url blocks — PDF/image resume parsing)
            # has no single string to preview; pull out the text block if one
            # exists rather than crashing on list.replace().
            _text_blocks = [b.get("text", "") for b in _last_content if isinstance(b, dict) and b.get("type") == "text"]
            _last_content = " ".join(_text_blocks) or "[multimodal content]"
        _preview = str(_last_content)[:70].replace("\n", " ")
        _call_msg = f"🧮 LLM call #{_CALL_SEQ} (max_tokens={max_tokens}): {_preview}..."
        _gui_msg = _CALL_TYPE_NARRATION.get(call_type)

        print(f"   {_call_msg}")
        if _SESSION_REPORTER is not None and _gui_msg is not None:
            _SESSION_REPORTER.emit(_gui_msg, actor="llm")
        resp = self.client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_cover(self, vacancy_text: str, match_context: dict | None = None) -> str:
        """Generate cover letter.

        match_context: optional dict from score_vacancy() — matched_skills, gaps, signals,
        vacancy_role_type. Injected as a compact SCORING CONTEXT block so the model writes
        precisely to what actually overlaps instead of re-analysing the vacancy cold.
        """
        prompt = self._load_prompt("cover_letter.md")
        hint = self._build_match_hint(match_context) if match_context else ""
        content = self._chat_completion(
            model=self.cover_model,
            max_tokens=800,
            call_type="cover",
            messages=[
                {"role": "system", "content": self._system()},
                {"role": "user", "content": f"{prompt}{hint}\n\nVACANCY:\n{vacancy_text[:_MAX_VACANCY_CHARS]}"},
            ],
        )
        stripped = content.strip()
        # Follow-up to _chat_completion's own generic "Writing your cover
        # letter…" line — that one fires before the call even happens (it
        # doesn't have the text yet); this one fires here, once real text
        # exists, so the reporter shows the actual letter, not just the
        # in-progress phrase, for as long as it stays in the rolling buffer.
        if _SESSION_REPORTER is not None and stripped:
            _preview = stripped[:100].replace("\n", " ")
            _ellipsis = "…" if len(stripped) > 100 else ""
            _SESSION_REPORTER.emit(f'Cover letter drafted: "{_preview}{_ellipsis}"', actor="llm")
        return stripped

    def score_vacancy(self, vacancy_text: str) -> dict:
        """Returns {score, matched_skills, gaps, signals, stop_match}.

        stop_match: str category name if LLM detected a blocked category, else None.
        The list of blocked categories comes from stop_categories in the system prompt
        (loaded from job_preferences.md) — no extra parameters needed.
        """
        prompt = self._load_prompt("match_scoring.md")
        content = self._chat_completion(
            model=self.model,
            max_tokens=400,
            call_type="score",
            messages=[
                {"role": "system", "content": self._system()},
                {"role": "user", "content": f"{prompt}\n\nVACANCY:\n{vacancy_text[:_MAX_VACANCY_CHARS]}"},
            ],
        )
        raw = (content or "{}").strip()
        result = self._parse_json(raw, fallback={
            "score": 50,
            "matched_skills": [],
            "gaps": [],
            "signals": [],
            "stop_match": None,
        })
        # Sanitize score: some models embed emoji or extraneous text alongside the
        # integer (e.g. DeepSeek occasionally returns "紙 67"). Extract the first
        # integer found; fall back to 50 if none present.
        raw_score = result.get("score")
        if raw_score is not None and not isinstance(raw_score, int):
            m = re.search(r"\d+", str(raw_score))
            result["score"] = int(m.group()) if m else 50
        return self._sanitize_score_result(result)

    def fill_form(self, vacancy_text: str, fields: list[dict]) -> dict[str, str]:
        """
        Fill all form fields in one call.
        fields: [{"idx": 0, "label": "...", "type": "text"}, ...]
        Returns: {"0": "answer", "1": "answer", ...}
        """
        prompt_template = self._load_prompt("form_fill.md")
        import json as _json
        prompt = (
            prompt_template
            .replace("{{FIELDS}}", _json.dumps(fields, ensure_ascii=False))
            .replace("{{VACANCY}}", vacancy_text[:_MAX_VACANCY_CHARS])
        )
        content = self._chat_completion(
            model=self.model,
            max_tokens=800,
            call_type="fill_form",
            messages=[
                {"role": "system", "content": self._system()},
                {"role": "user", "content": prompt},
            ],
        )
        raw = (content or "{}").strip()
        return self._parse_json(raw, fallback={})

    def ask_modal_action(self, modal_text: str, buttons: list[dict]) -> dict:
        """Decide which button to click for a blocking modal.

        Returns {"action": "click", "button_index": N} or {"action": "skip"}.
        Lightweight — no candidate context, ~50 output tokens.
        """
        prompt = (
            "A modal dialog is blocking a job application page. "
            "Choose which button to click to continue the application.\n"
            "Prefer buttons like 'продолжить', 'ок', 'подтвердить', 'да', 'continue', 'yes'. "
            'Return {"action": "skip"} only if no button allows continuing the application.\n\n'
            f"Modal text:\n{modal_text[:500]}\n\n"
            f"Buttons: {json.dumps(buttons, ensure_ascii=False)}\n\n"
            'Reply with JSON only: {"action": "click", "button_index": N} or {"action": "skip"}'
        )
        try:
            content = self._chat_completion(
                model=self.model,
                max_tokens=50,
                call_type="modal_action",
                messages=[{"role": "user", "content": prompt}],
            )
            raw = (content or "{}").strip()
            result = self._parse_json(raw, fallback={"action": "skip"})
            if result.get("action") == "click" and isinstance(result.get("button_index"), int):
                return result
            return {"action": "skip"}
        except Exception:
            return {"action": "skip"}

    def answer_question(self, question: str) -> str:
        content = self._chat_completion(
            model=self.model,
            max_tokens=200,
            call_type="answer_question",
            messages=[
                {"role": "system", "content": self._system()},
                {"role": "user", "content": (
                    "Answer this HR screening question briefly (2–4 sentences, in Russian). "
                    "Use only facts from the candidate profile above. Do not invent.\n\n"
                    f"Question: {question}"
                )},
            ],
        )
        return content.strip()

    # ── System prompt (built once, cached) ───────────────────────────────────

    def _system(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = self._build_system_prompt()
        return self._system_prompt

    def _build_system_prompt(self) -> str:
        parts = [
            "You are a job application assistant. "
            "Help craft personalized applications based on the candidate profile below.\n"
        ]
        for filename, label in [
            ("candidate.md",       "CANDIDATE PROFILE"),
            ("job_preferences.md", "JOB PREFERENCES"),
            ("tone_of_voice.md",   "TONE & STYLE"),
        ]:
            content = self._load_profile(filename)
            if content:
                parts.append(f"## {label}\n{content}")
        return "\n\n".join(parts)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_match_hint(self, match_context: dict) -> str:
        """Compact context injected between cover_letter prompt and VACANCY block.

        Passes score, role type, vacancy signals (direction vector), and matched skills
        (ATS intersection — framed as "weave naturally" to prevent enumeration).
        """
        parts = []
        score = match_context.get("score")
        if score is not None:
            parts.append(f"Match score: {score}/100")
        role_type = match_context.get("vacancy_role_type")
        if role_type and role_type not in ("unknown", None):
            parts.append(f"Vacancy role type: {role_type}")
        signals = match_context.get("signals") or []
        if signals:
            parts.append(f"What makes this vacancy distinctive: {', '.join(signals)}")
        skills = match_context.get("matched_skills") or []
        if skills:
            parts.append(
                f"Skills overlap — weave these naturally, do NOT enumerate or list:\n"
                f"  {', '.join(skills)}"
            )
        if not parts:
            return ""
        body = "\n".join(f"- {p}" for p in parts)
        return (
            "\n\nSCORING CONTEXT (do not copy these labels into the letter):\n"
            + body + "\n"
        )

    def _load_profile(self, filename: str) -> str:
        path = self._data_dir / filename
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def _load_prompt(self, filename: str) -> str:
        path = _PROMPTS_DIR / filename
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def _sanitize_score_result(self, result: dict) -> dict:
        """Type-guards scoring output — protects log and downstream code from LLM garbage.

        signals/matched_skills/gaps must be list[str]; stop_match must be str or None.
        score is clamped to [0, 100] — LLM modifier arithmetic can exceed the stated range.
        Template-echo (model returns match_scoring.md's own placeholder text instead of real
        analysis) makes the whole response untrusted, not just the affected field — a model
        confused enough to echo one field's schema isn't a reliable source for the rest either.
        Passes through unchanged when LLM output is well-formed.
        """
        if self._is_template_echo(result):
            return {"score": 50, "matched_skills": [], "gaps": [], "signals": [],
                    "stop_match": None, "vacancy_role_type": None}

        score = result.get("score", 50)
        if not isinstance(score, int):
            try:
                score = int(score)
            except (TypeError, ValueError):
                score = 50
        result["score"] = max(0, min(100, score))
        for key in ("signals", "matched_skills", "gaps"):
            val = result.get(key, [])
            if not isinstance(val, list):
                result[key] = []
            else:
                result[key] = [str(x) for x in val if isinstance(x, (str, int, float)) and str(x).strip()]
        sm = result.get("stop_match")
        if sm is not None and not isinstance(sm, str):
            result["stop_match"] = None
        return result

    def _is_template_echo(self, result: dict) -> bool:
        """True if any field is match_scoring.md's own JSON-example placeholder — either the
        old-style literal sentence or an unfilled <TOKEN> from the current bracket-style
        example — instead of real content. The <TOKEN> check is shape-based (not tied to
        today's exact wording) so it stays valid if the prompt's placeholder text changes
        again later without this guard being updated in lockstep.
        """
        def _is_placeholder(val) -> bool:
            return isinstance(val, str) and bool(_PLACEHOLDER_TOKEN_RE.search(val))

        for key, placeholder in _SCORE_PLACEHOLDER_TEXT.items():
            val = result.get(key)
            if val == placeholder or _is_placeholder(val):
                return True
            if isinstance(val, list) and (placeholder in val or any(_is_placeholder(v) for v in val)):
                return True
        return False

    def _parse_json(self, raw: str, fallback: dict) -> dict:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            if _HAS_JSON_REPAIR:
                try:
                    return json.loads(_repair_json(raw))
                except Exception:
                    pass
        return fallback
