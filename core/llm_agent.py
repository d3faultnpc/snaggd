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
    def __init__(self):
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise RuntimeError("LLM_API_KEY not set — add it to .env")

        self.model = os.getenv("LLM_MODEL", "anthropic/claude-3-5-haiku")
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
            model=self.model,
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
        return result

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
        """Compact scoring summary injected between cover_letter prompt and VACANCY block.

        Gives the cover model pre-computed match signals so it writes specifically
        to the real overlap — not to the vacancy text cold.
        Only non-empty / meaningful fields are included to avoid token waste.
        """
        parts = []
        score = match_context.get("score")
        if score is not None:
            parts.append(f"Match score: {score}/100")
        matched = match_context.get("matched_skills") or []
        if matched:
            parts.append(f"Matched skills: {', '.join(matched[:5])}")
        gaps = match_context.get("gaps") or []
        if gaps:
            parts.append(f"Gaps (do NOT overstate in letter): {', '.join(gaps[:3])}")
        signals = match_context.get("signals") or []
        if signals:
            parts.append(f"Signals: {', '.join(signals)}")
        role_type = match_context.get("vacancy_role_type")
        if role_type and role_type not in ("unknown", None):
            parts.append(f"Vacancy role type: {role_type}")
        if not parts:
            return ""
        body = "\n".join(f"- {p}" for p in parts)
        return (
            "\n\nSCORING CONTEXT (from pre-run analysis — use to write more precisely, "
            "do not copy these labels into the letter):\n" + body + "\n"
        )

    def _load_profile(self, filename: str) -> str:
        path = Path(CONFIG.data_dir) / filename
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def _load_prompt(self, filename: str) -> str:
        path = _PROMPTS_DIR / filename
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

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
