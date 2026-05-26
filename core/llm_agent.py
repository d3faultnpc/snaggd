"""
LLM agent — single client for all AI calls (cover letter, scoring, HR questions).
Uses OpenRouter as gateway; model configured via LLM_MODEL env var.
"""

import json
import os
from pathlib import Path

from openai import OpenAI

from config import CONFIG

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

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

    def generate_cover(self, vacancy_text: str) -> str:
        prompt = self._load_prompt("cover_letter.md")
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=600,
            messages=[
                {"role": "system", "content": self._system()},
                {"role": "user", "content": f"{prompt}\n\nVACANCY:\n{vacancy_text[:3000]}"},
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
                {"role": "user", "content": f"{prompt}\n\nVACANCY:\n{vacancy_text[:3000]}"},
            ],
        )
        raw = (resp.choices[0].message.content or "{}").strip()
        return self._parse_json(raw, fallback={
            "score": 50,
            "matched_skills": [],
            "gaps": [],
            "signals": [],
            "stop_match": None,
        })

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
            .replace("{{VACANCY}}", vacancy_text[:2000])
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
