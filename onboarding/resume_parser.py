"""
Resume parser: PDF / DOCX / image / markdown → ResumeData → resume_facts.md

Pattern borrowed from Health-concierge/server/src/process-attachment.ts:
  - pdfminer extracts text from selectable PDFs (no LLM tokens)
  - multimodal image_url (base64) for scanned PDFs and images — Gemini reads both natively
  - LLM structures extracted text into JSON (json_repair as fallback)
  - OpenRouter as the unified gateway (RESUME_PARSE_MODEL env var)
"""

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from json_repair import repair_json
    _HAS_JSON_REPAIR = True
except ImportError:
    _HAS_JSON_REPAIR = False


@dataclass
class ResumeData:
    # Tier 1 — Critical: cover generation degrades without these
    name: Optional[str] = None
    role: Optional[str] = None
    experience_years: Optional[int] = None
    current_company: Optional[str] = None
    domain: Optional[str] = None

    # Tier 2 — Important: enrichable, directly affect match score
    skills: list = field(default_factory=list)
    achievements: list = field(default_factory=list)
    key_cases: list = field(default_factory=list)

    # Tier 3 — Nice to have
    tools: list = field(default_factory=list)
    languages: dict = field(default_factory=dict)

    # Metadata
    source_file: str = ""
    parsed_at: str = ""
    completeness: float = 0.0
    hints: list = field(default_factory=list)


