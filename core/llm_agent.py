"""
LLM agent — single client for all AI calls (cover letter, scoring, HR questions).
Uses OpenRouter as gateway; model configured via LLM_MODEL env var.
"""

import json
import os
import re
from pathlib import Path

from openai import OpenAI

from config import CONFIG

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_MAX_VACANCY_CHARS = CONFIG.llm_max_input_chars

try:
    from json_repair import repair_json as _repair_json
    _HAS_JSON_REPAIR = True
except ImportError:
    _HAS_JSON_REPAIR = False


class LLMAgent:
    def __init__(self, data_dir: Path = None):
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise RuntimeError("LLM_API_KEY not set — add it to .env")

        self._data_dir = data_dir or CONFIG.data_dir
        self.model = os.getenv("LLM_MODEL", "deepseek/deepseek-v3.2")
        self.cover_model = os.getenv("COVER_MODEL", self.model)
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        self._system_prompt: str | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_cover(self, vacancy_text: str, match_context: dict | None = None) -> str:
        """Generate cover letter.

        match_context: optional dict from score_vacancy() — matched_skills, gaps, signals,
        vacancy_role_type. Injected as a compact SCORING CONTEXT block so the model writes
        precisely to what actually overlaps instead of re-analysing the vacancy cold.
        """
        prompt = self._load_prompt("cover_letter.md")
        hint = self._build_match_hint(match_context) if match_context else ""
        resp = self.client.chat.completions.create(
            model=self.cover_model,
            max_tokens=800,
            messages=[
                {"role": "system", "content": self._system()},
                {"role": "user", "content": f"{prompt}{hint}\n\nVACANCY:\n{vacancy_text[:_MAX_VACANCY_CHARS]}"},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    def score_vacancy(self, vacancy_text: str) -> dict:
        """Returns {score, matched_skills, gaps, signals, stop_match}.

        stop_match: str category name if LLM detected a blocked category, else None.
        The list of blocked categories comes from stop_categories in the system prompt
        (loaded from job_preferences.md) — no extra parameters needed.
        """
        prompt = self._load_prompt("match_scoring.md")
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=400,
            messages=[
                {"role": "system", "content": self._system()},
                {"role": "user", "content": f"{prompt}\n\nVACANCY:\n{vacancy_text[:_MAX_VACANCY_CHARS]}"},
            ],
        )
        raw = (resp.choices[0].message.content or "{}").strip()
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
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=800,
            messages=[
                {"role": "system", "content": self._system()},
                {"role": "user", "content": prompt},
            ],
        )
        raw = (resp.choices[0].message.content or "{}").strip()
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
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=50,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = (resp.choices[0].message.content or "{}").strip()
            result = self._parse_json(raw, fallback={"action": "skip"})
            if result.get("action") == "click" and isinstance(result.get("button_index"), int):
                return result
            return {"action": "skip"}
        except Exception:
            return {"action": "skip"}

    def answer_question(self, question: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=200,
            messages=[
                {"role": "system", "content": self._system()},
                {"role": "user", "content": (
                    "Answer this HR screening question briefly (2–4 sentences, in Russian). "
                    "Use only facts from the candidate profile above. Do not invent.\n\n"
                    f"Question: {question}"
                )},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

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
        Passes through unchanged when LLM output is well-formed.
        """
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
