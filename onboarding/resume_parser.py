"""
Resume parser: PDF / DOCX / image / markdown → ResumeData → resume_facts.md

- PDF + images → base64 image_url → Gemini reads both natively (no local extraction)
- DOCX → python-docx text → LLM text mode (no image representation available)
- MD/TXT → LLM text mode
- json_repair as fallback for malformed LLM JSON output
- OpenRouter as unified gateway (RESUME_PARSE_MODEL / LLM_MODEL env vars)
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

    # Tier 2 — Structured work history (preferred over legacy flat fields)
    # jobs: [{company, title, period, domain, projects: [{name, context, results[]}]}]
    jobs: list = field(default_factory=list)
    # side_projects: [{name, context, results[]}]  — first-class, same schema
    side_projects: list = field(default_factory=list)

    # Tier 2 — Legacy flat fields (kept for wizard backward compat; to_md() ignores if jobs present)
    skills: list = field(default_factory=list)
    achievements: list = field(default_factory=list)
    key_cases: list = field(default_factory=list)

    # Tier 3 — Nice to have
    tools: list = field(default_factory=list)
    languages: dict = field(default_factory=dict)

    # Contacts & personal — used to answer HR form questions (LinkedIn, GitHub, etc.)
    contacts: dict = field(default_factory=dict)   # linkedin, github, telegram, email, phone
    personal: dict = field(default_factory=dict)   # age, location, relocation

    # Search
    suggested_queries: list = field(default_factory=list)

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
            # DOCX has no image representation — extract text, send as text
            return self._extract_with_llm(self._extract_docx_text(path), path.name)

        # PDF and images: always multimodal — Gemini reads layout/structure natively
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
            jobs=answers.get("jobs") or [],
            side_projects=answers.get("side_projects") or [],
            contacts=answers.get("contacts") or {},
            personal=answers.get("personal") or {},
            source_file="wizard",
            parsed_at=datetime.now().isoformat(),
        )
        return self._finalize(data)

    def to_md(self, data: ResumeData) -> str:
        """Serialize ResumeData → candidate.md (dense format optimized for LLM tokens)."""
        lines = [
            "# candidate.md",
            f"# completeness: {data.completeness:.0%} | source: {data.source_file} | updated: {data.parsed_at[:10]}",
            "",
            "## Identity",
            f"name: {data.name or 'MISSING — add your name'}",
            f"role: {data.role or 'MISSING — add your target role'}",
            f"experience_years: {data.experience_years if data.experience_years is not None else 'MISSING'}",
            f"current_company: {data.current_company or 'MISSING'}",
            f"domain: {data.domain or 'MISSING'}",
            "",
            "## Skills",
        ]

        if data.skills:
            for s in data.skills:
                lines.append(f"- {s}")
            if len(data.skills) < 5:
                lines.append("# HINT: add more specific skills to improve match quality")
        else:
            lines.append("# EMPTY — add professional skills (e.g. platform thinking, API design, SQL)")

        # ── Work Experience ───────────────────────────────────────────────────
        lines += ["", "## Work Experience"]
        if data.jobs:
            for job in data.jobs:
                company = job.get("company", "")
                title   = job.get("title", "")
                period  = job.get("period", "")
                domain  = job.get("domain", "")
                header_parts = [x for x in [company, title, period, domain] if x]
                lines.append(f"### {' | '.join(header_parts)}")
                for proj in job.get("projects", []):
                    lines.append(f"#### {proj.get('name', '')}")
                    ctx = proj.get("context", "")
                    if ctx:
                        lines.append(f"Context: {ctx}")
                    for r in proj.get("results", []):
                        lines.append(f"- {r}")
                lines.append("")
        elif data.achievements or data.key_cases:
            # Legacy fallback: flat achievements + key_cases from old wizard/parser
            lines.append("# (legacy format — re-run wizard or edit directly to add structured work history)")
            for a in data.achievements:
                lines.append(f"- {a}")
            if data.key_cases:
                lines.append("")
                for c in data.key_cases:
                    lines.append(f"- {c}")
        else:
            lines.append("# EMPTY — add work history via wizard or edit candidate.md directly")

        # ── Side Projects ─────────────────────────────────────────────────────
        lines += ["## Side Projects"]
        if data.side_projects:
            for sp in data.side_projects:
                lines.append(f"### {sp.get('name', '')}")
                ctx = sp.get("context", "")
                if ctx:
                    lines.append(f"Context: {ctx}")
                for r in sp.get("results", []):
                    lines.append(f"- {r}")
                lines.append("")
        else:
            lines.append("# EMPTY — add personal/side projects (pet projects, open source, etc.)")

        # ── Tools & Languages ─────────────────────────────────────────────────
        lines += ["", "## Tools & Languages"]
        if data.tools:
            lines.append(f"tools: {', '.join(data.tools)}")
        else:
            lines.append("tools:  # HINT: add tools you use (Jira, Figma, SQL, etc.)")
        for lang, level in (data.languages or {}).items():
            lines.append(f"{lang}: {level}")

        # ── Contacts & Personal ───────────────────────────────────────────────
        lines += ["", "## Contacts & Personal"]
        contacts = data.contacts or {}
        personal = data.personal or {}

        if personal.get("age"):
            lines.append(f"age: {personal['age']}")
        if personal.get("location"):
            lines.append(f"location: {personal['location']}")
        if personal.get("relocation"):
            lines.append(f"relocation: {personal['relocation']}")
        for key in ("linkedin", "github", "telegram", "email", "phone"):
            val = contacts.get(key)
            if val:
                lines.append(f"{key}: {val}")

        has_contacts = any(contacts.get(k) for k in ("linkedin", "github", "telegram", "email", "phone"))
        has_personal = any(personal.get(k) for k in ("age", "location", "relocation"))
        if not has_contacts and not has_personal:
            lines.append("# HINT: add LinkedIn, GitHub, Telegram — used to answer HR form questions")

        # ── Completeness hints ────────────────────────────────────────────────
        if data.hints:
            lines += ["", "## Completeness Hints"]
            for hint in data.hints:
                lines.append(f"# - {hint}")

        return "\n".join(lines)

    # ── Extraction methods ────────────────────────────────────────────────────

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
            max_tokens=1800,
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
            max_tokens=1800,
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
            '  "jobs": [\n'
            '    {\n'
            '      "company": "Company name",\n'
            '      "title": "Job title",\n'
            '      "period": "2022–2024",\n'
            '      "domain": "industry domain",\n'
            '      "projects": [\n'
            '        {\n'
            '          "name": "Exact product/project name verbatim from CV",\n'
            '          "context": "What it was, who used it, the business problem solved (1-2 sentences)",\n'
            '          "results": ["quantified metric tied directly to this project"]\n'
            '        }\n'
            '      ]\n'
            '    }\n'
            '  ],\n'
            '  "side_projects": [\n'
            '    {\n'
            '      "name": "Project name",\n'
            '      "context": "What it does, tech stack if relevant",\n'
            '      "results": ["outcome or current status"]\n'
            '    }\n'
            '  ],\n'
            '  "contacts": {\n'
            '    "linkedin": "URL or null",\n'
            '    "github": "URL or null",\n'
            '    "telegram": "@handle or null",\n'
            '    "email": "email or null",\n'
            '    "phone": "phone or null"\n'
            '  },\n'
            '  "personal": {\n'
            '    "age": integer or null,\n'
            '    "location": "city or null",\n'
            '    "relocation": "yes/no/open or null"\n'
            '  },\n'
            '  "tools": ["tool1", "tool2"],\n'
            '  "languages": {"english": "B2", "russian": "native"},\n'
            '  "suggested_queries": ["product manager b2b", "руководитель продукта"]\n'
            "}\n\n"
            "Rules:\n"
            "- jobs[].projects[].name: preserve the exact product/project name verbatim from the CV\n"
            "- jobs[].projects[].context: explain what the product is in 1-2 sentences — include domain, audience, key technical/business context\n"
            "- jobs[].projects[].results: ONLY quantified results directly tied to THIS specific project (not global career stats)\n"
            "- skills: professional skills only — NO metrics (AOV, CAC, TTR are metrics, not skills)\n"
            "- If a field is absent in the CV, use null or empty array/object\n"
            "- Do NOT invent or assume anything not explicitly present\n"
            "- suggested_queries: 2-3 Russian-language HH.ru search queries matching this candidate's role; use terms job seekers actually type on hh.ru"
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
            jobs=parsed.get("jobs") or [],
            side_projects=parsed.get("side_projects") or [],
            contacts=parsed.get("contacts") or {},
            personal=parsed.get("personal") or {},
            suggested_queries=parsed.get("suggested_queries") or [],
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
        t1 = sum(1 for f in tier1 if f is not None) / len(tier1) * 0.35

        has_work_history = bool(data.jobs) or bool(data.achievements)
        has_projects = (
            bool(data.key_cases)
            or any(proj for job in data.jobs for proj in job.get("projects", []))
            or bool(data.side_projects)
        )
        tier2 = [len(data.skills) >= 3, has_work_history, has_projects]
        t2 = sum(tier2) / len(tier2) * 0.40

        has_contact = any((data.contacts or {}).get(k) for k in ("linkedin", "github", "telegram", "email"))
        tier3 = [len(data.tools) >= 1, bool(data.languages), has_contact]
        t3 = sum(tier3) / len(tier3) * 0.25

        return round(t1 + t2 + t3, 2)

    def _build_hints(self, data: ResumeData) -> list:
        hints = []
        if not data.name:            hints.append("Add your full name")
        if not data.role:            hints.append("Add your target job title")
        if not data.current_company: hints.append("Add your current company name")
        if not data.domain:          hints.append("Add your industry/domain (e.g. fintech, e-commerce)")
        if len(data.skills) < 3:
            hints.append("Add at least 3 professional skills")
        if not data.jobs and not data.achievements:
            hints.append("Add work history — run wizard or edit candidate.md directly")
        if not any((data.contacts or {}).get(k) for k in ("linkedin", "github", "telegram")):
            hints.append("Add LinkedIn/GitHub/Telegram — helps LLM answer HR form contact questions")
        return hints