class ResumeParser:
    SUPPORTED_TYPES = {
        ".pdf":  "application/pdf",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".md":   "text/markdown",
        ".txt":  "text/plain",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    # Gemini Flash reads PDF and images natively via image_url — same as Health-concierge
    MULTIMODAL_MODEL = os.getenv("RESUME_PARSE_MODEL", "google/gemini-2.0-flash-001")
    TEXT_MODEL       = os.getenv("LLM_MODEL", "anthropic/claude-3-5-haiku")

    def __init__(self, llm_client):
        self.llm = llm_client

    # ── Public API ────────────────────────────────────────────────────────────

    def parse_file(self, path: Path) -> ResumeData:
        path = Path(path)
        ext = path.suffix.lower()
        if ext not in self.SUPPORTED_TYPES:
            raise ValueError(f"Unsupported file type: {ext}. Supported: {list(self.SUPPORTED_TYPES)}")

        mime = self.SUPPORTED_TYPES[ext]

        if mime in ("text/markdown", "text/plain"):
            return self._extract_with_llm(path.read_text(encoding="utf-8"), path.name)

        if ext == ".docx":
            return self._extract_with_llm(self._extract_docx_text(path), path.name)

        if mime == "application/pdf":
            raw = self._extract_pdf_text(path)
            if len(raw.strip()) > 200:
                return self._extract_with_llm(raw, path.name)
            # Scanned PDF — fall through to multimodal

        return self._extract_multimodal(path, mime)

    def from_wizard(self, answers: dict) -> ResumeData:
        data = ResumeData(
            name=answers.get("name"),
            role=answers.get("role"),
            experience_years=answers.get("experience_years"),
            current_company=answers.get("current_company"),
            domain=answers.get("domain"),
            skills=answers.get("skills") or [],
            achievements=answers.get("achievements") or [],
            key_cases=answers.get("key_cases") or [],
            tools=answers.get("tools") or [],
            languages=answers.get("languages") or {},
            source_file="wizard",
            parsed_at=datetime.now().isoformat(),
        )
        return self._finalize(data)

    def to_md(self, data: ResumeData) -> str:
        """Serialize ResumeData → resume_facts.md (dense format optimized for LLM tokens)."""
        lines = [
            "# resume_facts.md",
            f"# completeness: {data.completeness:.0%} | source: {data.source_file} | updated: {data.parsed_at[:10]}",
            "",
            "# --- IDENTITY ---",
            f"name: {data.name or 'MISSING — add your name'}",
            f"role: {data.role or 'MISSING — add your target role'}",
            f"experience_years: {data.experience_years if data.experience_years is not None else 'MISSING'}",
            f"current_company: {data.current_company or 'MISSING'}",
            f"domain: {data.domain or 'MISSING'}",
            "",
            "# --- SKILLS ---",
        ]

        if data.skills:
            lines.append("skills:")
            for s in data.skills:
                lines.append(f"  - {s}")
            if len(data.skills) < 5:
                lines.append("  # HINT: add more specific skills to improve match quality")
        else:
            lines += [
                "skills:",
                "  # EMPTY — add professional skills (e.g. platform thinking, API design, SQL)",
            ]

        lines += ["", "# --- ACHIEVEMENTS ---"]
        if data.achievements:
            lines.append("achievements:")
            for a in data.achievements:
                lines.append(f"  - {a}")
        else:
            lines += [
                "achievements:",
                "  # EMPTY — add 2-3 quantified results to significantly boost match score",
                '  # Format: "verb + metric + context" (e.g. "Launched X, increased Y by Z%")',
            ]

        lines += ["", "# --- KEY CASES ---"]
        if data.key_cases:
            lines.append("key_cases:")
            for c in data.key_cases:
                lines.append(f"  - {c}")
        else:
            lines += [
                "key_cases:",
                "  # EMPTY — add 1-2 main projects or products you worked on",
            ]

        lines += ["", "# --- TOOLS & LANGUAGES ---"]
        if data.tools:
            lines.append(f"tools: [{', '.join(data.tools)}]")
        else:
            lines.append("tools: []  # HINT: add tools you use (Jira, Figma, SQL, etc.)")

        for lang, level in (data.languages or {}).items():
            lines.append(f"{lang}: {level}")

        if data.hints:
            lines += ["", "# --- COMPLETENESS HINTS ---"]
            for hint in data.hints:
                lines.append(f"# - {hint}")

        return "\n".join(lines)

    # ── Extraction methods ────────────────────────────────────────────────────

    def _extract_pdf_text(self, path: Path) -> str:
        try:
            import pdfminer.high_level
            return pdfminer.high_level.extract_text(str(path)) or ""
        except Exception:
            return ""

    def _extract_docx_text(self, path: Path) -> str:
        try:
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            return ""

    def _extract_multimodal(self, path: Path, mime: str) -> ResumeData:
        """Send file as base64 image_url — works for images and scanned PDFs (Gemini)."""
        b64 = base64.b64encode(path.read_bytes()).decode()
        response = self.llm.chat.completions.create(
            model=self.MULTIMODAL_MODEL,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": self._extraction_prompt()},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
        )
        raw = response.choices[0].message.content or "{}"
        return self._parse_json_response(raw, source_file=path.name)

    def _extract_with_llm(self, text: str, source_file: str) -> ResumeData:
        response = self.llm.chat.completions.create(
            model=self.TEXT_MODEL,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": f"{self._extraction_prompt()}\n\nCV text:\n{text[:6000]}",
            }],
        )
        raw = response.choices[0].message.content or "{}"
        return self._parse_json_response(raw, source_file=source_file)

    # ── Prompt & parsing ──────────────────────────────────────────────────────

    def _extraction_prompt(self) -> str:
        return (
            "Extract structured information from this CV/resume.\n\n"
            "Return ONLY valid JSON, no markdown, no wrapper:\n"
            "{\n"
            '  "name": "Full name or null",\n'
            '  "role": "Current/target job title or null",\n'
            '  "experience_years": integer or null,\n'
            '  "current_company": "Company name or null",\n'
            '  "domain": "Industry/domain (e.g. fintech, e-commerce) or null",\n'
            '  "skills": ["skill1", "skill2"],\n'
            '  "achievements": ["quantified result 1"],\n'
            '  "key_cases": ["project or case description"],\n'
            '  "tools": ["tool1", "tool2"],\n'
            '  "languages": {"english": "B2", "russian": "native"}\n'
            "}\n\n"
            "Rules:\n"
            "- achievements: ONLY quantified results with metrics/numbers\n"
            "- skills: professional skills only, no soft skills\n"
            "- If a field is absent in the CV, use null or empty array\n"
            "- Do NOT invent or assume anything not explicitly present"
        )

    def _parse_json_response(self, raw: str, source_file: str) -> ResumeData:
        raw = raw.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            if _HAS_JSON_REPAIR:
                try:
                    parsed = json.loads(repair_json(raw))
                except Exception:
                    pass

        data = ResumeData(
            name=parsed.get("name"),
            role=parsed.get("role"),
            experience_years=parsed.get("experience_years"),
            current_company=parsed.get("current_company"),
            domain=parsed.get("domain"),
            skills=parsed.get("skills") or [],
            achievements=parsed.get("achievements") or [],
            key_cases=parsed.get("key_cases") or [],
            tools=parsed.get("tools") or [],
            languages=parsed.get("languages") or {},
            source_file=source_file,
            parsed_at=datetime.now().isoformat(),
        )
        return self._finalize(data)

    # ── Completeness & hints ──────────────────────────────────────────────────

    def _finalize(self, data: ResumeData) -> ResumeData:
        data.completeness = self._completeness_score(data)
        data.hints = self._build_hints(data)
        return data

    def _completeness_score(self, data: ResumeData) -> float:
        tier1 = [data.name, data.role, data.experience_years, data.current_company, data.domain]
        t1 = sum(1 for f in tier1 if f is not None) / len(tier1) * 0.40

        tier2 = [len(data.skills) >= 3, len(data.achievements) >= 1, len(data.key_cases) >= 1]
        t2 = sum(tier2) / len(tier2) * 0.40

        tier3 = [len(data.tools) >= 1, bool(data.languages)]
        t3 = sum(tier3) / len(tier3) * 0.20

        return round(t1 + t2 + t3, 2)

    def _build_hints(self, data: ResumeData) -> list:
        hints = []
        if not data.name:            hints.append("Add your full name")
        if not data.role:            hints.append("Add your target job title")
        if not data.current_company: hints.append("Add your current company name")
        if not data.domain:          hints.append("Add your industry/domain (e.g. fintech, e-commerce)")
        if len(data.skills) < 3:
            hints.append("Add at least 3 professional skills")
        if not data.achievements:
            hints.append("Add 2-3 quantified achievements — significantly boosts match score")
        elif len(data.achievements) < 2:
            hints.append("Add 1-2 more quantified achievements to strengthen your profile")
        if not data.key_cases:
            hints.append("Add 1-2 key projects or products you've worked on")
        return hints
