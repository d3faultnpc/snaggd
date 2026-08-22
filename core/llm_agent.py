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

# match_scoring.md's JSON example uses <GRADE>-style bracket tokens; an answer still
# carrying one is the model echoing the shape instead of filling it (seen live
# 2026-07-12 on a long, ambiguous posting). Shape-based rather than a table of the
# prompt's exact sentences: the table that used to sit here listed placeholder text
# from a version of the prompt that no longer exists, and three of its four keys named
# fields that no longer exist either. Unanchored (search, not fullmatch) because a
# partial fill can leave one token embedded in an otherwise-real string.
_PLACEHOLDER_TOKEN_RE = re.compile(r"<[A-Z0-9_]+>")

# Stamped on every scored record. The evidence layer — 913 entries on one live
# profile, and the dashboard, History and CSV export built on them — spans both the
# free-text era and the graded one. An aggregate that cannot tell which record is
# which would average a verbatim selection together with the paraphrases that
# preceded it and report a number describing neither.
_SCORING_FORMAT = "axes-v1"

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
    "requirements": "Reading what this vacancy actually asks for…",
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


# ── Sampling temperature, stated per call type ───────────────────────────────
# Never set before 2026-08-16: every call ran at whatever the provider defaults
# to, which for most models is 1.0 — the most creative setting there is, applied
# to scoring and to CV parsing alike.
#
# What that cost: the same vacancy scored twice returned different numbers, so a
# MIN_SCORE gate was a coin flip for anything near the threshold; the same CV
# parsed twice produced a different structure, which makes any acceptance metric
# over a corpus unmeasurable — you cannot tell a prompt change from a re-roll.
#
# Two values, because there are two kinds of call. Structured calls answer with
# JSON or a decision and want the likeliest answer every time. Prose calls are
# written for a human, and there the spread is the point — which is also why
# these two constants are the natural home for a future "strict → creative"
# control: it moves _PROSE_TEMPERATURE and the tone instruction together, and by
# construction cannot reach the structured calls.
_STRUCTURED_TEMPERATURE = 0.0
_PROSE_TEMPERATURE = 0.7
_CALL_TEMPERATURE = {
    "score": _STRUCTURED_TEMPERATURE,
    # Stage one of split scoring: reading a posting for what it asks. Structured
    # for the same reason as the rest of them — the answer is a list of facts
    # about a document, and there is nothing to be creative about.
    "requirements": _STRUCTURED_TEMPERATURE,
    "fill_form": _STRUCTURED_TEMPERATURE,
    "modal_action": _STRUCTURED_TEMPERATURE,
    "resume_parse": _STRUCTURED_TEMPERATURE,
    "cover": _PROSE_TEMPERATURE,
    "answer_question": _PROSE_TEMPERATURE,
}


def _temperature_for(call_type: str | None) -> float:
    """The temperature for this call type. A call type nobody declared is a gap
    in this table, not a licence to fall back to the provider's default — that
    default is exactly the invisible state this table exists to end. It is
    treated as structured and said out loud."""
    if call_type in _CALL_TEMPERATURE:
        return _CALL_TEMPERATURE[call_type]
    print(f"   ⚠️  no temperature declared for call type {call_type!r} — "
          f"using {_STRUCTURED_TEMPERATURE}; add it to _CALL_TEMPERATURE")
    return _STRUCTURED_TEMPERATURE


# ── Per-call observability ───────────────────────────────────────────────────
# Everything that explains a bad answer is knowable here and used to be dropped
# with the response object. Two facts in particular:
#
#   finish_reason == "length" — the reply was cut off at max_tokens. What comes
#   back is not malformed, it is SHORT: the tail is simply missing, and every
#   later frame sees a plausible answer.
#
#   a json_repair rescue — the reply was not valid JSON on its own. Repair then
#   makes a truncated stump into a well-formed object, so the two failures
#   compound into something indistinguishable from success.
#
# Recorded, not enforced: nothing here changes what a call returns.
_TRUNCATED = "length"
LAST_CALL: dict = {}


