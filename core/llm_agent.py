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


# Case and quotation marks only. Deliberately NOT a fuzzy match: the whole point of
# the check that uses this is to separate a name from one that merely resembles it,
# so anything tolerant of a character difference would defeat it. Russian «» and the
# various apostrophes are stripped because a model quoting a posting keeps them, and
# a name inside guillemets is the same name.
_MATCH_STRIP = str.maketrans("", "", "«»\"'`“”‘’")


def _fold_for_match(text: str) -> str:
    return str(text).lower().translate(_MATCH_STRIP)


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

    # Two-stage "split" scoring lived here until 2026-08-25, behind
    # SNAGGD_SPLIT_SCORING: stage one read a posting for its requirements
    # in its own prompt, stage two matched the profile against them in a second, and
    # code turned the verdicts into a coverage ratio. Both prompt files went with it;
    # they are deliberately not named here, because a name in a comment is what
    # tests/test_docs_name_real_files.py reads as a live claim about a file.
    #
    # Removed as a rejected experiment, not as working code nobody switched on. The
    # measurement that rejected it is in tz-2026-08-22 §2: stage one, asked to read a
    # posting with no candidate in front of it, cannot rank — 12 requirements out of
    # 12 came back "unspecified", the reply was truncated in 11 runs out of 12, and
    # the output grew 5.9x for it. The design that replaced it asks one call to grade
    # five axes AGAINST the person, which is the comparison stage one was missing.
    #
    # Two things made keeping it worse than deleting it. Its prompts asked for
    # `role_type`, a field retired the same day this was removed, so the flag no
    # longer described anything the schema had. And a flag that silently swaps the
    # scorer is a second scorer to keep true — every change to axes, anchors, the
    # stop rule or the arithmetic would have had to be made twice or knowingly not
    # made twice. It is in git history if the idea is ever revisited; what it must
    # not be is a live path pretending to be maintained.

    def score_vacancy_judgement(self, vacancy_text: str) -> dict:
        """The scorer as one judgement, with the number coming from the model.

        The track this project abandoned when the axes landed, brought back to be
        measured rather than remembered. It was abandoned partly for zero-scores
        that were later found to be a property of the model and fixed elsewhere —
        so half its sentence was served for someone else's crime, and it has never
        been run since the fix.

        Experiment, not an option. Selected by SNAGGD_SCORER=judgement, lives on
        the scoring/judgement branch, and is expected to die there: the likely
        destination is a mix — a freer judgement INSIDE each axis with the
        arithmetic still in code — and that is a change to the axes scorer, not a
        third strategy beside it. See tz-2026-08-28 for the measurement this
        exists to produce.

        Differences from the axes path, all deliberate:
          - `match` is taken from the reply. That is the whole point, and it is
            why the guard test that forbids it is scoped to the axes path rather
            than to the process.
          - `axes` comes back empty. Nothing downstream requires it (llm_cover
            reads it with `or {}`), and an empty dict is the truth: this scorer
            did not grade axes.
          - `basis` takes the anchors' place in what the writer is given. Both are
            the same kind of observation — what this posting asked, and what
            answered it — so the letter keeps the per-vacancy hook either way.
        """
        prompt = self._load_prompt("judgement_scoring.md")
        declared = sorted(self._declared_stop_categories())
        blocks = ("BLOCKED (this candidate's own list — categories of business and "
                  "named employers, the entire vocabulary available to you): "
                  f"{', '.join(declared)}"
                  if declared else
                  "BLOCKED: this candidate declared nothing. stop_match is null.")
        content = self._chat_completion(
            model=self.model,
            max_tokens=900,
            call_type="score",
            messages=[
                {"role": "system", "content": self._system("score")},
                {"role": "user", "content": f"{prompt}\n\n{blocks}\n\nVACANCY:\n{vacancy_text[:_MAX_VACANCY_CHARS]}"},
            ],
        )
        call_meta = last_call_snapshot()
        result = self._parse_json((content or "{}").strip(), fallback={})

        # The guard the arithmetic used to provide. A model that has seen a
        # thousand scoring prompts will occasionally answer 454, -5, or "紙 67" —
        # all three are real replies from this project's own history. With the
        # number now coming from the reply, out-of-range is not clamped to a
        # neighbouring value: a reply that cannot be trusted about the range
        # cannot be trusted about the judgement either, and a missing score is
        # not a low one.
        raw_match = result.get("match")
        score = raw_match if isinstance(raw_match, int) and 0 <= raw_match <= 100 else None
        if score is None and raw_match is not None:
            print(f"   ⚠️ judgement scorer returned an unusable match ({raw_match!r}) — no score")

        # A number with nothing behind it is the failure mode this prompt is most
        # likely to produce, so it is checked rather than trusted: `basis` is what
        # the model says it counted, and a high score asserted without one is not
        # a judgement, it is a guess wearing one.
        basis = [b for b in (result.get("basis") or []) if isinstance(b, dict)]
        if score is not None and score >= 50 and not basis:
            print(f"   ⚠️ judgement scorer gave {score} with an empty basis — not trusting it")
            score = None

        # The same validator the axes path uses, not a second reading of the
        # field: a block must name a category the person actually declared, and
        # the rule about that is one rule, not one per scorer.
        stop = self._validated_block(result)
        signals = [str(s).strip() for s in (result.get("signals") or []) if str(s).strip()][:8]
        return {
            "score": score,
            "scoring_format": "judgement",
            # Empty on purpose — this scorer graded nothing. Written out rather
            # than omitted so the History screen and the log see a shape they
            # already know how to read.
            "axes": {}, "axes_in_play": [], "axes_neutral": [],
            "non_compensable": [], "role_fit": None,
            "matched_skills": [], "matched_skills_dropped": 0,
            "signals": signals,
            "stop_match": stop.get("stop_match"),
            "stop_basis": stop.get("stop_basis"),
            "stop_evidence": stop.get("stop_evidence"),
            "stop_suppressed": stop.get("stop_suppressed"),
            # The experiment's own record. `counted_unnamed` is the measured
            # quantity: evidence read out of work that never names what was asked
            # — the thing an axis cannot see, because an axis reads the vocabulary.
            "basis": basis,
            "gaps": [str(g) for g in (result.get("gaps") or [])][:10],
            "counted_unnamed": [u for u in (result.get("counted_unnamed") or []) if isinstance(u, dict)],
            "confidence": result.get("confidence"),
            "call_meta": call_meta,
        }

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
        if os.getenv("SNAGGD_SCORER", "").strip().lower() == "judgement":
            return self.score_vacancy_judgement(vacancy_text)

        prompt = self._load_prompt("match_scoring.md")
        # Passed in, not left to survive the projection. The block vocabulary used
        # to reach the model only because it sat in a section the scorer happened
        # to receive whole — together with four keys about what the person WANTS,
        # which is what the projection is there to withhold. A list the answer is
        # validated against is data this call needs; it says so.
        declared = sorted(self._declared_stop_categories())
        # "categories or named employers" since 2026-08-25: the wizard now collects
        # both through one field, because a person refusing work does not sort their
        # own refusals into a semantic tier and an exact-match tier — that split is
        # ours, not theirs. Naming both here is what lets the prompt's two judging
        # rules (primary domain for a kind, identity for a name) apply to the right
        # entry. Header said "CATEGORIES" alone while a name could arrive on the list.
        blocks = ("BLOCKED (this candidate's own list — categories of business and "
                  "named employers, the entire vocabulary available to you): "
                  f"{', '.join(declared)}"
                  if declared else
                  "BLOCKED: this candidate declared nothing. stop_match is null.")
        content = self._chat_completion(
            model=self.model,
            # A ceiling is a cap, not a budget: output tokens are billed as
            # generated, so headroom is free and a truncated answer is not. 400 was
            # already cutting 8% of replies (measured 2026-08-24 over 140 calls) and
            # `asked` — five more fields — took that to 22%. Every one of those was
            # then "repaired" by json_repair into a shape that parses, which is the
            # failure mode split scoring was rejected for: an answer that looks
            # whole and lost its tail.
            max_tokens=700,
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
            "role_fit": None,
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

        Passes role type, vacancy signals (direction vector), and matched skills
        (ATS intersection — framed as "weave naturally" to prevent enumeration).
        The score is deliberately NOT here. It is our verdict about this person's
        distance from the posting, and handing it to the one call whose whole job is
        to advocate for them puts the advocacy and the doubt in the same prompt — the
        same mistake the system preamble carried until it was split per reader. What
        stays is observation: the signals that say what this vacancy is, and the
        skills the profile and the posting genuinely share. What the letter must not
        receive is a number telling it how convinced we are.

        Quality here is not measurable — one metric under four names, and no labelled
        truth — so this is argued, not measured, and says so.
        """
        parts = []
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
        # Anchors only, never the grade beside them: the anchor says what the
        # posting asked about, the grade says how convinced we are, and only the
        # first is this call's business (same line the score is kept out on).
        # Axes in play only — an axis the posting never raised has an anchor
        # saying so ("сертификаты не требуются"), which is true and useless to
        # someone writing to that employer.
        axes = match_context.get("axes") or {}
        in_play = match_context.get("axes_in_play") or []
        anchored = [(a, (axes.get(a) or {}).get("anchor", "").strip())
                    for a in in_play if (axes.get(a) or {}).get("anchor", "").strip()]
        if anchored:
            lines = "\n".join(f"  {axis} — {anchor}" for axis, anchor in anchored)
            parts.append(
                "What this posting actually asked about, in its own terms — use it to "
                "decide WHICH of the candidate's cases to open with, not as phrases to "
                f"reuse:\n{lines}"
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
        from core.axes import (AXES, axes_present, grade_after_asked, normalise_label,
                               normalise_role_fit, score_from_axes, validate_matched_skills)

        if self._is_template_echo(result):
            return {"score": None, "axes": {}, "role_fit": None, "matched_skills": [],
                    "signals": [],
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
            # The posting is one question, the profile another. See grade_after_asked.
            grade, coerced_from = grade_after_asked(grade, entry.get("asked"))
            if grade is None:
                # Kept out of the arithmetic but not out of the record: an invented
                # grade has to be visible, and score_from_axes counts it.
                grades[axis] = entry.get("grade")
                continue
            grades[axis] = grade
            axes[axis] = {"grade": grade, "anchor": anchor}
            if coerced_from:
                # Kept in the record, not swallowed: the model contradicting its own
                # `asked` is exactly what a later measurement will want to count.
                axes[axis]["coerced_from"] = coerced_from

        # An absent section is a real gap in the document an employer reads, so it is
        # graded as one. Grounding is passed only to the non-compensable gate: 11 of
        # 13 live profiles carry no certificates section, and refusing to apply
        # forever on a miss there would be deciding from data we never collected.
        verdict = score_from_axes(grades, grounded=self._profile_axes())
        result["axes"] = axes
        result["score"] = verdict.score
        result["axes_in_play"] = list(verdict.in_play)
        result["axes_neutral"] = list(verdict.neutral)
        result["non_compensable"] = list(verdict.non_compensable)
        if verdict.unknown_labels:
            result["axes_unknown"] = {k: str(v) for k, v in verdict.unknown_labels.items()}
            print(f"   ⚠️ grades outside the vocabulary: {result['axes_unknown']}")

        # ── The second question ──────────────────────────────────────────────
        # Recorded, not applied. What a distant role costs is a calibration question,
        # and calibrating against a guess is how the domain modifier ended up halved
        # twice and still called a stopgap. It rides beside the score until there is
        # something to weigh it against.
        raw_fit = result.get("role_fit")
        fit = raw_fit if isinstance(raw_fit, dict) else {}
        value = normalise_role_fit(fit.get("value"))
        if value is None:
            result["role_fit"] = None
        else:
            anchor = fit.get("anchor")
            result["role_fit"] = {
                "value": value,
                "anchor": str(anchor).strip() if isinstance(anchor, (str, int, float)) else "",
            }

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

        # A block on an EMPLOYER NAME must quote the name it is blocking on.
        #
        # Names reached this tier on 2026-08-25, when one wizard field started
        # feeding both stop tiers. Everything below was built and measured for
        # CATEGORIES, and names break one of its premises: "a quote from the
        # posting supports itself" holds for a kind of business, because the quote
        # has to describe the business. It does not hold for a name, because a
        # posting is full of company names — clients, partners, vendors — and a
        # name that merely RESEMBLES the declared one reads as a quote just fine.
        #
        # Measured, twice, on five synthetic postings (deepseek-v3.2):
        #   · before the prompt said which evidence counts for a name, the employer
        #     itself was not blocked (the model reached for company_knowledge for
        #     something the posting said out loud) while a company whose CLIENT was
        #     the named employer WAS blocked. Wrong in both directions.
        #   · after the prompt fixed that, the employer itself blocked correctly and
        #     a grocery chain called «Монетка» blocked as «Монеткин», quoting its own
        #     name as the evidence.
        #
        # The prompt says resemblance is not evidence. It said so during the run
        # that produced the false block, which makes it a rule with nobody to
        # enforce it — the same shape as the company_knowledge corroboration below,
        # and the same answer: the model labels, the code decides. `stop_kind` is
        # the label; this is the decision.
        #
        # Deliberately NOT applied to a category: a category legitimately matches
        # wording that never contains the word (a posting saying "онлайн-казино"
        # evidences a gambling block without spelling it), and demanding containment
        # there would break the semantic tier's whole reason to exist.
        if result.get("stop_kind") == "employer" and basis == "text":
            haystack = _fold_for_match(evidence)
            if _fold_for_match(category) not in haystack:
                return _refused("an employer block must quote the name it blocks on, "
                                "and this quote does not contain it")

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
                "stop_evidence": evidence.strip(), "stop_suppressed": None,
                # Which of the two judgements the model made. Carried out of here so
                # a reviewer can see WHICH guard a block passed, not only that it
                # passed one. Not yet threaded into the written record (llm_cover /
                # adapter carry stop_basis and stop_evidence) — see the TZ.
                "stop_kind": result.get("stop_kind")}

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