def call_meta_of(resp) -> dict:
    """The provider's own identifiers and token counts, pulled off a response.

    Defensive on every field: this runs against whatever the gateway handed back,
    and a provider that omits `usage` (or an SDK version that shapes it
    differently) must cost an observation, never a run. Everything absent is
    None, which is the honest value for "the provider did not say".

    `id` is OpenRouter's generation id. Nothing stored it before, so no past call
    can be looked up for its real token counts or cost — the reason this exists.
    """
    meta = {"generation_id": None, "tokens_prompt": None, "tokens_completion": None}
    if resp is None:
        return meta
    meta["generation_id"] = getattr(resp, "id", None)
    usage = getattr(resp, "usage", None)
    if usage is not None:
        meta["tokens_prompt"] = getattr(usage, "prompt_tokens", None)
        meta["tokens_completion"] = getattr(usage, "completion_tokens", None)
    return meta


def last_call_snapshot() -> dict:
    """A copy of LAST_CALL, taken by the caller in the frame that made the call.

    LAST_CALL is module-global and sessions run in background threads, so it is
    only truthful about "the call that just returned" — reading it later, or from
    another frame, is reading whatever ran most recently anywhere in the process.
    Callers take this snapshot immediately and carry the value; the global never
    becomes the transport. See the ambient-state lesson in the app repo's memory.
    """
    return dict(LAST_CALL)


def _note_call(*, model: str, max_tokens: int, finish_reason: str | None,
               call_type: str | None = None, temperature: float | None = None,
               generation_id: str | None = None, tokens_prompt: int | None = None,
               tokens_completion: int | None = None) -> None:
    LAST_CALL.update(model=model, max_tokens=max_tokens, finish_reason=finish_reason,
                     call_type=call_type, temperature=temperature, json_repaired=False,
                     generation_id=generation_id, tokens_prompt=tokens_prompt,
                     tokens_completion=tokens_completion)
    if finish_reason == _TRUNCATED:
        print(f"   ⚠️  reply cut off at max_tokens={max_tokens} "
              f"(model={model}, call={call_type or '?'}) — the tail is lost, not malformed")
        if _SESSION_REPORTER is not None:
            _SESSION_REPORTER.emit("The model's answer was cut short — some of it is missing.",
                                   actor="llm", level="warn")


def _note_json_repair(where: str) -> None:
    LAST_CALL["json_repaired"] = True
    print(f"   ⚠️  JSON repair fired ({where}) — the reply was not valid JSON on its own")


class LLMAgent:
    # data_dir is required, deliberately. It used to default to CONFIG.data_dir,
    # which is resolved once at import from the DATA_DIR env var — a model that
    # only holds when a process serves exactly one profile. The API serves many
    # and resolves the active one per request, so the default silently pointed
    # every caller that forgot the argument at the flat legacy directory. Two
    # handlers forgot it for months and answered employers from a stale profile.
    # A missing argument must fail here, loudly, instead of reading someone
    # else's facts. Callers that genuinely have no profile (CLI entry points)
    # pass CONFIG.data_dir explicitly, which states the intent at the call site.
    def __init__(self, data_dir: Path):
        env_api_key = os.getenv("LLM_API_KEY")
        if not env_api_key:
            raise RuntimeError("LLM_API_KEY not set — add it to .env")

        if data_dir is None:
            raise ValueError("LLMAgent requires an explicit data_dir (the active profile's directory)")
        self._data_dir = Path(data_dir)
        self._env_api_key = env_api_key
        self._env_model = os.getenv("LLM_MODEL", "deepseek/deepseek-v3.2")
        self._env_cover_model = os.getenv("COVER_MODEL", self._env_model)
        self._system_prompts: dict[str, str] = {}
        # Read lazily from the profile on first use — see _declared_stop_categories.
        self._stop_categories: set | None = None
        # Likewise — see _declared_skills. Both are the person's own declarations,
        # and both are read from the profile rather than configured anywhere else.
        self._declared_skills_cache: list | None = None
        self._profile_axes_cache: set | None = None
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
        temperature = _temperature_for(call_type)
        resp = self.client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=temperature,
        )
        _note_call(model=model, max_tokens=max_tokens, call_type=call_type,
                   temperature=temperature,
                   finish_reason=getattr(resp.choices[0], "finish_reason", None),
                   **call_meta_of(resp))
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
                {"role": "system", "content": self._system("cover")},
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

    # A requirement the posting calls mandatory counts double. "unspecified" sits
    # with "nice" rather than with "must": when a posting does not say how much
    # something matters, treating it as mandatory is our invention, not its.
    _REQUIREMENT_WEIGHTS = {"must": 2.0, "nice": 1.0, "unspecified": 1.0}
    # "silent" is deliberately absent. A verdict of silent leaves the denominator
    # entirely — see _score_from_verdicts.
    _VERDICT_CREDIT = {"met": 1.0, "partial": 0.5, "absent": 0.0}

    def _score_from_verdicts(self, requirements: list, verdicts: list):
        """Coverage as a ratio: what the profile closes over what was asked.

        A ratio rather than a sum, because the demand belongs in the denominator.
        A junior posting with three requirements where two are met is a better
        match than a senior one with twelve where four are — and the absolute
        bands the single-call prompt uses cannot express that at all: they measure
        the candidate, not the relation.

        A `silent` requirement leaves the denominator rather than scoring zero.
        Counting silence as failure is the same defect fixed in the domain
        modifier on 2026-08-16 — a profile that never discusses punctuality has
        not failed at it. Most of what a short profile does not cover is silence.

        Returns None when nothing was judgeable at all. There is no honest number
        for that, and inventing one is what this whole layer exists to stop.
        """
        by_index = {v.get("i"): v for v in verdicts if isinstance(v, dict)}
        earned = asked = 0.0
        for i, req in enumerate(requirements):
            verdict = (by_index.get(i) or {}).get("verdict")
            if verdict not in self._VERDICT_CREDIT:
                continue  # silent, missing, or a word we did not ask for
            weight = self._REQUIREMENT_WEIGHTS.get(req.get("importance"), 1.0)
            asked += weight
            earned += weight * self._VERDICT_CREDIT[verdict]
        if asked <= 0:
            return None
        return max(0, min(100, round(100 * earned / asked)))

    def _requirements_of(self, vacancy_text: str) -> dict:
        """Stage one: what the posting asks for. No candidate in this call.

        Deliberately given no profile at all, the way ask_modal_action is: the
        question is about the posting, and a profile in the context is something
        to read the posting against — which is the entanglement this layer exists
        to take apart.

        A requirement whose quote cannot be found in the posting is dropped. The
        quote is the whole basis for calling something a requirement, and unlike
        every other rule in these prompts this one can be enforced rather than
        asked for.
        """
        content = self._chat_completion(
            model=self.model, max_tokens=900, call_type="requirements",
            messages=[
                {"role": "system", "content": "You read job postings and list what they ask for."},
                {"role": "user", "content": f"{self._load_prompt('vacancy_requirements.md')}"
                                            f"\n\nVACANCY:\n{vacancy_text[:_MAX_VACANCY_CHARS]}"},
            ],
        )
        parsed = self._parse_json((content or "{}").strip(), fallback={})
        haystack = " ".join(vacancy_text.split()).lower()
        kept = []
        for req in parsed.get("requirements") or []:
            if not isinstance(req, dict) or not str(req.get("text", "")).strip():
                continue
            quote = " ".join(str(req.get("quote", "")).split()).lower()
            if not quote or quote not in haystack:
                continue
            kept.append({"text": str(req["text"]).strip(),
                         "importance": req.get("importance", "unspecified"),
                         "quote": req.get("quote", "")})
        parsed["requirements"] = kept
        return parsed

    def _match_requirements(self, requirements: list) -> dict:
        """Stage two: the candidate against that list, with no numbers in it.

        The posting's own text is not sent again — the requirements are what is
        left of it, and they are what the candidate is being read against. So
        neither call carries both large documents, which is where the input cost
        of the single call actually goes.
        """
        import json as _json
        listing = "\n".join(
            f"{i}. [{r.get('importance', 'unspecified')}] {r['text']}"
            for i, r in enumerate(requirements))
        prompt = self._load_prompt("requirement_match.md").replace("{{REQUIREMENTS}}", listing)
        content = self._chat_completion(
            model=self.model, max_tokens=900, call_type="score",
            messages=[
                {"role": "system", "content": self._system("score")},
                {"role": "user", "content": prompt},
            ],
        )
        return self._parse_json((content or "{}").strip(), fallback={})

    def score_vacancy_split(self, vacancy_text: str) -> dict | None:
        """Both stages plus our own arithmetic. None means "this did not work".

        Returning None rather than a guess is the point: the caller falls back to
        the single call, one extra request in a rare case, instead of a number
        with nothing behind it.
        """
        stage1 = self._requirements_of(vacancy_text)
        meta1 = last_call_snapshot()
        requirements = stage1.get("requirements") or []
        if not requirements:
            return None

        stage2 = self._match_requirements(requirements)
        meta2 = last_call_snapshot()
        verdicts = stage2.get("verdicts") or []
        score = self._score_from_verdicts(requirements, verdicts)
        if score is None:
            return None

        judged = {v.get("i"): v.get("verdict") for v in verdicts if isinstance(v, dict)}
        gaps = [r["text"] for i, r in enumerate(requirements)
                if judged.get(i) in ("absent", "partial")]

        result = {
            "score": score,
            "matched_skills": stage2.get("matched_skills") or [],
            "gaps": gaps,
            "signals": stage1.get("signals") or [],
            "stop_match": stage2.get("stop_match"),
            "stop_basis": stage2.get("stop_basis"),
            "stop_evidence": stage2.get("stop_evidence"),
            "vacancy_role_type": stage1.get("vacancy_role_type"),
            "role_type_match": stage2.get("role_type_match"),
        }
        scored = self._sanitize_score_result(result)
        # Both calls, or the token figures understate the split by half and the
        # comparison it exists to enable would be wrong in its own favour.
        scored["call_meta"] = {
            "generation_id": meta2.get("generation_id"),
            "generation_id_stage1": meta1.get("generation_id"),
            "tokens_prompt": (meta1.get("tokens_prompt") or 0) + (meta2.get("tokens_prompt") or 0),
            "tokens_completion": (meta1.get("tokens_completion") or 0) + (meta2.get("tokens_completion") or 0),
            "stages": 2,
        }
        scored["requirements"] = requirements
        return scored

    def score_vacancy(self, vacancy_text: str) -> dict:
        """Returns {score, axes, matched_skills, signals, stop_match, ...}.

        The model grades five axes; `score` is computed here from those grades by
        core.axes, not returned by the model. `axes` rides along beside it so the
        number can be argued with: every score decomposes into the grades it came
        from, which the single opaque integer never did.

        stop_match: str category name if LLM detected a blocked category, else None.
        The list of blocked categories comes from the `stop_categories:` line in
        candidate.md, already in the system prompt — no extra parameters needed.
        """
        if os.getenv("SNAGGD_SPLIT_SCORING", "").strip() in ("1", "true", "yes"):
            split = self.score_vacancy_split(vacancy_text)
            if split is not None:
                return split
            print("   ℹ️ split scoring produced nothing to judge — falling back to one call")

        prompt = self._load_prompt("match_scoring.md")
        # Passed in, not left to survive the projection. The block vocabulary used
        # to reach the model only because it sat in a section the scorer happened
        # to receive whole — together with four keys about what the person WANTS,
        # which is what the projection is there to withhold. A list the answer is
        # validated against is data this call needs; it says so.
        declared = sorted(self._declared_stop_categories())
        blocks = ("BLOCKED CATEGORIES (this candidate's own list — the entire "
                  f"vocabulary available to you): {', '.join(declared)}"
                  if declared else
                  "BLOCKED CATEGORIES: this candidate declared none. stop_match is null.")
        content = self._chat_completion(
            model=self.model,
            max_tokens=400,
            call_type="score",
            messages=[
                {"role": "system", "content": self._system("score")},
                {"role": "user", "content": f"{prompt}\n\n{blocks}\n\nVACANCY:\n{vacancy_text[:_MAX_VACANCY_CHARS]}"},
            ],
        )
        # Taken here, one frame after the call and in the same thread — see
        # last_call_snapshot(). A cache hit never reaches this line, so a
        # restored score carries no call meta at all, which is the truth about
        # it: there was no call.
        call_meta = last_call_snapshot()
        raw = (content or "{}").strip()
        # No "score" here and no default of 50. The model is not asked for a number
        # any more, and a fabricated 50 was a real number standing in for an answer
        # nobody gave — indistinguishable, downstream, from a genuine middling match.
        result = self._parse_json(raw, fallback={
            "axes": {},
            "matched_skills": [],
            "signals": [],
            "stop_match": None,
            "stop_basis": None,
            "stop_evidence": None,
        })
        scored = self._sanitize_score_result(result)
        # Rides alongside the answer rather than in it: sanitising guards the
        # analysis fields, and this is not one of them — it describes the call,
        # not the vacancy. Never cached (see llm_cover): a restored entry has
        # no call behind it.
        scored["call_meta"] = call_meta
        return scored

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
                {"role": "system", "content": self._system("fill_form")},
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
                {"role": "system", "content": self._system("answer_question")},
                {"role": "user", "content": (
                    "Answer this HR screening question briefly (2–4 sentences, in Russian). "
                    "Use only facts from the candidate profile above. Do not invent.\n\n"
                    f"Question: {question}"
                )},
            ],
        )
        return content.strip()

    # ── System prompt (built once, cached) ───────────────────────────────────

    # Which reader each call type speaks as. modal_action is absent on purpose:
    # it never asks for the profile at all. So is the CV parse, which builds its
    # own messages through GatewayClient — reading an existing person while
    # parsing a new resume would be a leak, not a convenience.
    _CALL_READERS = {"score": "score", "cover": "cover",
                     "fill_form": "answer", "answer_question": "answer"}

    def _system(self, call_type: str | None = None) -> str:
        """The profile this particular call is entitled to read.

        Every call used to receive the whole file. That is how the scorer ended
        up holding a salary range and a relocation preference while deciding
        whether someone can do a job — present in the context, influencing by
        presence, with no rule attached to either.

        Off by default. This is the first change that alters what the model
        sees, so switching it on is a decision with a measurement behind it, not
        a side effect of installing a version. SNAGGD_PROJECT_PROFILE=1 enables.
        """
        reader = self._CALL_READERS.get(call_type or "")
        # Scoring always projects. It is not an option there: judging whether a
        # person can do a job while holding their salary range and relocation
        # preference is the coupling this rebuild exists to remove, and a scorer
        # measured with the whole profile in context is not the scorer that ships.
        # Every other reader still waits behind the flag — the writer and the
        # answerer legitimately want the whole document, and narrowing them is a
        # separate change with its own measurements.
        if reader and reader != "score" and \
                os.getenv("SNAGGD_PROJECT_PROFILE", "").strip() not in ("1", "true", "yes"):
            reader = ""
        # Keyed by reader, not one string: a single cache would freeze whichever
        # slice happened to be built first and hand it to every other call.
        if reader not in self._system_prompts:
            self._system_prompts[reader] = self._build_system_prompt(reader)
        return self._system_prompts[reader]

    # One preamble per reader, because they are not doing the same job. Until
    # 2026-08-22 every call — including scoring — opened with "You are a job
    # application assistant. Help craft personalized applications", which is the
    # letter writer's brief. The scoring call then spent nine thousand characters
    # saying the opposite: judge, do not write, do not decide. Asking a model to
    # ignore its own system prompt is not a instruction, it is a handicap, and it
    # was there because one preamble predated there being more than one caller.
    _PREAMBLES = {
        "score": ("You assess how a candidate's record lines up with a job posting. "
                  "You do not write anything for them and you do not decide whether "
                  "to apply — you report what you observe, and the decision is made "
                  "outside this call.\n"),
        "cover": ("You are a job application assistant. Help craft personalized "
                  "applications based on the candidate profile below.\n"),
        "answer": ("You answer an employer's questions on the candidate's behalf, "
                   "from their profile below and nothing else.\n"),
    }
    _DEFAULT_PREAMBLE = ("You are a job application assistant. "
                         "Help craft personalized applications based on the "
                         "candidate profile below.\n")

    def _build_system_prompt(self, reader: str = "") -> str:
        parts = [self._PREAMBLES.get(reader, self._DEFAULT_PREAMBLE)]
        # One document. There used to be two — job_preferences.md sat beside this one
        # and reached the model in the same prompt, holding its own answers to the same
        # questions; on a live profile it said "Not specified — open to market rate"
        # while candidate.md gave a range. Dropped 2026-08-21. The loop stays a loop
        # only because a second document is a plausible future, not because one exists.
        for filename, label in [("candidate.md", "CANDIDATE PROFILE")]:
            content = self._load_profile(filename)
            if content:
                if reader:
                    # Projection happens per document, so a second one would be
                    # projected by the same rule rather than slipping past it.
                    from onboarding.profile_frame import project_for
                    content = project_for(content, reader)
                if content:
                    parts.append(f"## {label}\n{content}")
        return "\n\n".join(parts)

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
        """Type-guards scoring output, and turns graded axes into the score.

        The score is computed here rather than read from the reply. No clamping is
        needed as a result: arithmetic that cannot leave [0, 100] cannot overflow it,
        and the clamp that used to live here was containing our own modifier stack.

        Template-echo (an unfilled <TOKEN> anywhere in the answer) makes the whole
        response untrusted, not just the field carrying it — a model confused enough
        to echo the schema is not a reliable source for the rest either.
        """
        from core.axes import (AXES, axes_present, normalise_label, score_from_axes,
                               validate_matched_skills)

        if self._is_template_echo(result):
            return {"score": None, "axes": {}, "matched_skills": [], "signals": [],
                    "stop_match": None, "stop_basis": None, "stop_evidence": None,
                    "stop_suppressed": None, "scoring_format": _SCORING_FORMAT}

        # ── Axes ─────────────────────────────────────────────────────────────
        raw_axes = result.get("axes")
        raw_axes = raw_axes if isinstance(raw_axes, dict) else {}
        axes: dict = {}
        grades: dict = {}
        for axis in AXES:
            entry = raw_axes.get(axis)
            if not isinstance(entry, dict):
                continue
            grade = normalise_label(entry.get("grade"))
            anchor = entry.get("anchor")
            anchor = str(anchor).strip() if isinstance(anchor, (str, int, float)) else ""
            if grade is None:
                # Kept out of the arithmetic but not out of the record: an invented
                # grade has to be visible, and score_from_axes counts it.
                grades[axis] = entry.get("grade")
                continue
            grades[axis] = grade
            axes[axis] = {"grade": grade, "anchor": anchor}

        # An axis the document does not speak to cannot be graded, and the prompt
        # saying so was not enough — asked politely, the model still graded education
        # `weak` on a profile with no education section, twice out of twelve. So the
        # code decides it: we know what the document contains.
        #
        # Only when the profile speaks our vocabulary at all. A hand-written one whose
        # headings the frame does not recognise yields an empty set, and coercing on
        # that would silence every axis and score nothing — absence is only meaningful
        # once presence is legible.
        present = self._profile_axes()
        ungrounded = []
        if present:
            for axis in list(grades):
                if axis not in present and grades.get(axis) != "neutral":
                    ungrounded.append(axis)
                    grades[axis] = "neutral"
                    axes.pop(axis, None)
        if ungrounded:
            result["axes_ungrounded"] = ungrounded

        verdict = score_from_axes(grades)
        result["axes"] = axes
        result["score"] = verdict.score
        result["axes_in_play"] = list(verdict.in_play)
        result["axes_neutral"] = list(verdict.neutral)
        result["non_compensable"] = list(verdict.non_compensable)
        if verdict.unknown_labels:
            result["axes_unknown"] = {k: str(v) for k, v in verdict.unknown_labels.items()}
            print(f"   ⚠️ grades outside the vocabulary: {result['axes_unknown']}")

        # ── Lists ────────────────────────────────────────────────────────────
        for key in ("signals", "matched_skills"):
            val = result.get(key, [])
            if not isinstance(val, list):
                result[key] = []
            else:
                result[key] = [str(x) for x in val if isinstance(x, (str, int, float)) and str(x).strip()]

        kept, dropped = validate_matched_skills(result["matched_skills"], self._declared_skills())
        result["matched_skills"] = kept
        result["matched_skills_dropped"] = dropped

        # gaps is gone on purpose. Over 913 live applications it produced 2190
        # distinct strings — roughly three and a half new ones per vacancy — so the
        # aggregate built on it was counting near-duplicates it had to normalise with
        # a regular expression first. An axis graded `weak` says the same thing in a
        # form that is comparable between two vacancies.
        result.pop("gaps", None)

        # Which shape this record is in. Without it, an aggregate cannot tell a
        # verbatim selection from the paraphrases that preceded it, and would average
        # the two into a number that describes neither.
        result["scoring_format"] = _SCORING_FORMAT

        result.update(self._validated_block(result))
        return result

    def _profile_axes(self) -> set:
        """Which axes this profile's own document speaks to. Empty when unreadable.

        Empty is also what an unrecognised document gives, and the caller treats both
        the same way — it coerces nothing. That is deliberate: absence of a section is
        only evidence once the document's presence is legible to us.
        """
        if getattr(self, "_profile_axes_cache", None) is None:
            try:
                from core.axes import axes_present
                self._profile_axes_cache = axes_present(self._load_profile("candidate.md"))
            except Exception:
                self._profile_axes_cache = set()
        return self._profile_axes_cache

    def _declared_skills(self) -> list:
        """Everything on the candidate's own `skills:` and `tools:` lines.

        The closed vocabulary matched_skills must select from. Read from the profile
        for the same reason the stop categories are: what the person listed is theirs
        to decide, and a skill nobody claimed is the model improvising.
        """
        # getattr rather than attribute access: this is reachable on an instance
        # built through __new__ (the sanitiser is unit-tested that way, without a
        # profile on disk), and a guard that raises there is a guard that stops the
        # thing it guards from being testable.
        if getattr(self, "_declared_skills_cache", None) is None:
            out: list = []
            try:
                text = self._load_profile("candidate.md")
                for line in text.splitlines():
                    low = line.lower()
                    if not (low.startswith("skills:") or low.startswith("tools:")):
                        continue
                    for raw in line.split(":", 1)[1].split(","):
                        val = raw.strip()
                        if val and val not in out:
                            out.append(val)
            except Exception:
                out = []
            self._declared_skills_cache = out
        return self._declared_skills_cache

    def _declared_stop_categories(self) -> set:
        """The candidate's own stop_categories — the entire vocabulary a block may
        use. Read from the profile rather than hardcoded, because which categories
        exist at all is the person's decision: a category nobody declared is not a
        rule, it is the model improvising."""
        if self._stop_categories is None:
            try:
                from utils.filters import load_stop_filters
                self._stop_categories = {
                    str(c).strip().lower()
                    for c in load_stop_filters(self._data_dir).categories if str(c).strip()
                }
            except Exception:
                self._stop_categories = set()
        return self._stop_categories

    def _validated_block(self, result: dict) -> dict:
        """A block is the strongest thing done to a vacancy — no application is sent
        at all — so it has to be declared and it has to be evidenced.

        Both requirements come from reviewing every block one profile had made: of
        29, five were plainly wrong and one was not a category at all but a UI
        object's repr that had leaked into the reply, blocking a bank because a
        non-empty string is truthy. The wrong ones shared a shape — a
        neighbouring field read as the field itself: products sharing users or
        mechanics with a blocked category, a vendor selling tooling into one, and
        an employer whose name merely resembled a brand in one.

        Undeclared category, missing basis or missing evidence all mean the same
        thing here: not a block. Erring toward applying is deliberate — a false
        block costs an opportunity the person never learns they had, while a
        missed block costs one application they can see and stop.
        """
        none = {"stop_match": None, "stop_basis": None, "stop_evidence": None,
                "stop_suppressed": None}
        raw = result.get("stop_match")
        if not isinstance(raw, str) or not raw.strip():
            return none

        category = raw.strip().lower()
        declared = self._declared_stop_categories()
        if category not in declared:
            print(f"   ℹ️ not blocking on {raw!r} — this profile declares "
                  f"{sorted(declared) or 'no stop categories'}")
            return none

        basis = result.get("stop_basis")
        evidence = result.get("stop_evidence")
        if basis not in ("text", "company_knowledge") or not (
                isinstance(evidence, str) and evidence.strip()):
            print(f"   ℹ️ not blocking on {category!r} — no basis or evidence given")
            return none

        def _refused(why: str) -> dict:
            """Refused, and said so in the record rather than silently.

            A suppressed block is the one thing this method does that nobody can
            see afterwards: the vacancy simply proceeds. Writing down what was
            proposed is how we find out later whether the suppression is right —
            without it, over-suppressing looks exactly like nothing happening."""
            print(f"   ℹ️ not blocking on {category!r} — {why}")
            out = dict(none)
            out["stop_suppressed"] = {"category": category, "basis": basis,
                                      "evidence": evidence.strip(), "why": why}
            return out

        # Both checks below are scoped to company knowledge on purpose. A quote
        # from the posting supports itself: refusing it for a formatting
        # deficiency would mean applying to an employer the person explicitly
        # excluded, in the one case where the evidence is unambiguous. All three
        # text-based blocks in the 2026-08-18..20 measurement were correct, and
        # the degenerate answer below arrived on company knowledge, not on text.
        if basis == "company_knowledge":
            # An answer with no analysis in it is not an answer about this
            # vacancy. The prompt asks for 3-5 signals on every call, so all
            # three lists empty means the model declined to do the work — and one
            # real block arrived exactly like that, with nothing in it but the
            # category and a story about the employer.
            if not any(result.get(k) for k in ("matched_skills", "axes", "signals")):
                return _refused("the answer carries no analysis at all")

        # Company knowledge has to be corroborated by the model's own signals.
        # The prompt already says "if the signals you are producing contradict the
        # category you are about to block on, do not block" — a rule with nobody
        # to enforce it. Every false block measured on 2026-08-18..20 named a
        # category that appears nowhere in its own signals: an employer blocked
        # for its parent group, a bank blocked through a chain of ownership, an
        # ML vendor blocked for what its CUSTOMERS do. A "<category>_adjacent"
        # signal is the prompt's own marker for NOT blocking, so it corroborates
        # nothing.
        #
        # Text stays exempt: a quote from the posting is self-supporting, and all
        # three text-based blocks in the same measurement were correct.
        if basis == "company_knowledge":
            signals = [str(x).lower() for x in (result.get("signals") or [])]
            corroborating = [x for x in signals
                             if category in x and "adjacent" not in x]
            if not corroborating:
                return _refused("company knowledge is not corroborated by its own signals")

        return {"stop_match": category, "stop_basis": basis,
                "stop_evidence": evidence.strip(), "stop_suppressed": None}

    def _is_template_echo(self, result: dict) -> bool:
        """True if anything anywhere in the answer is still an unfilled <TOKEN> from
        match_scoring.md's own JSON example rather than real content. Shape-based and
        recursive, so it stays valid when the prompt's example wording changes and it
        reaches the grades and anchors nested inside `axes`.
        """
        def _walk(val) -> bool:
            if isinstance(val, str):
                return bool(_PLACEHOLDER_TOKEN_RE.search(val))
            if isinstance(val, dict):
                return any(_walk(v) for v in val.values())
            if isinstance(val, list):
                return any(_walk(v) for v in val)
            return False

        # Walks rather than checking a fixed key list: grades and anchors live one
        # level down inside `axes`, and a per-key check would not have seen them.
        return _walk(result)

    def _parse_json(self, raw: str, fallback: dict) -> dict:
        """Parse a model reply into a dict, or return the caller's fallback.

        The return type says dict and every caller believes it — they go
        straight to .get(). Until 2026-08-12 this returned whatever json.loads
        produced, so a reply that was valid JSON but the wrong SHAPE came back
        as a list, a string or None and blew up one frame later, far from here.

        Live: 2026-08-12, a scoring call answered with a JSON array. That
        surfaced as `Score error: 'list' object has no attribute 'get'`, then
        as "LLM unavailable — skipping vacancy", and every vacancy in the run
        was skipped without a score. The model was reachable and answering the
        whole time; nothing about that message was true.

        Wrapping a single object in an array is the most common way a model
        gets this wrong, so that one case is unwrapped rather than discarded.
        Anything else falls back — a wrong-shaped answer is a missing answer.
        """
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            if _HAS_JSON_REPAIR:
                try:
                    parsed = json.loads(_repair_json(raw))
                    _note_json_repair(LAST_CALL.get("call_type") or "unknown call")
                except Exception:
                    parsed = None

        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
            print("   ℹ️ Model wrapped its JSON object in an array — unwrapped")
            return parsed[0]
        if parsed is not None:
            print(f"   ⚠️ Model returned valid JSON of the wrong shape "
                  f"({type(parsed).__name__}) — using the fallback")
        return fallback


class GatewayClient:
    """Drop-in replacement for a raw OpenAI client's .chat.completions.create()
    interface — routes through _chat_completion() instead of calling OpenRouter
    directly, with no change needed at the caller's own call sites.

    Exists so per-call policy has exactly one home. onboarding/resume_parser.py
    takes a *client*, not an agent, and issued its own calls; the CV parse was
    therefore the single call the gateway could not see — and it is the call
    with by far the largest token ceiling, the one place a reply cut short is
    quietly repaired into a shorter profile with no complaint anywhere.

    Narrow on purpose: model/messages/max_tokens in, response.choices[0].
    message.content out. Any other raw-client caller with that same shape can
    use it.
    """

    def __init__(self, agent: "LLMAgent", call_type: str | None = None):
        self._agent = agent
        self._call_type = call_type

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, *, model: str, messages: list, max_tokens: int):
        content = self._agent._chat_completion(
            model=model, messages=messages, max_tokens=max_tokens, call_type=self._call_type,
        )
        _Msg = type("_Msg", (), {"content": content})
        _Choice = type("_Choice", (), {"message": _Msg()})
        return type("_Response", (), {"choices": [_Choice()]})()
